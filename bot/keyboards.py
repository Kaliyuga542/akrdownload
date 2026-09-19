from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup
)


def video_keyboard(videos):

    buttons = []

    for item in videos:

        resolution = item.get(
            "resolution"
        )

        if resolution:

            label = (
                f"{resolution[0]}x"
                f"{resolution[1]}"
            )

        else:
            label = "Unknown"

        buttons.append([
            InlineKeyboardButton(
                f"🎥 {label}",
                callback_data=f"video:{item['id']}"
            )
        ])

    return InlineKeyboardMarkup(buttons)


def audio_keyboard(audios):

    buttons = []

    for item in audios:

        name = item.get("name") or "Audio"

        language = item.get("language")

        if language:
            name += f" ({language})"

        buttons.append([
            InlineKeyboardButton(
                f"🔊 {name}",
                callback_data=f"audio:{item['id']}"
            )
        ])

    return InlineKeyboardMarkup(buttons)


def subtitle_keyboard(subtitles):

    buttons = []

    for item in subtitles:

        name = item.get("name") or "Subtitle"

        language = item.get("language")

        if language:
            name += f" ({language})"

        buttons.append([
            InlineKeyboardButton(
                f"💬 {name}",
                callback_data=f"subtitle:{item['id']}"
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
            "⏭ Skip",
            callback_data="subtitle_skip"
        )
    ])

    return InlineKeyboardMarkup(buttons)


def continue_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Continue",
                callback_data="continue"
            )
        ]
    ])
