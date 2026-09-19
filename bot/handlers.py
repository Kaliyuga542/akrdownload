import os
from telegram import Update
from telegram.ext import ContextTypes

from config import validate_config

from bot.cookie_manager import (
    save_cookie_file,
    basic_cookie_check,
    delete_cookie_file,
)


async def start_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    validate_config()

    await update.message.reply_text(
        "🎬 HLS Video Bot\n\n"
        "Send your authorized .m3u8 URL.\n\n"
        "The bot will show available:\n"
        "🎥 Video qualities\n"
        "🔊 Audio tracks\n"
        "💬 Subtitle tracks"
    )


async def help_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "📖 Help\n\n"
        "1️⃣ Send an authorized .m3u8 URL\n"
        "2️⃣ Select video quality\n"
        "3️⃣ Select audio\n"
        "4️⃣ Select or upload subtitle\n"
        "5️⃣ Press Continue\n"
        "6️⃣ Bot creates the MP4\n\n"
        "Only authorized/unprotected streams are supported."
    )


async def cancel_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    # Remove current user session if your main.py
    # exposes the sessions dictionary.
    try:
        from main import sessions

        if user_id in sessions:
            sessions.pop(user_id, None)

    except Exception:
        pass

    await update.message.reply_text(
        "❌ Current operation cancelled."
    )


async def unknown_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "❓ Unknown command.\n\n"
        "Send an authorized .m3u8 URL or use /help."
    )

async def receive_cookie_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id
    document = update.message.document

    try:
        path = await save_cookie_file(
            document,
            user_id,
            context.bot
        )

        if not basic_cookie_check(path):
            delete_cookie_file(user_id)

            await update.message.reply_text(
                "❌ Invalid cookie file."
            )
            return

        await update.message.reply_text(
            "✅ Cookie file received.\n\n"
            "🔐 Authorized session validation required."
        )

    except ValueError as e:
        await update.message.reply_text(
            f"❌ {e}"
        )

    except Exception as e:
        print(f"Cookie upload error: {e}")

        await update.message.reply_text(
            "❌ Cookie file process failed."
        )
