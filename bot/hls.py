from urllib.parse import urljoin

import aiohttp
import m3u8


async def get_playlist(url, headers=None):

    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=headers or {}
    ) as session:

        async with session.get(url) as response:

            response.raise_for_status()

            return await response.text()


async def analyse(url, headers=None):

    text = await get_playlist(url, headers)

    # -------------------------------------------------
    # DRM DETECTION
    # -------------------------------------------------
    if "METHOD=SAMPLE-AES" in text:
        raise RuntimeError(
            "🔒 DRM (SAMPLE-AES) detected.\n\n"
            "This stream is DRM protected and cannot be "
            "downloaded without decryption keys."
        )

    playlist = m3u8.loads(text)

    videos = []
    audios = []
    subtitles = []

    for index, item in enumerate(playlist.playlists):

        info = item.stream_info

        videos.append({
            "id": index,
            "uri": urljoin(url, item.uri),
            "bandwidth": info.bandwidth,
            "resolution": info.resolution,
        })

    for index, item in enumerate(playlist.media):

        if item.type == "AUDIO":

            audios.append({
                "id": index,
                "uri": (
                    urljoin(url, item.uri)
                    if item.uri else None
                ),
                "name": item.name,
                "language": item.language,
            })

        elif item.type == "SUBTITLES":

            subtitles.append({
                "id": index,
                "uri": (
                    urljoin(url, item.uri)
                    if item.uri else None
                ),
                "name": item.name,
                "language": item.language,
            })

    return videos, audios, subtitles
