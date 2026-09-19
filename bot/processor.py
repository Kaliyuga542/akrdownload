import asyncio
from pathlib import Path


async def merge_video_audio(
    video_file: str,
    audio_file: str | None,
    output_file: str,
    subtitle_file: str | None = None,
):
    """
    Merge video + audio + optional subtitle into MP4.
    """

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_file),
    ]

    # Audio input
    if audio_file:
        command.extend([
            "-i",
            str(audio_file),
        ])

    # Subtitle input
    if subtitle_file:
        command.extend([
            "-i",
            str(subtitle_file),
        ])

    # Video
    command.extend([
        "-map",
        "0:v:0",
    ])

    # Audio
    if audio_file:
        command.extend([
            "-map",
            "1:a:0",
        ])
    else:
        command.extend([
            "-map",
            "0:a?",
        ])

    # Subtitle
    if subtitle_file:

        subtitle_index = 2 if audio_file else 1

        command.extend([
            "-map",
            f"{subtitle_index}:0",
            "-c:s",
            "mov_text",
        ])

    # Codecs
    command.extend([
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_file),
    ])

    print(
        "Running FFmpeg:",
        " ".join(command)
    )

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:

        error = stderr.decode(
            errors="ignore"
        )

        raise RuntimeError(
            "FFmpeg failed:\n"
            + error[-5000:]
        )

    result = Path(output_file)

    if not result.exists():
        raise RuntimeError(
            "FFmpeg completed but "
            "output file was not created."
        )

    if result.stat().st_size == 0:
        raise RuntimeError(
            "Output MP4 is empty."
        )

    return result


async def run_ffmpeg(
    video,
    audio,
    subtitle,
    output,
):
    """
    Backward-compatible FFmpeg wrapper.
    """

    return await merge_video_audio(
        video_file=str(video),
        audio_file=(
            str(audio)
            if audio
            else None
        ),
        output_file=str(output),
        subtitle_file=(
            str(subtitle)
            if subtitle
            else None
        ),
    )


def get_file_size(path):
    """Return file size in bytes."""

    return Path(path).stat().st_size


def get_file_size_gb(path):
    """Return file size in GB."""

    return (
        get_file_size(path)
        / (1024 ** 3)
    )
