from pathlib import Path
import tempfile
import os

COOKIE_DIR = Path(tempfile.gettempdir()) / "akrdownload_cookies"
COOKIE_DIR.mkdir(parents=True, exist_ok=True)


def cookie_path(user_id: int) -> Path:
    return COOKIE_DIR / f"{user_id}.txt"


async def save_cookie_file(document, user_id: int, bot) -> Path:
    """
    Save an uploaded cookie text file temporarily.
    """
    if not document:
        raise ValueError("No document received")

    if document.file_size and document.file_size > 2 * 1024 * 1024:
        raise ValueError("Cookie file is too large")

    filename = (document.file_name or "").lower()

    if not (
        filename.endswith(".txt")
        or filename.endswith(".cookies")
        or filename.endswith(".json")
    ):
        raise ValueError("Only cookie text/JSON files are supported")

    path = cookie_path(user_id)

    tg_file = await bot.get_file(document.file_id)
    await tg_file.download_to_drive(str(path))

    return path


def basic_cookie_check(path: Path) -> bool:
    """
    Basic file validation only.
    Does NOT bypass authentication or DRM.
    """
    if not path.exists():
        return False

    try:
        if path.stat().st_size == 0:
            return False

        content = path.read_text(
            encoding="utf-8",
            errors="ignore"
        ).strip()

        if not content:
            return False

        # Netscape cookie files normally contain tab-separated fields.
        # JSON cookie exports are also allowed.
        if content.startswith("[") or content.startswith("{"):
            return True

        for line in content.splitlines():
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if "\t" in line:
                return True

        return False

    except Exception:
        return False


def delete_cookie_file(user_id: int):
    """
    Remove temporary cookie file.
    """
    path = cookie_path(user_id)

    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def cleanup_all_cookies():
    """
    Remove all temporary cookie files.
    """
    try:
        for path in COOKIE_DIR.iterdir():
            if path.is_file():
                try:
                    path.unlink()
                except Exception:
                    pass
    except Exception:
        pass
