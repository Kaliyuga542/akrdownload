import aiohttp
import xml.etree.ElementTree as ET
from urllib.parse import urljoin


# =========================================================
# DASH MPD DOWNLOADER / ANALYSER
# =========================================================

async def get_mpd(url):
    """
    Download an authorized/unprotected MPEG-DASH MPD.
    """

    timeout = aiohttp.ClientTimeout(
        total=30
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            url
        ) as response:

            response.raise_for_status()

            return await response.text()


# =========================================================
# XML HELPERS
# =========================================================

def strip_namespace(tag):

    if "}" in tag:

        return tag.split(
            "}",
            1
        )[1]

    return tag


def find_children(
    element,
    name
):

    result = []

    for child in list(element):

        if strip_namespace(
            child.tag
        ) == name:

            result.append(
                child
            )

    return result


def find_child(
    element,
    name
):

    for child in list(element):

        if strip_namespace(
            child.tag
        ) == name:

            return child

    return None


def get_attr(
    element,
    name,
    default=None
):

    if element is None:

        return default

    return element.attrib.get(
        name,
        default
    )


# =========================================================
# INTEGER HELPER
# =========================================================

def to_int(
    value,
    default=0
):

    try:

        return int(
            value
        )

    except (
        TypeError,
        ValueError
    ):

        return default


# =========================================================
# RESOLUTION
# =========================================================

def get_resolution(
    representation
):

    width = to_int(
        get_attr(
            representation,
            "width"
        ),
        0
    )

    height = to_int(
        get_attr(
            representation,
            "height"
        ),
        0
    )

    return (
        width,
        height
    )


# =========================================================
# BANDWIDTH
# =========================================================

def get_bandwidth(
    representation,
    adaptation
):

    bandwidth = get_attr(
        representation,
        "bandwidth"
    )

    if bandwidth is None:

        bandwidth = get_attr(
            adaptation,
            "bandwidth"
        )

    return to_int(
        bandwidth,
        0
    )


# =========================================================
# BASE URL
# =========================================================

def get_base_url(
    mpd_url,
    mpd,
    adaptation,
    representation
):

    """
    Resolve MPD -> AdaptationSet -> Representation BaseURL.
    """

    base = mpd_url

    mpd_base = find_child(
        mpd,
        "BaseURL"
    )

    if mpd_base is not None:

        if mpd_base.text:

            base = urljoin(
                base,
                mpd_base.text.strip()
            )

    adaptation_base = find_child(
        adaptation,
        "BaseURL"
    )

    if adaptation_base is not None:

        if adaptation_base.text:

            base = urljoin(
                base,
                adaptation_base.text.strip()
            )

    representation_base = find_child(
        representation,
        "BaseURL"
    )

    if representation_base is not None:

        if representation_base.text:

            base = urljoin(
                base,
                representation_base.text.strip()
            )

    return base


# =========================================================
# CODEC
# =========================================================

def get_codec(
    representation,
    adaptation
):

    codec = get_attr(
        representation,
        "codecs"
    )

    if codec:

        return codec

    return get_attr(
        adaptation,
        "codecs"
    )


# =========================================================
# LANGUAGE
# =========================================================

def get_language(
    adaptation
):

    language = get_attr(
        adaptation,
        "lang"
    )

    if language:

        return language

    return get_attr(
        adaptation,
        "language"
    )


# =========================================================
# AUDIO NAME
# =========================================================

def get_audio_name(
    adaptation,
    index
):

    role = find_child(
        adaptation,
        "Role"
    )

    if role is not None:

        role_value = get_attr(
            role,
            "value"
        )

        if role_value:

            return role_value.title()

    label = get_attr(
        adaptation,
        "label"
    )

    if label:

        return label

    language = get_language(
        adaptation
    )

    if language:

        return language

    return f"Audio {index + 1}"


# =========================================================
# SUBTITLE NAME
# =========================================================

def get_subtitle_name(
    adaptation,
    index
):

    label = get_attr(
        adaptation,
        "label"
    )

    if label:

        return label

    language = get_language(
        adaptation
    )

    if language:

        return language

    return f"Subtitle {index + 1}"


# =========================================================
# REPRESENTATION ID
# =========================================================

def get_representation_id(
    representation
):

    return get_attr(
        representation,
        "id"
    )


# =========================================================
# ANALYSE DASH
# =========================================================

async def analyse(
    url
):

    """
    Analyse an MPEG-DASH MPD.

    Returns:

        videos
        audios
        subtitles

    Same general structure as bot.hls.analyse()
    so the rest of the bot can use the same
    selection workflow.
    """

    text = await get_mpd(
        url
    )

    try:

        root = ET.fromstring(
            text
        )

    except ET.ParseError as error:

        raise RuntimeError(
            "Invalid DASH MPD XML:\n"
            + str(error)
        )

    videos = []
    audios = []
    subtitles = []

    video_id = 0
    audio_id = 0
    subtitle_id = 0

    # =====================================================
    # ADAPTATION SETS
    # =====================================================

    adaptation_sets = []

    for child in list(root):

        if strip_namespace(
            child.tag
        ) == "Period":

            adaptation_sets.extend(
                find_children(
                    child,
                    "AdaptationSet"
                )
            )

    # =====================================================
    # PROCESS ADAPTATION SETS
    # =====================================================

    for adaptation in adaptation_sets:

        content_type = get_attr(
            adaptation,
            "contentType"
        )

        mime_type = get_attr(
            adaptation,
            "mimeType",
            ""
        )

        content_type = (
            content_type or ""
        ).lower()

        mime_type = (
            mime_type or ""
        ).lower()

        # -------------------------------------------------
        # REPRESENTATIONS
        # -------------------------------------------------

        representations = find_children(
            adaptation,
            "Representation"
        )

        # =================================================
        # VIDEO
        # =================================================

        is_video = (
            content_type == "video"
            or mime_type.startswith(
                "video/"
            )
        )

        if is_video:

            for representation in representations:

                resolution = get_resolution(
                    representation
                )

                bandwidth = get_bandwidth(
                    representation,
                    adaptation
                )

                representation_id = (
                    get_representation_id(
                        representation
                    )
                )

                base_url = get_base_url(
                    url,
                    root,
                    adaptation,
                    representation
                )

                videos.append({

                    "id": video_id,

                    "uri": url,

                    "base_url": base_url,

                    "representation_id": (
                        representation_id
                    ),

                    "bandwidth": bandwidth,

                    "resolution": resolution,

                    "codec": get_codec(
                        representation,
                        adaptation
                    ),

                    "mime_type": mime_type,

                    "type": "video",
                })

                video_id += 1

            continue

        # =================================================
        # AUDIO
        # =================================================

        is_audio = (
            content_type == "audio"
            or mime_type.startswith(
                "audio/"
            )
        )

        if is_audio:

            language = get_language(
                adaptation
            )

            name = get_audio_name(
                adaptation,
                audio_id
            )

            for representation in representations:

                bandwidth = get_bandwidth(
                    representation,
                    adaptation
                )

                representation_id = (
                    get_representation_id(
                        representation
                    )
                )

                base_url = get_base_url(
                    url,
                    root,
                    adaptation,
                    representation
                )

                audios.append({

                    "id": audio_id,

                    "uri": url,

                    "base_url": base_url,

                    "representation_id": (
                        representation_id
                    ),

                    "name": name,

                    "language": language,

                    "bandwidth": bandwidth,

                    "codec": get_codec(
                        representation,
                        adaptation
                    ),

                    "mime_type": mime_type,

                    "type": "audio",
                })

                audio_id += 1

            continue

        # =================================================
        # SUBTITLE / TEXT
        # =================================================

        is_subtitle = (
            content_type == "text"
            or mime_type.startswith(
                "text/"
            )
            or mime_type.startswith(
                "application/ttml"
            )
            or mime_type.startswith(
                "application/mp4"
            )
        )

        if is_subtitle:

            language = get_language(
                adaptation
            )

            name = get_subtitle_name(
                adaptation,
                subtitle_id
            )

            # Some MPDs have no Representation
            # for text tracks.
            if representations:

                for representation in representations:

                    representation_id = (
                        get_representation_id(
                            representation
                        )
                    )

                    base_url = get_base_url(
                        url,
                        root,
                        adaptation,
                        representation
                    )

                    subtitles.append({

                        "id": subtitle_id,

                        "uri": url,

                        "base_url": base_url,

                        "representation_id": (
                            representation_id
                        ),

                        "name": name,

                        "language": language,

                        "mime_type": mime_type,

                        "type": "subtitle",
                    })

                    subtitle_id += 1

            else:

                subtitles.append({

                    "id": subtitle_id,

                    "uri": url,

                    "base_url": url,

                    "representation_id": None,

                    "name": name,

                    "language": language,

                    "mime_type": mime_type,

                    "type": "subtitle",
                })

                subtitle_id += 1

    return (
        videos,
        audios,
        subtitles
    )


# =========================================================
# CHECK DASH URL
# =========================================================

def is_dash_url(
    url
):

    if not url:

        return False

    return (
        ".mpd" in url.lower()
    )
