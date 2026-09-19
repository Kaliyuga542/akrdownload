import os


BOT_TOKEN = os.getenv("BOT_TOKEN")


MAX_TELEGRAM_SIZE = int(
    float(
        os.getenv(
            "MAX_TELEGRAM_SIZE",
            str(1.94 * 1024 * 1024 * 1024)
        )
    )
)


TEMP_DIR = os.getenv(
    "TEMP_DIR",
    "/tmp/m3u8_bot"
)


MAX_CONCURRENT_JOBS = int(
    os.getenv(
        "MAX_CONCURRENT_JOBS",
        "2"
    )
)


FFMPEG_PATH = os.getenv(
    "FFMPEG_PATH",
    "ffmpeg"
)


GOFILE_API = os.getenv(
    "GOFILE_API",
    "https://api.gofile.io"
)


def validate_config():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )
