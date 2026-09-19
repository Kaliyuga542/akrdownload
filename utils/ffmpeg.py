import asyncio
from pathlib import Path


async def run_ffmpeg(
    inputs: list[str],
    output: str,
    maps: list[str] | None = None,
    subtitle: bool = False
):
    """
    Run FFmpeg asynchronously.

    inputs:
        List of input file/URL paths.

    output:
        Final output MP4 path.

    maps:
        Optional FFmpeg map arguments.

    subtitle:
        Whether subtitle stream is included.
    """

    command = [
        "ffmpeg",
        "-y"
    ]

    # Inputs
    for input_file in inputs:

        command.extend([
            "-i",
            str(input_file)
        ])

    # Mapping
    if maps:

        for mapping in maps:

            command.extend([
                "-map",
                mapping
            ])

    else:

        # Default video/audio mapping
        command.extend([
            "-map",
            "0:v:0",
            "-map",
            "0:a?"
        ])

    # Video
    command.extend([
        "-c:v",
        "copy"
    ])

    # Audio
    command.extend([
        "-c:a",
        "aac"
    ])

    # Subtitle
    if subtitle:

        command.extend([
            "-c:s",
            "mov_text"
        ])

    # MP4 optimization
    command.extend([
        "-movflags",
        "+faststart",
        str(output)
    ])

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:

        error = stderr.decode(
            errors="ignore"
        )

        raise RuntimeError(
            f"FFmpeg failed:\n{error[-5000:]}"
        )

    result = Path(output)

    if not result.exists():

        raise RuntimeError(
            "FFmpeg finished but output file "
            "was not created."
        )

    return result


async def merge_video_audio(
    video_file: str,
    audio_file: str | None,
    output_file: str,
    subtitle_file: str | None = None
):

    inputs = [
        video_file
    ]

    if audio_file:
        inputs.append(audio_file)

    if subtitle_file:
        inputs.append(subtitle_file)

    maps = [
        "0:v:0"
    ]

    if audio_file:
        maps.append("1:a:0")
    else:
        maps.append("0:a?")

    if subtitle_file:

        subtitle_index = 2 if audio_file else 1

        maps.append(
            f"{subtitle_index}:0"
        )

    return await run_ffmpeg(
        inputs=inputs,
        output=output_file,
        maps=maps,
        subtitle=bool(subtitle_file)
    )


def get_file_size(path: str) -> int:

    return Path(path).stat().st_size


def get_file_size_gb(path: str) -> float:

    size = get_file_size(path)

    return size / (1024 ** 3)
