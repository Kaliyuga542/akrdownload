import aiohttp
from pathlib import Path


GOFILE_API = "https://api.gofile.io"


# =========================================================
# GET BEST UPLOAD SERVER
# =========================================================

async def get_server():

    timeout = aiohttp.ClientTimeout(
        total=30
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            f"{GOFILE_API}/servers"
        ) as response:

            # -----------------------------------------
            # NON-JSON RESPONSE GUARD
            # -----------------------------------------

            try:

                data = await response.json()

            except Exception:

                raise RuntimeError(
                    f"GoFile servers error: "
                    f"HTTP {response.status} "
                    "(non-JSON response)"
                )

            if data.get("status") != "ok":

                raise RuntimeError(
                    f"GoFile server error: {data}"
                )

            return data["data"]["servers"][0]["name"]


# =========================================================
# UPLOAD TO GOFILE
# =========================================================

async def upload_to_gofile(file_path):

    file_path = Path(file_path)

    if not file_path.exists():

        raise FileNotFoundError(
            f"File not found: {file_path}"
        )

    server = await get_server()

    # ---------------------------------------------
    # NEW GOFILE API ENDPOINT (FIX)
    # ---------------------------------------------

    upload_url = (
        f"https://{server}.gofile.io/"
        "contents/uploadfile"
    )

    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=60,
        sock_read=300
    )

    size_gb = (
        file_path.stat().st_size
        / (1024 ** 3)
    )

    print(
        f"Uploading to GoFile: {file_path.name} "
        f"({size_gb:.2f} GB) -> {upload_url}"
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        with open(
            file_path,
            "rb"
        ) as file:

            form = aiohttp.FormData()

            form.add_field(
                "file",
                file,
                filename=file_path.name,
                content_type="video/mp4"
            )

            async with session.post(
                upload_url,
                data=form
            ) as response:

                # ---------------------------------
                # NON-JSON RESPONSE GUARD
                # ---------------------------------

                try:

                    data = await response.json()

                except Exception:

                    raise RuntimeError(
                        f"GoFile upload failed: "
                        f"HTTP {response.status} "
                        "(non-JSON response)"
                    )

    if data.get("status") != "ok":

        raise RuntimeError(
            f"GoFile upload failed: {data}"
        )

    download_page = (
        data.get("data", {})
        .get("downloadPage")
    )

    if not download_page:

        raise RuntimeError(
            f"GoFile download page missing: {data}"
        )

    return download_page
