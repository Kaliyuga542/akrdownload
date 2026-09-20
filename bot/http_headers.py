from pathlib import Path

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def parse_netscape_cookies(path: Path) -> dict:

    cookies = {}

    try:

        for line in path.read_text(
            encoding="utf-8",
            errors="ignore"
        ).splitlines():

            line = line.strip()

            if not line:
                continue

            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]

            elif line.startswith("#"):
                continue

            parts = line.split("\t")

            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]

    except Exception:
        pass

    return cookies


def parse_json_cookies(path: Path) -> dict:

    import json

    cookies = {}

    try:

        data = json.loads(
            path.read_text(
                encoding="utf-8",
                errors="ignore"
            )
        )

        if isinstance(data, list):

            for item in data:

                if isinstance(item, dict):

                    name = item.get("name")
                    value = item.get("value")

                    if name:
                        cookies[name] = value or ""

        elif isinstance(data, dict):

            for key, value in data.items():

                if isinstance(value, str):
                    cookies[key] = value

                elif isinstance(value, dict):
                    name = value.get("name") or key
                    cookies[name] = value.get("value") or ""

    except Exception:
        pass

    return cookies


def load_cookies(path: Path | None) -> dict:

    if not path or not path.exists():
        return {}

    try:
        content = path.read_text(
            encoding="utf-8",
            errors="ignore"
        ).strip()
    except Exception:
        return {}

    if not content:
        return {}

    if content.startswith("[") or content.startswith("{"):
        return parse_json_cookies(path)

    return parse_netscape_cookies(path)


def build_cookie_header(path: Path | None) -> str | None:

    cookies = load_cookies(path)

    if not cookies:
        return None

    return "; ".join(
        f"{k}={v}"
        for k, v in cookies.items()
    )


def build_headers(path: Path | None = None) -> dict:

    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }

    cookie = build_cookie_header(path)

    if cookie:
        headers["Cookie"] = cookie

    return headers


def headers_to_ffmpeg_format(headers: dict) -> str | None:

    if not headers:
        return None

    return "".join(
        f"{k}: {v}\r\n"
        for k, v in headers.items()
    )
