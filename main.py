import asyncio
import shutil
import tempfile
from pathlib import Path

import aiohttp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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

# Maximum number of simultaneous processing jobs
processing_semaphore = asyncio.Semaphore(2)


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
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
# NORMAL FILE DOWNLOAD
# =========================================================

async def download_file(
    url: str,
    destination: Path
):
    """
    Download normal files such as SRT/VTT.

    HLS .m3u8 URLs are intentionally not handled here.
    HLS media is processed by FFmpeg directly.
    """

    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=30
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(url) as response:

            response.raise_for_status()

            with open(
                destination,
                "wb"
            ) as file:

                async for chunk in response.content.iter_chunked(
                    1024 * 1024
                ):
                    file.write(chunk)

    if not destination.exists():
        raise RuntimeError(
            "Download failed: file was not created."
        )

    if destination.stat().st_size == 0:
        raise RuntimeError(
            "Downloaded file is empty."
        )


# =========================================================
# HLS DOWNLOAD USING FFMPEG
# =========================================================

async def download_hls(
    url: str,
    destination: Path,
    media_type: str
):
    """
    Download an authorized/unprotected HLS media playlist
    using FFmpeg.

    media_type:
        video
        audio
    """

    if media_type == "video":

        command = [
            "ffmpeg",
            "-y",

            "-i",
            url,

            "-map",
            "0:v:0",

            "-c:v",
            "copy",

            str(destination),
        ]

    elif media_type == "audio":

        command = [
            "ffmpeg",
            "-y",

            "-i",
            url,

            "-map",
            "0:a:0",

            "-vn",

            "-c:a",
            "copy",

            str(destination),
        ]

    else:
        raise ValueError(
            f"Unsupported HLS media type: {media_type}"
        )

    print(
        "Running FFmpeg HLS:",
        " ".join(command)
    )

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:

        error = stderr.decode(
            errors="ignore"
        )

        raise RuntimeError(
            "FFmpeg HLS download failed:\n"
            + error[-5000:]
        )

    if not destination.exists():

        raise RuntimeError(
            "FFmpeg completed but media file "
            "was not created."
        )

    if destination.stat().st_size == 0:

        raise RuntimeError(
            "Downloaded media file is empty."
        )


# =========================================================
# RECEIVE M3U8 URL
# =========================================================

async def receive_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    if not update.message.text:
        return

    url = update.message.text.strip()

    if ".m3u8" not in url.lower():

        await update.message.reply_text(
            "❌ Please send a valid .m3u8 URL."
        )

        return

    user_id = update.effective_user.id

    # Remove old session
    old = sessions.pop(
        user_id,
        None
    )

    if old and old.get("workdir"):

        shutil.rmtree(
            old["workdir"],
            ignore_errors=True
        )

    status = await update.message.reply_text(
        "🔍 Analysing HLS playlist..."
    )

    try:

        videos, audios, subtitles = await analyse(
            url
        )

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

            "processing": False,
        }

        buttons = []

        for video in videos:

            resolution = video.get(
                "resolution"
            )

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
                InlineKeyboardButton(
                    f"🎥 {label}",
                    callback_data=(
                        f"video:{video['id']}"
                    )
                )
            ])

        await status.edit_text(
            "🎥 Select video quality:",
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
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

    session = sessions.get(
        user_id
    )

    if not session:

        await query.edit_message_text(
            "❌ Session expired.\n"
            "Please send the .m3u8 URL again."
        )

        return

    data = query.data

    # =====================================================
    # VIDEO
    # =====================================================

    if data.startswith("video:"):

        try:

            index = int(
                data.split(
                    ":",
                    1
                )[1]
            )

            videos = session["videos"]

            if index < 0 or index >= len(videos):

                await query.edit_message_text(
                    "❌ Invalid video selection."
                )

                return

            session["video"] = videos[index]

        except (
            ValueError,
            KeyError
        ):

            await query.edit_message_text(
                "❌ Invalid video selection."
            )

            return

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

                name += (
                    f" ({language})"
                )

            buttons.append([
                InlineKeyboardButton(
                    f"🔊 {name}",
                    callback_data=(
                        f"audio:{audio['id']}"
                    )
                )
            ])

        await query.edit_message_text(
            "🔊 Select audio:",
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

        return

    # =====================================================
    # AUDIO
    # =====================================================

    if data.startswith("audio:"):

        try:

            index = int(
                data.split(
                    ":",
                    1
                )[1]
            )

            audios = session["audios"]

            if index < 0 or index >= len(audios):

                await query.edit_message_text(
                    "❌ Invalid audio selection."
                )

                return

            session["audio"] = audios[index]

        except (
            ValueError,
            KeyError
        ):

            await query.edit_message_text(
                "❌ Invalid audio selection."
            )

            return

        await show_subtitles(
            query,
            session
        )

        return

    # =====================================================
    # SUBTITLE
    # =====================================================

    if data.startswith("subtitle:"):

        try:

            index = int(
                data.split(
                    ":",
                    1
                )[1]
            )

            subtitles = session["subtitles"]

            if index < 0 or index >= len(subtitles):

                await query.edit_message_text(
                    "❌ Invalid subtitle selection."
                )

                return

            selected = subtitles[index]

            if not selected.get("uri"):

                await query.edit_message_text(
                    "❌ This subtitle track does not "
                    "have a downloadable URI."
                )

                return

            session["subtitle"] = selected

            # If previously uploaded subtitle exists,
            # remove it from selection.
            session["uploaded_subtitle"] = None

        except (
            ValueError,
            KeyError
        ):

            await query.edit_message_text(
                "❌ Invalid subtitle selection."
            )

            return

        await show_continue(
            query
        )

        return

    # =====================================================
    # SKIP SUBTITLE
    # =====================================================

    if data == "subtitle_skip":

        session["subtitle"] = None
        session["uploaded_subtitle"] = None

        await show_continue(
            query
        )

        return

    # =====================================================
    # UPLOAD SUBTITLE INFO
    # =====================================================

    if data == "upload_info":

        await query.edit_message_text(
            "📤 Upload subtitle\n\n"
            "Please send your .srt or .vtt "
            "subtitle file here.\n\n"
            "After uploading it, you will get "
            "the ▶️ Continue button."
        )

        return

    # =====================================================
    # CONTINUE
    # =====================================================

    if data == "continue":

        if not session.get("video"):

            await query.edit_message_text(
                "❌ Please select video quality first."
            )

            return

        if session.get("processing"):

            await query.answer(
                "⏳ This job is already processing.",
                show_alert=True
            )

            return

        session["processing"] = True

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

            name += (
                f" ({language})"
            )

        buttons.append([
            InlineKeyboardButton(
                f"💬 {name}",
                callback_data=(
                    f"subtitle:{subtitle['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "📤 Upload subtitle",
            callback_data="upload_info"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "⏭ Skip subtitle",
            callback_data="subtitle_skip"
        )
    ])

    await query.edit_message_text(
        "💬 Select subtitle:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================================================
# CONTINUE BUTTON
# =========================================================

async def show_continue(
    query
):

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
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

    if not update.message:
        return

    document = update.message.document

    if not document:
        return

    user_id = update.effective_user.id

    session = sessions.get(
        user_id
    )

    if not session:

        await update.message.reply_text(
            "❌ No active session.\n"
            "Send an .m3u8 URL first."
        )

        return

    if session.get("processing"):

        await update.message.reply_text(
            "⏳ This job is already processing."
        )

        return

    filename = (
        document.file_name
        or ""
    )

    if not filename.lower().endswith(
        (
            ".srt",
            ".vtt"
        )
    ):

        await update.message.reply_text(
            "❌ Only .srt and .vtt files "
            "are supported."
        )

        return

    # =====================================================
    # CREATE WORKING DIRECTORY
    # =====================================================

    if not session.get("workdir"):

        session["workdir"] = tempfile.mkdtemp(
            prefix="m3u8bot_"
        )

    workdir = Path(
        session["workdir"]
    )

    workdir.mkdir(
        parents=True,
        exist_ok=True
    )

    # Prevent path traversal from Telegram filename
    safe_filename = Path(
        filename
    ).name

    subtitle_path = (
        workdir / safe_filename
    )

    telegram_file = await document.get_file()

    await telegram_file.download_to_drive(
        custom_path=str(
            subtitle_path
        )
    )

    session[
        "uploaded_subtitle"
    ] = str(
        subtitle_path
    )

    # Clear remote subtitle
    session["subtitle"] = None

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
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

    session = sessions.get(
        user_id
    )

    if not session:
        return

    async with processing_semaphore:

        # =================================================
        # WORK DIRECTORY
        # =================================================

        existing_workdir = session.get(
            "workdir"
        )

        if existing_workdir:

            workdir = Path(
                existing_workdir
            )

            workdir.mkdir(
                parents=True,
                exist_ok=True
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

        output_file = (
            workdir / "final.mp4"
        )

        subtitle_file = None

        try:

            # =============================================
            # VIDEO
            # =============================================

            await context.bot.send_message(
                chat_id,
                "🎥 Downloading selected video..."
            )

            video = session.get(
                "video"
            )

            if not video:

                raise RuntimeError(
                    "Video selection is missing."
                )

            video_url = video.get(
                "uri"
            )

            if not video_url:

                raise RuntimeError(
                    "Selected video has no URI."
                )

            # IMPORTANT:
            # HLS playlist -> FFmpeg
            await download_hls(
                video_url,
                video_file,
                "video"
            )

            # =============================================
            # AUDIO
            # =============================================

            audio = session.get(
                "audio"
            )

            if audio and audio.get("uri"):

                await context.bot.send_message(
                    chat_id,
                    "🔊 Downloading selected audio..."
                )

                # HLS audio playlist -> FFmpeg
                await download_hls(
                    audio["uri"],
                    audio_file,
                    "audio"
                )

            else:

                audio_file = None

            # =============================================
            # SUBTITLE
            # =============================================

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

                if not subtitle_file.exists():

                    raise RuntimeError(
                        "Uploaded subtitle file "
                        "could not be found."
                    )

            elif selected_subtitle:

                subtitle_url = selected_subtitle.get(
                    "uri"
                )

                if subtitle_url:

                    subtitle_file = (
                        workdir / "subtitle.vtt"
                    )

                    await context.bot.send_message(
                        chat_id,
                        "💬 Downloading subtitle..."
                    )

                    await download_file(
                        subtitle_url,
                        subtitle_file
                    )

                else:

                    subtitle_file = None

            # =============================================
            # MERGE
            # =============================================

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

            # =============================================
            # VERIFY OUTPUT
            # =============================================

            if not output_file.exists():

                raise RuntimeError(
                    "Final MP4 was not created."
                )

            file_size = (
                output_file.stat().st_size
            )

            if file_size <= 0:

                raise RuntimeError(
                    "Final MP4 is empty."
                )

            size_gb = (
                file_size /
                (1024 ** 3)
            )

            await context.bot.send_message(
                chat_id,
                "✅ Processing complete.\n"
                f"📦 Size: {size_gb:.2f} GB"
            )

            # =============================================
            # TELEGRAM
            # =============================================

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

            # =============================================
            # GOFILE
            # =============================================

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

            print(
                "Processing error:",
                repr(error)
            )

            try:

                await context.bot.send_message(
                    chat_id,
                    "❌ Processing failed.\n\n"
                    f"{str(error)[:4000]}"
                )

            except Exception:

                pass

        finally:

            # =============================================
            # CLEANUP
            # =============================================

            shutil.rmtree(
                workdir,
                ignore_errors=True
            )

            sessions.pop(
                user_id,
                None
            )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "Telegram handler error:",
        repr(context.error)
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

    # =====================================================
    # COMMANDS
    # =====================================================

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

    # =====================================================
    # INLINE BUTTONS
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            callback
        )
    )

    # =====================================================
    # SUBTITLE FILES
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            receive_subtitle
        )
    )

    # =====================================================
    # M3U8 URL
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_url
        )
    )

    # =====================================================
    # ERROR HANDLER
    # =====================================================

    application.add_error_handler(
        error_handler
    )

    print(
        "🚀 M3U8 Telegram Bot started..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()
