import aiohttp
from pathlib import Path


GOFILE_API = "https://api.gofile.io"


async def get_server():
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            f"{GOFILE_API}/servers"
        ) as response:

            data = await response.json()

            if data.get("status") != "ok":
                raise RuntimeError(
                    f"GoFile server error: {data}"
                )

            return data["data"]["servers"][0]["name"]


async def upload_to_gofile(file_path):

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"File not found: {file_path}"
        )

    server = await get_server()

    upload_url = (
        f"https://{server}.gofile.io/uploadFile"
    )

    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=60
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        with open(file_path, "rb") as file:

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

                data = await response.json()

    if data.get("status") != "ok":
        raise RuntimeError(
            f"GoFile upload failed: {data}"
        )

    return data["data"]["downloadPage"]
