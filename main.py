import asyncio
import shutil
import tempfile
import subprocess
from pathlib import Path
from urllib.parse import urljoin

import aiohttp

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from config import (
    BOT_TOKEN,
    MAX_TELEGRAM_SIZE,
    validate_config,
)

from bot.hls import analyse
from bot.uploader import upload_to_gofile
from bot.processor import merge_video_audio


# =========================================================
# USER SESSIONS
# =========================================================

sessions = {}

# Maximum number of simultaneous FFmpeg jobs
processing_semaphore = asyncio.Semaphore(2)


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    validate_config()

    await update.message.reply_text(
        "🎬 HLS → MP4 Bot\n\n"
        "Send an authorized .m3u8 URL.\n\n"
        "I will show:\n"
        "🎥 Video quality\n"
        "🔊 Audio quality\n"
        "💬 Subtitle tracks\n\n"
        "Then press ▶️ Continue."
    )


# =========================================================
# HELP
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "📖 How to use\n\n"
        "1️⃣ Send an authorized .m3u8 URL\n"
        "2️⃣ Select video quality\n"
        "3️⃣ Select audio\n"
        "4️⃣ Select subtitle or upload .srt/.vtt\n"
        "5️⃣ Press Continue\n"
        "6️⃣ Bot creates the MP4\n\n"
        "📦 File routing:\n"
        "≤ 1.94 GB → Telegram\n"
        "> 1.94 GB → GoFile"
    )


# =========================================================
# CANCEL
# =========================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    session = sessions.pop(user_id, None)

    if session:

        workdir = session.get("workdir")

        if workdir:
            shutil.rmtree(
                workdir,
                ignore_errors=True
            )

    await update.message.reply_text(
        "❌ Operation cancelled."
    )


# =========================================================
# DOWNLOAD HELPER
# =========================================================

async def download_file(
    url: str,
    destination: Path
):
    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=30
    )

    # HLS .m3u8 -> FFmpeg directly downloads
    # the HLS segments and writes the media file.
    if ".m3u8" in url.lower():

        command = [
            "ffmpeg",
            "-y",
            "-i",
            url,
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            str(destination)
        ]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(
                "FFmpeg HLS download failed:\n"
                + stderr.decode(errors="ignore")[-4000:]
            )

        if not destination.exists():
            raise RuntimeError(
                "FFmpeg did not create the output file."
            )

        if destination.stat().st_size == 0:
            raise RuntimeError(
                "Downloaded media file is empty."
            )

        return

    # Normal files such as .srt / .vtt
    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(url) as response:

            response.raise_for_status()

            with open(destination, "wb") as file:

                async for chunk in response.content.iter_chunked(
                    1024 * 1024
                ):
                    file.write(chunk)


# =========================================================
# RECEIVE M3U8
# =========================================================

async def receive_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    url = update.message.text.strip()

    if ".m3u8" not in url.lower():

        await update.message.reply_text(
            "❌ Please send a valid .m3u8 URL."
        )
        return

    user_id = update.effective_user.id

    # Remove old session
    old = sessions.pop(user_id, None)

    if old and old.get("workdir"):

        shutil.rmtree(
            old["workdir"],
            ignore_errors=True
        )

    status = await update.message.reply_text(
        "🔍 Analysing HLS playlist..."
    )

    try:

        videos, audios, subtitles = await analyse(url)

        if not videos:

            await status.edit_text(
                "❌ No selectable video qualities found."
            )
            return

        sessions[user_id] = {
            "url": url,
            "videos": videos,
            "audios": audios,
            "subtitles": subtitles,

            "video": None,
            "audio": None,
            "subtitle": None,

            "uploaded_subtitle": None,

            "workdir": None,
        }

        buttons = []

        for video in videos:

            resolution = video.get("resolution")

            if resolution:

                label = (
                    f"{resolution[0]}x"
                    f"{resolution[1]}"
                )

            else:

                bandwidth = video.get(
                    "bandwidth",
                    0
                )

                if bandwidth:
                    label = (
                        f"{bandwidth // 1000} kbps"
                    )
                else:
                    label = "Unknown"

            buttons.append([
                __import__(
                    "telegram"
                ).InlineKeyboardButton(
                    f"🎥 {label}",
                    callback_data=f"video:{video['id']}"
                )
            ])

        await status.edit_text(
            "🎥 Select video quality:",
            reply_markup=__import__(
                "telegram"
            ).InlineKeyboardMarkup(buttons)
        )

    except Exception as error:

        await status.edit_text(
            "❌ HLS analysis failed.\n\n"
            f"{str(error)[:3000]}"
        )


# =========================================================
# VIDEO / AUDIO / SUBTITLE CALLBACKS
# =========================================================

async def callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    session = sessions.get(user_id)

    if not session:

        await query.edit_message_text(
            "❌ Session expired.\n"
            "Please send the .m3u8 URL again."
        )
        return

    data = query.data

    # -----------------------------------------------------
    # VIDEO
    # -----------------------------------------------------

    if data.startswith("video:"):

        index = int(
            data.split(":", 1)[1]
        )

        session["video"] = session[
            "videos"
        ][index]

        audios = session["audios"]

        # No audio tracks
        if not audios:

            await show_subtitles(
                query,
                session
            )

            return

        buttons = []

        for audio in audios:

            name = audio.get(
                "name"
            ) or "Audio"

            language = audio.get(
                "language"
            )

            if language:
                name += f" ({language})"

            buttons.append([
                __import__(
                    "telegram"
                ).InlineKeyboardButton(
                    f"🔊 {name}",
                    callback_data=f"audio:{audio['id']}"
                )
            ])

        await query.edit_message_text(
            "🔊 Select audio:",
            reply_markup=__import__(
                "telegram"
            ).InlineKeyboardMarkup(buttons)
        )

        return

    # -----------------------------------------------------
    # AUDIO
    # -----------------------------------------------------

    if data.startswith("audio:"):

        index = int(
            data.split(":", 1)[1]
        )

        session["audio"] = session[
            "audios"
        ][index]

        await show_subtitles(
            query,
            session
        )

        return

    # -----------------------------------------------------
    # SUBTITLE
    # -----------------------------------------------------

    if data.startswith("subtitle:"):

        index = int(
            data.split(":", 1)[1]
        )

        session["subtitle"] = session[
            "subtitles"
        ][index]

        await show_continue(
            query
        )

        return

    # -----------------------------------------------------
    # SKIP SUBTITLE
    # -----------------------------------------------------

    if data == "subtitle_skip":

        session["subtitle"] = None

        await show_continue(
            query
        )

        return

    # -----------------------------------------------------
    # UPLOAD SUBTITLE
    # -----------------------------------------------------

    if data == "upload_info":

        await query.edit_message_text(
            "📤 Upload subtitle\n\n"
            "Please send your .srt or .vtt "
            "subtitle file here.\n\n"
            "After uploading it, you will get "
            "the ▶️ Continue button."
        )

        return

    # -----------------------------------------------------
    # CONTINUE
    # -----------------------------------------------------

    if data == "continue":

        if not session.get("video"):

            await query.edit_message_text(
                "❌ Please select video quality first."
            )
            return

        await query.edit_message_text(
            "⏳ Processing started...\n\n"
            "Please wait."
        )

        asyncio.create_task(
            process_video(
                query.message.chat_id,
                context,
                user_id
            )
        )

        return


# =========================================================
# SUBTITLE MENU
# =========================================================

async def show_subtitles(
    query,
    session
):

    subtitles = session["subtitles"]

    buttons = []

    for subtitle in subtitles:

        name = subtitle.get(
            "name"
        ) or "Subtitle"

        language = subtitle.get(
            "language"
        )

        if language:
            name += f" ({language})"

        buttons.append([
            __import__(
                "telegram"
            ).InlineKeyboardButton(
                f"💬 {name}",
                callback_data=f"subtitle:{subtitle['id']}"
            )
        ])

    buttons.append([
        __import__(
            "telegram"
        ).InlineKeyboardButton(
            "📤 Upload subtitle",
            callback_data="upload_info"
        )
    ])

    buttons.append([
        __import__(
            "telegram"
        ).InlineKeyboardButton(
            "⏭ Skip subtitle",
            callback_data="subtitle_skip"
        )
    ])

    await query.edit_message_text(
        "💬 Select subtitle:",
        reply_markup=__import__(
            "telegram"
        ).InlineKeyboardMarkup(buttons)
    )


# =========================================================
# CONTINUE BUTTON
# =========================================================

async def show_continue(query):

    keyboard = __import__(
        "telegram"
    ).InlineKeyboardMarkup([
        [
            __import__(
                "telegram"
            ).InlineKeyboardButton(
                "▶️ Continue",
                callback_data="continue"
            )
        ]
    ])

    await query.edit_message_text(
        "✅ Selection complete.\n\n"
        "Press ▶️ Continue to start.",
        reply_markup=keyboard
    )


# =========================================================
# SUBTITLE UPLOAD
# =========================================================

async def receive_subtitle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    session = sessions.get(user_id)

    if not session:

        await update.message.reply_text(
            "❌ No active session.\n"
            "Send an .m3u8 URL first."
        )
        return

    document = update.message.document

    if not document:
        return

    filename = document.file_name or ""

    if not filename.lower().endswith(
        (".srt", ".vtt")
    ):

        await update.message.reply_text(
            "❌ Only .srt and .vtt files "
            "are supported."
        )
        return

    # Create working directory
    if not session.get("workdir"):

        session["workdir"] = tempfile.mkdtemp(
            prefix="m3u8bot_"
        )

    workdir = Path(
        session["workdir"]
    )

    subtitle_path = (
        workdir / filename
    )

    telegram_file = await document.get_file()

    await telegram_file.download_to_drive(
        custom_path=str(subtitle_path)
    )

    session[
        "uploaded_subtitle"
    ] = str(subtitle_path)

    # Clear selected remote subtitle
    session["subtitle"] = None

    keyboard = __import__(
        "telegram"
    ).InlineKeyboardMarkup([
        [
            __import__(
                "telegram"
            ).InlineKeyboardButton(
                "▶️ Continue",
                callback_data="continue"
            )
        ]
    ])

    await update.message.reply_text(
        "✅ Subtitle uploaded successfully.\n\n"
        "Press ▶️ Continue.",
        reply_markup=keyboard
    )


# =========================================================
# PROCESS VIDEO
# =========================================================

async def process_video(
    chat_id,
    context,
    user_id
):

    session = sessions.get(user_id)

    if not session:
        return

    async with processing_semaphore:

        if session.get("workdir"):
    workdir = Path(
        session["workdir"]
    )
else:
    workdir = Path(
        tempfile.mkdtemp(
            prefix="m3u8bot_"
        )
    )

session["workdir"] = str(
    workdir
)

        video_file = (
            workdir / "video.mp4"
        )

        audio_file = (
            workdir / "audio.m4a"
        )

        subtitle_file = None

        output_file = (
            workdir / "final.mp4"
        )

        try:

            # -------------------------------------------------
            # VIDEO
            # -------------------------------------------------

            await context.bot.send_message(
                chat_id,
                "🎥 Downloading selected video..."
            )

            video = session["video"]

            video_url = video["uri"]

            await download_file(
                video_url,
                video_file
            )

            # -------------------------------------------------
            # AUDIO
            # -------------------------------------------------

            audio = session.get(
                "audio"
            )

            if audio and audio.get("uri"):

                await context.bot.send_message(
                    chat_id,
                    "🔊 Downloading selected audio..."
                )

                await download_file(
                    audio["uri"],
                    audio_file
                )

            else:

                audio_file = None

            # -------------------------------------------------
            # SUBTITLE
            # -------------------------------------------------

            uploaded_subtitle = session.get(
                "uploaded_subtitle"
            )

            selected_subtitle = session.get(
                "subtitle"
            )

            if uploaded_subtitle:

                subtitle_file = Path(
                    uploaded_subtitle
                )

            elif selected_subtitle:

                subtitle_file = (
                    workdir / "subtitle.vtt"
                )

                subtitle_url = (
                    selected_subtitle["uri"]
                )

                if subtitle_url:

                    await context.bot.send_message(
                        chat_id,
                        "💬 Downloading subtitle..."
                    )

                    await download_file(
                        subtitle_url,
                        subtitle_file
                    )

            # -------------------------------------------------
            # MERGE
            # -------------------------------------------------

            await context.bot.send_message(
                chat_id,
                "🔧 Merging video + audio"
                + (
                    " + subtitle..."
                    if subtitle_file
                    else "..."
                )
            )

            await merge_video_audio(
                video_file=str(
                    video_file
                ),
                audio_file=(
                    str(audio_file)
                    if audio_file
                    else None
                ),
                output_file=str(
                    output_file
                ),
                subtitle_file=(
                    str(subtitle_file)
                    if subtitle_file
                    else None
                )
            )

            # -------------------------------------------------
            # FILE SIZE
            # -------------------------------------------------

            file_size = (
                output_file.stat().st_size
            )

            size_gb = (
                file_size /
                (1024 ** 3)
            )

            await context.bot.send_message(
                chat_id,
                f"✅ Processing complete.\n"
                f"📦 Size: {size_gb:.2f} GB"
            )

            # -------------------------------------------------
            # TELEGRAM
            # -------------------------------------------------

            if file_size <= MAX_TELEGRAM_SIZE:

                await context.bot.send_message(
                    chat_id,
                    "📤 Uploading to Telegram..."
                )

                with open(
                    output_file,
                    "rb"
                ) as file:

                    await context.bot.send_document(
                        chat_id,
                        document=file,
                        caption=(
                            "🎬 MP4 Ready\n\n"
                            f"📦 {size_gb:.2f} GB"
                        )
                    )

            # -------------------------------------------------
            # GOFILE
            # -------------------------------------------------

            else:

                await context.bot.send_message(
                    chat_id,
                    "☁️ File is larger than "
                    "1.94 GB.\n\n"
                    "Uploading to GoFile..."
                )

                download_url = (
                    await upload_to_gofile(
                        str(output_file)
                    )
                )

                await context.bot.send_message(
                    chat_id,
                    "✅ Upload complete!\n\n"
                    "🔗 Download:\n"
                    f"{download_url}"
                )

        except Exception as error:

            await context.bot.send_message(
                chat_id,
                "❌ Processing failed.\n\n"
                f"{str(error)[:4000]}"
            )

        finally:

            # Cleanup
            shutil.rmtree(
                workdir,
                ignore_errors=True
            )

            sessions.pop(
                user_id,
                None
            )


# =========================================================
# MAIN
# =========================================================

def main():

    validate_config()

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    application.add_handler(
        CommandHandler(
            "cancel",
            cancel
        )
    )

    # Inline buttons
    application.add_handler(
        CallbackQueryHandler(
            callback
        )
    )

    # Subtitle files
    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            receive_subtitle
        )
    )

    # m3u8 URL
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_url
        )
    )

    print(
        "🚀 M3U8 Telegram Bot started..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
