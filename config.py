import os

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Telegram upload limit used by this bot
MAX_TELEGRAM_SIZE = int(
    os.getenv(
        "MAX_TELEGRAM_SIZE",
        str(1.94 * 1024 * 1024 * 1024)
    )
)

# Temporary working directory
TEMP_DIR = os.getenv(
    "TEMP_DIR",
    "/tmp/m3u8_bot"
)

# Maximum concurrent processing jobs
MAX_CONCURRENT_JOBS = int(
    os.getenv(
        "MAX_CONCURRENT_JOBS",
        "2"
    )
)

# FFmpeg executable
FFMPEG_PATH = os.getenv(
    "FFMPEG_PATH",
    "ffmpeg"
)

# GoFile API
GOFILE_API = os.getenv(
    "GOFILE_API",
    "https://api.gofile.io"
)


def validate_config():
    """
    Check required configuration.
    """

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )
