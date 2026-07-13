# -*- coding: utf-8 -*-
"""
SMM Ssenariy Bot
================
Ovozli xabarni eshitadi -> matnga o'giradi (Groq Whisper) ->
loyiha stiliga mos ssenariy yozadi (Groq Llama) -> text formatda qaytaradi.

Ishlashi uchun 2 ta bepul kalit kerak:
  1) TELEGRAM_BOT_TOKEN  — @BotFather dan olinadi
  2) GROQ_API_KEY        — https://console.groq.com dan bepul olinadi

Ularni .env fayliga yozing (README.md ga qarang).
"""

import os
import asyncio
import logging
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from projects import PROJECTS, DEFAULT_PROJECT

# ------------------------------------------------------------------ sozlamalar
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Groq modellari (bepul)
STT_MODEL = "whisper-large-v3"            # ovozni matnga o'girish (o'zbek tilini yaxshi tushunadi)
CHAT_MODEL = "llama-3.3-70b-versatile"    # ssenariy yozish

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if not TELEGRAM_BOT_TOKEN:
    raise SystemExit("XATO: .env faylida TELEGRAM_BOT_TOKEN ko'rsatilmagan.")
if not GROQ_API_KEY:
    raise SystemExit("XATO: .env faylida GROQ_API_KEY ko'rsatilmagan.")

groq_client = Groq(api_key=GROQ_API_KEY)

# Foydalanuvchi -> tanlangan loyiha (xotirada saqlanadi)
user_project: dict[int, str] = {}


# ------------------------------------------------------------------ yordamchilar
def project_keyboard() -> InlineKeyboardMarkup:
    """Loyiha tanlash tugmalari."""
    buttons = [
        [InlineKeyboardButton(f"{p['emoji']} {p['name']}", callback_data=f"proj:{key}")]
        for key, p in PROJECTS.items()
    ]
    return InlineKeyboardMarkup(buttons)


def get_project(user_id: int) -> dict:
    key = user_project.get(user_id, DEFAULT_PROJECT)
    return PROJECTS.get(key, PROJECTS[DEFAULT_PROJECT])


def transcribe(audio_path: str) -> str:
    """Ovoz faylini matnga o'giradi (o'zbekcha)."""
    with open(audio_path, "rb") as f:
        result = groq_client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f.read()),
            model=STT_MODEL,
            language="uz",          # o'zbek tili
            response_format="text",
        )
    # response_format="text" bo'lsa result to'g'ridan-to'g'ri string bo'ladi
    return result if isinstance(result, str) else getattr(result, "text", str(result))


def write_scenario(idea_text: str, project: dict) -> str:
    """Loyiha stiliga mos ssenariy yozadi."""
    completion = groq_client.chat.completions.create(
        model=CHAT_MODEL,
        temperature=0.8,
        max_tokens=2000,
        messages=[
            {"role": "system", "content": project["system_prompt"]},
            {
                "role": "user",
                "content": (
                    "Quyida men ovozli xabarda tushuntirgan g'oya (matnga o'girilgan). "
                    "Shuni tushunib, odamdek fikrlab, loyiha stiliga mos to'liq ssenariy yozib ber:\n\n"
                    f"«{idea_text}»"
                ),
            },
        ],
    )
    return completion.choices[0].message.content


# ------------------------------------------------------------------ handlerlar
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_project.setdefault(user_id, DEFAULT_PROJECT)
    cur = get_project(user_id)
    await update.message.reply_text(
        "Assalomu alaykum! 👋\n\n"
        "Men SMM ssenariy botiman. Sen menga *ovozli xabar* tashlaysan — "
        "qanaqa ssenariy kerakligini o'z tilingda tushuntirasan. "
        "Men eshitib, tushunib, loyiha stiliga mos *tayyor ssenariy* yozib beraman.\n\n"
        f"Hozirgi loyiha: *{cur['emoji']} {cur['name']}*\n"
        f"_{cur['short']}_\n\n"
        "Boshqa loyihaga o'tish: /loyiha\n\n"
        "Endi ovozli xabaringni tashla 🎙 (yoki oddiy matn ham yozsang bo'ladi).",
        parse_mode="Markdown",
    )


async def choose_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Qaysi loyiha uchun ssenariy yozamiz? 👇",
        reply_markup=project_keyboard(),
    )


async def project_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    key = query.data.split(":", 1)[1]
    if key in PROJECTS:
        user_project[query.from_user.id] = key
        p = PROJECTS[key]
        await query.edit_message_text(
            f"✅ Tanlandi: *{p['emoji']} {p['name']}*\n_{p['short']}_\n\n"
            "Endi ovozli xabaringni tashla 🎙",
            parse_mode="Markdown",
        )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ovozli xabar (voice yoki audio)ni qayta ishlaydi."""
    user_id = update.effective_user.id
    project = get_project(user_id)

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    status_msg = await update.message.reply_text("🎧 Ovozni eshityapman...")

    # 1) Faylni yuklab olamiz
    voice = update.message.voice or update.message.audio
    tg_file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as tmp:
        audio_path = tmp.name
    await tg_file.download_to_drive(audio_path)

    try:
        # 2) Matnga o'giramiz (bloklovchi so'rov alohida oqimda)
        idea = (await asyncio.to_thread(transcribe, audio_path)).strip()
        if not idea:
            await status_msg.edit_text("Ovozni tushuna olmadim 😕 Iltimos, qaytadan yuboring.")
            return

        await status_msg.edit_text(
            f"📝 Tushundim:\n_{idea}_\n\n✍️ Ssenariy yozyapman...",
            parse_mode="Markdown",
        )

        # 3) Ssenariy yozamiz (bloklovchi so'rov alohida oqimda)
        scenario = await asyncio.to_thread(write_scenario, idea, project)

        await status_msg.delete()
        await send_long(update, f"🎬 *{project['name']} — ssenariy*\n\n{scenario}")

    except Exception as e:
        logger.exception("Xatolik")
        await status_msg.edit_text(f"Xatolik yuz berdi: {e}")
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Matnli g'oyani ham qabul qiladi (ovoz o'rniga)."""
    user_id = update.effective_user.id
    project = get_project(user_id)
    idea = update.message.text.strip()

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    status_msg = await update.message.reply_text("✍️ Ssenariy yozyapman...")
    try:
        scenario = await asyncio.to_thread(write_scenario, idea, project)
        await status_msg.delete()
        await send_long(update, f"🎬 *{project['name']} — ssenariy*\n\n{scenario}")
    except Exception as e:
        logger.exception("Xatolik")
        await status_msg.edit_text(f"Xatolik yuz berdi: {e}")


async def send_long(update: Update, text: str) -> None:
    """Telegram limitidan (4096) uzun matnni bo'lib yuboradi."""
    limit = 4000
    for i in range(0, len(text), limit):
        await update.message.reply_text(text[i:i + limit], parse_mode="Markdown")


# ------------------------------------------------------------------ keep-alive
# Bulutli serverlar (Koyeb, Render va h.k.) ochiq port talab qiladi.
# PORT muhit o'zgaruvchisi bo'lsa, kichik HTTP server ishga tushiramiz.
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot ishlayapti")

    def log_message(self, *args):
        pass  # loglarni bosmaymiz


def start_keepalive_server() -> None:
    port = os.getenv("PORT")
    if not port:
        return  # lokalda kerak emas
    server = HTTPServer(("0.0.0.0", int(port)), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info(f"Keep-alive server {port}-portda ishga tushdi")


# ------------------------------------------------------------------ main
def main() -> None:
    start_keepalive_server()
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("loyiha", choose_project))
    app.add_handler(CallbackQueryHandler(project_selected, pattern=r"^proj:"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot ishga tushdi. To'xtatish: Ctrl+C")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
