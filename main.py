import os
import asyncio
import tempfile
import shutil
from pathlib import Path

import aiohttp
import m3u8

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

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

MAX_TELEGRAM_SIZE = int(1.94 * 1024 * 1024 * 1024)

sessions = {}


# ---------------------------------------------------------
# HLS
# ---------------------------------------------------------

async def get_text(url):
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()


def absolute_url(base, uri):
    from urllib.parse import urljoin
    return urljoin(base, uri)


async def analyse_hls(url):

    text = await get_text(url)

    playlist = m3u8.loads(text)

    videos = []
    audios = []
    subtitles = []

    # Master playlist
    if playlist.is_variant:

        for index, stream in enumerate(playlist.playlists):

            info = stream.stream_info

            videos.append({
                "id": index,
                "uri": absolute_url(url, stream.uri),
                "bandwidth": info.bandwidth,
                "resolution": info.resolution,
                "name": (
                    f"{info.resolution[0]}x{info.resolution[1]}"
                    if info.resolution
                    else "Unknown"
                )
            })

        for index, media in enumerate(playlist.media):

            if media.type == "AUDIO":

                audios.append({
                    "id": index,
                    "uri": absolute_url(url, media.uri)
                    if media.uri else None,
                    "name": media.name or "Audio",
                    "language": media.language or ""
                })

            elif media.type == "SUBTITLES":

                subtitles.append({
                    "id": index,
                    "uri": absolute_url(url, media.uri)
                    if media.uri else None,
                    "name": media.name or "Subtitle",
                    "language": media.language or ""
                })

    return videos, audios, subtitles


# ---------------------------------------------------------
# START
# ---------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🎬 HLS → MP4 Bot\n\n"
        "Send an authorized .m3u8 URL."
    )


# ---------------------------------------------------------
# URL RECEIVED
# ---------------------------------------------------------

async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = update.message.text.strip()

    if ".m3u8" not in url:
        await update.message.reply_text(
            "❌ Please send a valid HLS .m3u8 URL."
        )
        return

    msg = await update.message.reply_text(
        "🔍 Analysing HLS playlist..."
    )

    try:

        videos, audios, subtitles = await analyse_hls(url)

        if not videos:
            await msg.edit_text(
                "❌ No selectable video variants found."
            )
            return

        user_id = update.effective_user.id

        sessions[user_id] = {
            "url": url,
            "videos": videos,
            "audios": audios,
            "subtitles": subtitles,
            "video": None,
            "audio": None,
            "subtitle": None,
            "uploaded_subtitle": None,
        }

        buttons = []

        for video in videos:

            resolution = video["name"]

            buttons.append([
                InlineKeyboardButton(
                    f"🎥 {resolution}",
                    callback_data=f"video:{video['id']}"
                )
            ])

        await msg.edit_text(
            "🎥 Select video quality:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    except Exception as e:

        await msg.edit_text(
            f"❌ HLS analysis failed:\n{e}"
        )


# ---------------------------------------------------------
# CALLBACK
# ---------------------------------------------------------

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if user_id not in sessions:
        await query.edit_message_text(
            "❌ Session expired. Send the .m3u8 link again."
        )
        return

    session = sessions[user_id]

    data = query.data

    # VIDEO
    if data.startswith("video:"):

        index = int(data.split(":")[1])

        session["video"] = session["videos"][index]

        audios = session["audios"]

        if not audios:

            await show_subtitles(query, session)
            return

        buttons = []

        for audio in audios:

            label = audio["name"]

            if audio["language"]:
                label += f" ({audio['language']})"

            buttons.append([
                InlineKeyboardButton(
                    f"🔊 {label}",
                    callback_data=f"audio:{audio['id']}"
                )
            ])

        await query.edit_message_text(
            "🔊 Select audio:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # AUDIO
    elif data.startswith("audio:"):

        index = int(data.split(":")[1])

        session["audio"] = session["audios"][index]

        await show_subtitles(query, session)

    # SUBTITLE
    elif data.startswith("subtitle:"):

        index = int(data.split(":")[1])

        session["subtitle"] = session["subtitles"][index]

        await show_continue(query)

    # SKIP SUBTITLE
    elif data == "subtitle_skip":

        session["subtitle"] = None

        await show_continue(query)

    # CONTINUE
    elif data == "continue":

        await query.edit_message_text(
            "⏳ Starting processing..."
        )

        asyncio.create_task(
            process_video(
                query.message.chat_id,
                context,
                user_id
            )
        )


# ---------------------------------------------------------
# SUBTITLES
# ---------------------------------------------------------

async def show_subtitles(query, session):

    subtitles = session["subtitles"]

    buttons = []

    for subtitle in subtitles:

        label = subtitle["name"]

        if subtitle["language"]:
            label += f" ({subtitle['language']})"

        buttons.append([
            InlineKeyboardButton(
                f"💬 {label}",
                callback_data=f"subtitle:{subtitle['id']}"
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
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_continue(query):

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Continue",
                callback_data="continue"
            )
        ]
    ])

    await query.edit_message_text(
        "✅ Subtitle selected.\n\n"
        "Press Continue to start processing.",
        reply_markup=keyboard
    )


# ---------------------------------------------------------
# SUBTITLE UPLOAD
# ---------------------------------------------------------

async def receive_subtitle(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if user_id not in sessions:
        await update.message.reply_text(
            "❌ Start again by sending the HLS URL."
        )
        return

    document = update.message.document

    if not document:
        return

    filename = document.file_name or ""

    if not filename.lower().endswith((".srt", ".vtt")):

        await update.message.reply_text(
            "❌ Upload only .srt or .vtt subtitle files."
        )
        return

    workdir = Path(tempfile.mkdtemp())

    subtitle_path = workdir / filename

    telegram_file = await document.get_file()

    await telegram_file.download_to_drive(
        custom_path=str(subtitle_path)
    )

    sessions[user_id]["uploaded_subtitle"] = str(
        subtitle_path
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Continue",
                callback_data="continue"
            )
        ]
    ])

    await update.message.reply_text(
        "✅ Subtitle uploaded.\n\n"
        "Press Continue.",
        reply_markup=keyboard
    )


# ---------------------------------------------------------
# PROCESS
# ---------------------------------------------------------

async def process_video(chat_id, context, user_id):

    session = sessions[user_id]

    workdir = Path(tempfile.mkdtemp())

    try:

        video = session["video"]
        audio = session["audio"]

        video_url = video["uri"]

        audio_url = (
            audio["uri"]
            if audio
            else None
        )

        video_file = workdir / "video.mp4"
        audio_file = workdir / "audio.m4a"
        subtitle_file = None
        output_file = workdir / "final.mp4"

        await context.bot.send_message(
            chat_id,
            "⬇️ Downloading video..."
        )

        await download_file(
            video_url,
            video_file
        )

        if audio_url:

            await context.bot.send_message(
                chat_id,
                "🔊 Downloading audio..."
            )

            await download_file(
                audio_url,
                audio_file
            )

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

            subtitle_file = workdir / "subtitle.vtt"

            await download_file(
                selected_subtitle["uri"],
                subtitle_file
            )

        await context.bot.send_message(
            chat_id,
            "🔧 Merging video, audio and subtitles..."
        )

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_file)
        ]

        if audio_url:
            command += [
                "-i",
                str(audio_file)
            ]

        if subtitle_file:
            command += [
                "-i",
                str(subtitle_file)
            ]

        command += [
            "-map",
            "0:v:0"
        ]

        if audio_url:
            command += [
                "-map",
                "1:a:0"
            ]
        else:
            command += [
                "-map",
                "0:a?"
            ]

        if subtitle_file:

            subtitle_index = 2 if audio_url else 1

            command += [
                "-map",
                f"{subtitle_index}:0"
            ]

        command += [
            "-c:v",
            "copy",
            "-c:a",
            "aac"
        ]

        if subtitle_file:

            command += [
                "-c:s",
                "mov_text"
            ]

        command += [
            "-movflags",
            "+faststart",
            str(output_file)
        ]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:

            raise RuntimeError(
                stderr.decode(errors="ignore")[-3000:]
            )

        size = output_file.stat().st_size

        if size <= MAX_TELEGRAM_SIZE:

            await context.bot.send_message(
                chat_id,
                "📤 Uploading to Telegram..."
            )

            with open(output_file, "rb") as f:

                await context.bot.send_document(
                    chat_id,
                    document=f,
                    caption="✅ MP4 ready"
                )

        else:

            await context.bot.send_message(
                chat_id,
                "📦 File is larger than 1.94 GB.\n"
                "GoFile upload integration should be configured here."
            )

    except Exception as e:

        await context.bot.send_message(
            chat_id,
            f"❌ Processing failed:\n{e}"
        )

    finally:

        shutil.rmtree(
            workdir,
            ignore_errors=True
        )

        sessions.pop(user_id, None)


# ---------------------------------------------------------
# DOWNLOAD
# ---------------------------------------------------------

async def download_file(url, destination):

    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=30
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(url) as response:

            response.raise_for_status()

            with open(destination, "wb") as f:

                async for chunk in response.content.iter_chunked(
                    1024 * 1024
                ):

                    f.write(chunk)


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(callback)
    )

    app.add_handler(
        MessageHandler(
            filters.Document.ALL,
            receive_subtitle
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_url
        )
    )

    print("Bot started...")

    app.run_polling()


if __name__ == "__main__":
    main()
