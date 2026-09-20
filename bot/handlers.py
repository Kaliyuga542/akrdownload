from telegram import Update
from telegram.ext import ContextTypes

from bot.cookie_manager import (
    save_cookie_file,
    basic_cookie_check,
    delete_cookie_file,
)


# =========================================================
# COOKIE FILE UPLOAD HANDLER
# =========================================================

async def receive_cookie_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:

        return

    user_id = update.effective_user.id

    document = update.message.document

    try:

        # ---------------------------------------------
        # OLD COOKIE DELETE (OVERWRITE FIX)
        # ---------------------------------------------

        delete_cookie_file(user_id)

        path = await save_cookie_file(
            document,
            user_id,
            context.bot
        )

        if not basic_cookie_check(path):

            delete_cookie_file(user_id)

            await update.message.reply_text(
                "❌ Invalid cookie file.\n\n"
                "Please export cookies in Netscape "
                "format (or JSON) and try again."
            )

            return

        await update.message.reply_text(
            "✅ Cookie file received and saved.\n\n"
            "🔗 Now send your .m3u8 or .mpd URL "
            "again to start the download."
        )

    except ValueError as e:

        await update.message.reply_text(
            f"❌ {e}"
        )

    except Exception as e:

        print(
            f"Cookie upload error: {e}"
        )

        await update.message.reply_text(
            "❌ Cookie file process failed. "
            "Please try again."
        )
