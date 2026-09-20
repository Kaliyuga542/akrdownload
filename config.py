import os


# =========================================================
# BOT TOKEN
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")


# =========================================================
# MAX FILE SIZE FOR TELEGRAM UPLOAD
# Default: 1.94 GB (Telegram bot API limit ~2 GB)
# =========================================================

MAX_TELEGRAM_SIZE = int(
    float(
        os.getenv(
            "MAX_TELEGRAM_SIZE",
            str(1.94 * 1024 * 1024 * 1024)
        )
    )
)


# =========================================================
# MAX CONCURRENT PROCESSING JOBS
# Koyeb free tier: 1 recommended (CPU/disk limit)
# =========================================================

MAX_CONCURRENT_JOBS = int(
    os.getenv(
        "MAX_CONCURRENT_JOBS",
        "1"
    )
)


# =========================================================
# VALIDATION
# =========================================================

def validate_config():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )
