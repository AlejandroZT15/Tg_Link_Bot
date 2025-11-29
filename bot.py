#!/usr/bin/env python3
# Requiere: python-telegram-bot >=20 (async API)
# Instalar: pip install python-telegram-bot==20.6

import json
import logging
import os
import re
from typing import Dict, Any, List

from telegram import Update, constants
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")  # pon aquí tu token o usa variable de entorno
DATA_FILE = "data.json"
# ----------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        raise FileNotFoundError(f"{DATA_FILE} not found. Create it from the template.")
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: Dict[str, Any]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def format_index(data: Dict[str, Any]) -> str:
    lines = ["📚 <b>ÍNDICE</b>\n"]
    for cat, info in data["categorias"].items():
        count = len(info.get("links", []))
        # If message_id exists and channel_username is set, build a t.me link to jump
        jump = ""
        if data.get("channel_username") and info.get("message_id"):
            jump = f" — <a href=\"https://t.me/{data['channel_username'].lstrip('@')}/{info['message_id']}\">ir</a>"
        lines.append(f"• <b>{cat}</b> ({count}){jump}")
    return "\n".join(lines)


def format_category_message(cat_name: str, links: List[Dict[str, str]]) -> str:
    header = f"📎 <b>{cat_name.upper()}</b> ({len(links)} enlaces)\n\n"
    if not links:
        return header + "_No hay enlaces aún. Agrega alguno con_ /add"
    lines = []
    for i, item in enumerate(links, start=1):
        title = item.get("texto") or item.get("url")
        url = item.get("url")
        # Use markdown (HTML escape is handled by telegram when parse_mode is HTML)
        lines.append(f"{i}. <a href=\"{url}\">{title}</a>")
    return header + "\n".join(lines)


async def ensure_channel_messages(app):
    """
    If any message_id in data.json is null, create the messages in the channel
    and store their message_ids. This runs at startup.
    """
    data = load_data()
    channel = data.get("channel_username")
    if not channel:
        logger.warning("channel_username is null in data.json. Skipping auto-initialization.")
        return

    chat_id = channel  # e.g. '@mi_canal'
    modified = False

    # Ensure index exists first
    if not data.get("indice_message_id"):
        text = format_index(data)
        msg = await app.bot.send_message(chat_id=chat_id, text=text, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=True)
        data["indice_message_id"] = msg.message_id
        logger.info(f"Created index message id={msg.message_id}")
        modified = True

    # Ensure each category message exists
    for cat, info in data["categorias"].items():
        if not info.get("message_id"):
            text = format_category_message(cat, info.get("links", []))
            msg = await app.bot.send_message(chat_id=chat_id, text=text, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=False)
            data["categorias"][cat]["message_id"] = msg.message_id
            logger.info(f"Created message for category '{cat}' id={msg.message_id}")
            modified = True

    if modified:
        save_data(data)
        logger.info("data.json updated with new message ids.")


# -------------------- Handlers --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola — soy el bot admin del canal. Usa /add <Categoría> <Título> <URL> para agregar recursos.\nEjemplo:\n/add Diseño Cómo vectorizar una imagen https://example.com"
    )


def parse_add_args(text: str):
    # Remove leading command if present
    # Expect: /add <category> <title...> <url>
    # We'll try to find the last token that looks like a URL
    parts = text.strip().split()
    # remove command
    if parts and parts[0].startswith("/add"):
        parts = parts[1:]
    if not parts:
        return None, None, None
    # Find last URL-like token
    url_idx = None
    url_pattern = re.compile(r"^https?://\S+$")
    for i in range(len(parts) - 1, -1, -1):
        if url_pattern.match(parts[i]):
            url_idx = i
            break
    if url_idx is None:
        return None, None, None
    url = parts[url_idx]
    # category is first token
    category = parts[0]
    # title is everything between 1..url_idx-1
    title = " ".join(parts[1:url_idx]) if url_idx > 1 else ""
    return category, title, url


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    raw = update.message.text
    category, title, url = parse_add_args(raw)
    if not category or not url:
        await update.message.reply_text(
            "Uso inválido. Formato correcto:\n/add <Categoría> <Título opcional> <URL>\nEjemplo:\n/add Diseño Vectorizar imagen https://example.com"
        )
        return

    data = load_data()
    # match category case-insensitively
    matches = [c for c in data["categorias"].keys() if c.lower() == category.lower()]
    if not matches:
        await update.message.reply_text(f"Categoría '{category}' no encontrada. Usa /list para ver categorías disponibles.")
        return
    cat_key = matches[0]

    entry = {"texto": title or url, "url": url, "autor": user.username or user.full_name}
    data["categorias"][cat_key].setdefault("links", []).append(entry)
    save_data(data)

    # Update the category message and index in the channel (if channel configured)
    channel = data.get("channel_username")
    if channel:
        chat_id = channel
        # update category message
        cat_msg_id = data["categorias"][cat_key]["message_id"]
        new_text = format_category_message(cat_key, data["categorias"][cat_key]["links"])
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=cat_msg_id, text=new_text, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=False)
        except Exception as e:
            logger.error("Failed to edit category message: %s", e)
        # update index
        idx_id = data.get("indice_message_id")
        if idx_id:
            idx_text = format_index(data)
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=idx_id, text=idx_text, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=True)
            except Exception as e:
                logger.error("Failed to edit index message: %s", e)

    await update.message.reply_text(f"Enlace agregado a <b>{cat_key}</b> ✅", parse_mode=constants.ParseMode.HTML)


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    lines = ["📚 <b>Categorías disponibles:</b>\n"]
    for cat, info in data["categorias"].items():
        lines.append(f"• <b>{cat}</b> — {len(info.get('links', []))} enlaces")
    text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode=constants.ParseMode.HTML)


async def refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Regenera todos los mensajes del canal (útil si algo salió mal).
    """
    data = load_data()
    channel = data.get("channel_username")
    if not channel:
        await update.message.reply_text("channel_username no está configurado en data.json. Edita el archivo y pon el @username del canal.")
        return
    chat_id = channel
    # Rebuild category messages
    for cat, info in data["categorias"].items():
        msg_id = info.get("message_id")
        text = format_category_message(cat, info.get("links", []))
        try:
            if msg_id:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=False)
            else:
                msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=False)
                data["categorias"][cat]["message_id"] = msg.message_id
        except Exception as e:
            logger.error("Error refreshing category %s: %s", cat, e)

    # Rebuild index
    idx_id = data.get("indice_message_id")
    idx_text = format_index(data)
    try:
        if idx_id:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=idx_id, text=idx_text, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=True)
        else:
            msg = await context.bot.send_message(chat_id=chat_id, text=idx_text, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=True)
            data["indice_message_id"] = msg.message_id
    except Exception as e:
        logger.error("Error refreshing index: %s", e)

    save_data(data)
    await update.message.reply_text("Canal regenerado ✅")


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    channel = data.get("channel_username") or "No configurado (usa data.json)"
    total = sum(len(info.get("links", [])) for info in data["categorias"].values())
    await update.message.reply_text(f"Canal: {channel}\nCategorías: {len(data['categorias'])}\nEnlaces totales: {total}")


# Optional: allow users to send "url only" messages to the bot; we can ask for category later.
async def echo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    # Quick URL detection
    if text.startswith("http://") or text.startswith("https://"):
        await update.message.reply_text("Si quieres agregar este enlace, usa:\n/add <Categoría> <Título opcional> <URL>\nEjemplo: /add Videos Video útil " + text)
    else:
        await update.message.reply_text("Comando no reconocido. Usa /add para agregar enlaces o /list para ver categorías.")


# -------------------- Bootstrap --------------------

def main():
    if not BOT_TOKEN:
        logger.error("TG_BOT_TOKEN no configurado. Define la variable de entorno TG_BOT_TOKEN o edita BOT_TOKEN en el script.")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("refresh", refresh_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_handler))

    # On startup: create messages if needed (only works if channel_username is set)
    async def on_startup(app):
        logger.info("Aplicación iniciada — verificando canal...")
        await ensure_channel_messages(app)

    app.post_init = on_startup

    logger.info("Bot arrancando...")
    app.run_polling()


if __name__ == "__main__":
    main()
