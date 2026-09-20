import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from aiohttp import web
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
    MAX_CONCURRENT_JOBS,
    validate_config,
)

from bot.hls import analyse as analyse_hls
from bot.dash import analyse as analyse_dash
from bot.handlers import receive_cookie_file
from bot.uploader import upload_to_gofile
from bot.processor import merge_video_audio
from bot.cookie_manager import cookie_path
from bot.http_headers import (
    build_headers,
    headers_to_ffmpeg_format,
)


# =========================================================
# USER SESSIONS
# =========================================================

sessions = {}

processing_semaphore = asyncio.Semaphore(
    MAX_CONCURRENT_JOBS
)


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    validate_config()

    await update.message.reply_text(
        "🎬 HLS / DASH → MP4 Bot\n\n"
        "Send an authorized .m3u8 or .mpd URL.\n\n"
        "I will show:\n"
        "🎥 Video quality\n"
        "🔊 Audio\n"
        "💬 Subtitle\n\n"
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
        "1️⃣ Send .m3u8 or .mpd URL\n"
        "2️⃣ Select video quality\n"
        "3️⃣ Select audio\n"
        "4️⃣ Select subtitle or upload .srt/.vtt\n"
        "5️⃣ Press Continue\n"
        "6️⃣ Bot creates MP4\n\n"
        "📦 ≤ 1.94 GB → Telegram\n"
        "☁️ > 1.94 GB → GoFile\n\n"
        "🍪 If you get 403 error:\n"
        "Upload your cookies file, then "
        "send the URL again."
    )


# =========================================================
# CANCEL
# =========================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    session = sessions.pop(
        user_id,
        None
    )

    if session:

        workdir = session.get(
            "workdir"
        )

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
    destination: Path,
    headers=None
):

    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=30
    )

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=headers or {}
    ) as session:

        async with session.get(
            url
        ) as response:

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
# FFMPEG MEDIA DOWNLOAD
# =========================================================

async def download_media(
    url: str,
    destination: Path,
    media_type: str,
    headers=None
):

    if media_type == "video":

        command = [
            "ffmpeg",
            "-y",
        ]

    elif media_type == "audio":

        command = [
            "ffmpeg",
            "-y",
        ]

    else:

        raise ValueError(
            f"Unsupported media type: {media_type}"
        )

    # ----------------------------------------------------
    # HEADERS + COOKIES (403 FIX)
    # ----------------------------------------------------

    header_str = headers_to_ffmpeg_format(
        headers or {}
    )

    if header_str:

        command.extend([
            "-headers",
            header_str
        ])

    if media_type == "video":

        command.extend([
            "-i",
            url,
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            str(destination)
        ])

    else:

        command.extend([
            "-i",
            url,
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "copy",
            str(destination)
        ])

    print(
        "Running FFmpeg:",
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
            "FFmpeg download failed:\n"
            + error[-5000:]
        )

    if not destination.exists():

        raise RuntimeError(
            "FFmpeg did not create the media file."
        )

    if destination.stat().st_size == 0:

        raise RuntimeError(
            "Downloaded media file is empty."
        )


# =========================================================
# HLS SUBTITLE PLAYLIST → WEBVTT EXTRACTION
# =========================================================

async def extract_webvtt(
    subtitle_url: str,
    output_path: Path,
    headers=None
):

    command = [
        "ffmpeg",
        "-y",
    ]

    header_str = headers_to_ffmpeg_format(
        headers or {}
    )

    if header_str:

        command.extend([
            "-headers",
            header_str
        ])

    command.extend([
        "-i",
        subtitle_url,
        "-map",
        "0:s:0",
        "-f",
        "webvtt",
        str(output_path)
    ])

    print(
        "Extracting WebVTT:",
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
            "Subtitle extraction failed:\n"
            + error[-3000:]
        )

    if (
        not output_path.exists()
        or output_path.stat().st_size == 0
    ):

        raise RuntimeError(
            "Subtitle extraction produced "
            "an empty file."
        )


# =========================================================
# DISK SPACE CHECK
# =========================================================

def check_disk_space(
    min_gb: float = 2.0
) -> float:

    free_gb = (
        shutil.disk_usage("/tmp").free
        / (1024 ** 3)
    )

    return free_gb


# =========================================================
# KEEPALIVE PROGRESS MESSAGES
# =========================================================

async def keepalive_messages(
    bot,
    chat_id,
    stop_event: asyncio.Event
):

    try:

        while not stop_event.is_set():

            try:

                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=60
                )

            except asyncio.TimeoutError:
                pass

            if stop_event.is_set():
                break

            try:

                await bot.send_message(
                    chat_id,
                    "⏳ Still working, please wait..."
                )

            except Exception:
                pass

    except Exception:
        pass


# =========================================================
# RECEIVE M3U8 / MPD URL
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

    lower_url = url.lower()

    is_hls = ".m3u8" in lower_url
    is_dash = ".mpd" in lower_url

    if not is_hls and not is_dash:

        await update.message.reply_text(
            "❌ Please send a valid .m3u8 "
            "or .mpd URL."
        )

        return

    user_id = update.effective_user.id

    old = sessions.pop(
        user_id,
        None
    )

    if old and old.get("workdir"):

        shutil.rmtree(
            old["workdir"],
            ignore_errors=True
        )

    # ----------------------------------------------------
    # BUILD HEADERS + COOKIES (403 FIX)
    # ----------------------------------------------------

    cpath = cookie_path(user_id)

    if cpath.exists():

        headers = build_headers(cpath)

    else:

        headers = build_headers(None)

    status = await update.message.reply_text(
        "🔍 Analysing playlist..."
    )

    try:

        # =================================================
        # HLS
        # =================================================

        if is_hls:

            playlist_type = "hls"

            videos, audios, subtitles = (
                await analyse_hls(
                    url,
                    headers=headers
                )
            )

            status_text = (
                "🎥 Select video quality:"
            )

        # =================================================
        # DASH
        # =================================================

        else:

            playlist_type = "dash"

            videos, audios, subtitles = (
                await analyse_dash(
                    url,
                    headers=headers
                )
            )

            status_text = (
                "🎥 Select DASH video quality:"
            )

        if not videos:

            await status.edit_text(
                "❌ No selectable video "
                "qualities found."
            )

            return

        sessions[user_id] = {

            "url": url,

            "playlist_type": playlist_type,

            "headers": headers,

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
            status_text,
            reply_markup=InlineKeyboardMarkup(
                buttons
            )
        )

    except Exception as error:

        error_text = str(error)

        if "403" in error_text or "Forbidden" in error_text:

            keyboard = [
                [
                    InlineKeyboardButton(
                        "🍪 Upload Cookies",
                        callback_data="upload_cookies"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="cancel_analysis"
                    )
                ]
            ]

            await status.edit_text(
                "❌ Playlist access denied (403).\n\n"
                "🍪 Upload an authorized cookie file, "
                "then send the URL again.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        else:

            await status.edit_text(
                "❌ Playlist analysis failed.\n\n"
                f"{error_text[:3000]}"
            )


# =========================================================
# CALLBACK
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
            "Please send the URL again."
        )

        return

    data = query.data

    if data == "upload_cookies":

        await query.answer()

        await query.message.reply_text(
            "🍪 Please send your authorized cookie file.\n\n"
            "Supported:\n"
            "• .txt\n"
            "• .cookies\n"
            "• .json\n\n"
            "After uploading, send the URL again."
        )

        return

    if data == "cancel_analysis":

        sessions.pop(
            user_id,
            None
        )

        await query.edit_message_text(
            "❌ Cancelled."
        )

        return

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

                raise ValueError(
                    "Invalid video index"
                )

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

                raise ValueError(
                    "Invalid audio index"
                )

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

                raise ValueError(
                    "Invalid subtitle index"
                )

            selected = subtitles[index]

            if not selected.get("uri"):

                await query.edit_message_text(
                    "❌ This subtitle track does "
                    "not have a downloadable URI."
                )

                return

            session["subtitle"] = selected

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
    # UPLOAD SUBTITLE
    # =====================================================

    if data == "upload_info":

        await query.edit_message_text(
            "📤 Upload subtitle\n\n"
            "Send your .srt or .vtt file."
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
            "Send a .m3u8 or .mpd URL first."
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

    session["uploaded_subtitle"] = str(
        subtitle_path
    )

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

        # ------------------------------------------------
        # DISK SPACE CHECK
        # ------------------------------------------------

        free_gb = check_disk_space()

        if free_gb < 2.0:

            await context.bot.send_message(
                chat_id,
                "❌ Not enough disk space on server "
                f"({free_gb:.1f} GB free).\n"
                "Please try again later."
            )

            sessions.pop(
                user_id,
                None
            )

            return

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

        # ------------------------------------------------
        # HEADERS FROM SESSION (403 FIX)
        # ------------------------------------------------

        headers = session.get(
            "headers"
        ) or {}

        # ------------------------------------------------
        # KEEPALIVE TASK
        # ------------------------------------------------

        stop_event = asyncio.Event()

        keepalive_task = asyncio.create_task(
            keepalive_messages(
                context.bot,
                chat_id,
                stop_event
            )
        )

        try:

            # =================================================
            # VIDEO
            # =================================================

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

            playlist_type = session.get(
                "playlist_type"
            )

            # =================================================
            # HLS / DASH
            # =================================================

            if playlist_type == "hls":

                await download_media(
                    video_url,
                    video_file,
                    "video",
                    headers=headers
                )

            elif playlist_type == "dash":

                await download_media(
                    video_url,
                    video_file,
                    "video",
                    headers=headers
                )

            else:

                raise RuntimeError(
                    "Unknown playlist type."
                )

            # =================================================
            # AUDIO
            # =================================================

            audio = session.get(
                "audio"
            )

            if audio and audio.get("uri"):

                await context.bot.send_message(
                    chat_id,
                    "🔊 Downloading selected audio..."
                )

                audio_url = audio.get(
                    "uri"
                )

                await download_media(
                    audio_url,
                    audio_file,
                    "audio",
                    headers=headers
                )

            else:

                audio_file = None

            # =================================================
            # SUBTITLE
            # =================================================

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
                        subtitle_file,
                        headers=headers
                    )

                    # =========================================
                    # HLS SUBTITLE PLAYLIST DETECTION (FIX)
                    # =========================================

                    try:

                        head = subtitle_file.read_text(
                            encoding="utf-8",
                            errors="ignore"
                        )[:64]

                    except Exception:

                        head = ""

                    if head.startswith("#EXTM3U"):

                        await context.bot.send_message(
                            chat_id,
                            "💬 Subtitle is a playlist. "
                            "Extracting WebVTT..."
                        )

                        await extract_webvtt(
                            subtitle_url,
                            subtitle_file,
                            headers=headers
                        )

            # =================================================
            # MERGE
            # =================================================

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

            # =================================================
            # VERIFY
            # =================================================

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

            # Stop keepalive before upload messages
            stop_event.set()

            await context.bot.send_message(
                chat_id,
                "✅ Processing complete.\n"
                f"📦 Size: {size_gb:.2f} GB"
            )

            # =================================================
            # TELEGRAM
            # =================================================

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

            # =================================================
            # GOFILE
            # =================================================

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

            stop_event.set()

            keepalive_task.cancel()

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
# KOYEB HEALTH SERVER
# =========================================================

async def health_handler(
    request
):

    return web.Response(
        text="OK",
        status=200
    )


async def start_health_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health_handler
    )

    app.router.add_get(
        "/health",
        health_handler
    )

    # Respect Koyeb PORT env variable
    port = int(
        os.getenv(
            "PORT",
            "8000"
        )
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(
        f"❤️ Health server running on port {port}"
    )

    return runner


# =========================================================
# DOCUMENT ROUTER (SUBTITLE / COOKIE)
# =========================================================

async def receive_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:

        return

    document = update.message.document

    if not document:

        return

    filename = (
        document.file_name
        or ""
    ).lower()

    if filename.endswith(
        (
            ".srt",
            ".vtt"
        )
    ):
        await receive_subtitle(
            update,
            context
        )

        return

    if filename.endswith(
        (
            ".txt",
            ".cookies",
            ".json"
        )
    ):
        await receive_cookie_file(
            update,
            context
        )

        return

    await update.message.reply_text(
        "❌ Unsupported file.\n\n"
        "Subtitle: .srt / .vtt\n"
        "Cookie file: .txt / .cookies / .json"
    )


# =========================================================
# TELEGRAM BOT
# =========================================================

async def run_bot():

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
    # CALLBACKS
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            callback
        )
    )

    # =====================================================
    # DOCUMENTS (single router - filter bug fixed)
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            receive_document
        )
    )

    # =====================================================
    # URL
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_url
        )
    )

    # =====================================================
    # ERROR
    # =====================================================

    application.add_error_handler(
        error_handler
    )

    # =====================================================
    # START
    # =====================================================

    await application.initialize()

    await application.start()

    await application.updater.start_polling(
        drop_pending_updates=True
    )

    print(
        "🚀 M3U8 / MPD Telegram Bot started..."
    )

    await asyncio.Event().wait()


# =========================================================
# MAIN
# =========================================================

async def main():

    await start_health_server()

    await run_bot()


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
