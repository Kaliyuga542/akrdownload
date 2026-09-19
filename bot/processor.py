import asyncio
from pathlib import Path


async def run_ffmpeg(
    video,
    audio,
    subtitle,
    output
):

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video)
    ]

    if audio:
        command += [
            "-i",
            str(audio)
        ]

    if subtitle:
        command += [
            "-i",
            str(subtitle)
        ]

    command += [
        "-map",
        "0:v:0"
    ]

    if audio:

        command += [
            "-map",
            "1:a:0"
        ]

    else:

        command += [
            "-map",
            "0:a?"
        ]

    if subtitle:

        subtitle_index = 2 if audio else 1

        command += [
            "-map",
            f"{subtitle_index}:0",
            "-c:s",
            "mov_text"
        ]

    command += [
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output)
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:

        raise RuntimeError(
            stderr.decode(
                errors="ignore"
            )[-4000:]
        )

    return Path(output)
