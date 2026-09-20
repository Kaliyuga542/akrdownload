import asyncio
from pathlib import Path


# =========================================================
# FFPROBE AUDIO CODEC CHECK
# =========================================================

async def get_audio_codec(
    path: str
) -> str:

    """
    Return first audio stream codec name.
    Empty string if probe fails.
    """

    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]

    try:

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:

            return ""

        return stdout.decode(
            errors="ignore"
        ).strip().lower()

    except Exception:

        return ""


# =========================================================
# MERGE VIDEO + AUDIO + SUBTITLE
# =========================================================

async def merge_video_audio(
    video_file: str,
    audio_file: str | None,
    output_file: str,
    subtitle_file: str | None = None,
):

    """
    Merge video + audio + optional subtitle into MP4.

    Audio already AAC → stream copy (fast).
    Otherwise → re-encode to AAC 192k.
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
        "-c:v",
        "copy",
    ])

    # Audio
    if audio_file:

        audio_codec = await get_audio_codec(
            audio_file
        )

        if audio_codec == "aac":

            # Already AAC → fast copy
            command.extend([
                "-map",
                "1:a:0",
                "-c:a",
                "copy",
            ])

        else:

            # Re-encode for MP4 compatibility
            command.extend([
                "-map",
                "1:a:0",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ])

    else:

        command.extend([
            "-map",
            "0:a?",
            "-c:a",
            "copy",
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

    # Muxing stability fix
    command.extend([
        "-max_muxing_queue_size",
        "1024",
    ])

    # MP4 optimization
    command.extend([
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


# =========================================================
# BACKWARD-COMPATIBLE WRAPPER
# =========================================================

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


# =========================================================
# SIZE HELPERS
# =========================================================

def get_file_size(path):
    """Return file size in bytes."""

    return Path(path).stat().st_size


def get_file_size_gb(path):
    """Return file size in GB."""

    return (
        get_file_size(path)
        / (1024 ** 3)
    )
