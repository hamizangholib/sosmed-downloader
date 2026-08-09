"""
Saveflow API — social media media extractor.

Two endpoints:

  POST /api/extract   metadata + the list of available formats
  GET  /api/download  streams one of those formats back as a file attachment

The download endpoint exists because a browser cannot reliably save a
cross-origin CDN link on its own: the HTML `download` attribute is ignored
cross-origin, and platform CDNs reject requests that arrive without the
`Referer`/`User-Agent` headers yt-dlp negotiated. Streaming through here lets us
replay those headers and set `Content-Disposition: attachment`, which every
browser honours.
"""

import mimetypes
import re
from urllib.parse import quote, urlsplit

import yt_dlp
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from yt_dlp.networking import Request as YdlRequest
from yt_dlp.networking.exceptions import HTTPError as YdlHTTPError

app = FastAPI(
    title="Saveflow API",
    description="Extract direct media links from TikTok, Instagram, Facebook, X and Threads.",
    version="1.1.0",
)

# Frontend is hosted on a different origin (GitHub Pages), so allow every origin.
# The API is read-only and stateless — no cookies or credentials are involved.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Map yt-dlp's extractor key to a friendly platform label for the UI.
PLATFORM_NAMES = {
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "twitter": "X (Twitter)",
    "threads": "Threads",
}

# Options shared by every extraction. `skip_download` guarantees yt-dlp never
# writes media to disk — we only ever want the metadata it resolves.
YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "noplaylist": False,  # Instagram carousels arrive as a playlist of entries.
    "extract_flat": False,
    "socket_timeout": 30,
}

# Adaptive streaming protocols. A browser cannot save these as one file without
# remuxing, so they are only ever offered when nothing else exists.
STREAMING_PROTOCOLS = ("m3u8", "m3u8_native", "http_dash_segments", "mhtml")

IMAGE_EXTS = ("jpg", "jpeg", "png", "webp", "heic", "gif")

CHUNK_SIZE = 64 * 1024


class ExtractRequest(BaseModel):
    url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def friendly_platform(info: dict) -> str:
    """Turn a yt-dlp extractor key such as `TikTok:user` into `TikTok`."""
    key = (info.get("extractor_key") or info.get("extractor") or "").lower()
    for needle, label in PLATFORM_NAMES.items():
        if needle in key:
            return label
    return (info.get("extractor_key") or "Unknown").split(":")[0]


def human_size(num_bytes) -> str | None:
    """Bytes to a short human string. Returns None when the size is unknown."""
    if not num_bytes:
        return None
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def safe_filename(title: str | None, ext: str | None) -> str:
    """Build a filesystem-safe download name. Never returns an empty stem."""
    base = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", str(title or "")).strip()
    base = re.sub(r"\s+", " ", base)[:80].strip() or "saveflow"
    return f"{base}.{ext or 'mp4'}"


def run_extraction(ydl, url: str) -> dict:
    """Run yt-dlp on an existing session and map its failures to HTTP errors."""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Please enter a valid http(s) URL.")

    try:
        info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        message = str(exc)
        lowered = message.lower()
        if "private" in lowered or "login" in lowered or "cookies" in lowered:
            detail = "This post is private or requires a login, so it cannot be fetched."
        elif "unsupported url" in lowered:
            detail = "That link is not supported. Try TikTok, Instagram, Facebook, X or Threads."
        elif "not exist" in lowered or "404" in lowered or "unavailable" in lowered:
            detail = "The post could not be found. It may have been deleted."
        elif "region" in lowered or "geo" in lowered:
            detail = "This post is blocked in the region the server runs from."
        else:
            detail = "Could not extract media from that link. Double-check the URL and try again."
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001 — last-resort guard, never 500 blindly
        raise HTTPException(status_code=500, detail=f"Unexpected server error: {exc}") from exc

    if not info:
        raise HTTPException(status_code=400, detail="No media found at that link.")
    return info


def extract_info(url: str) -> dict:
    """One-shot extraction for callers that do not need the session afterwards."""
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        return run_extraction(ydl, url)


def entries_of(info: dict) -> list[dict]:
    """Flatten a post into a list of media items (carousels have several)."""
    entries = info.get("entries")
    if entries:
        return [e for e in entries if e]
    return [info]


def classify(fmt: dict) -> str | None:
    """
    Decide what a raw yt-dlp format actually is.

    Returns "video", "audio", "image", "stream" (adaptive, not directly
    saveable), or None when the entry carries nothing we can hand to a browser.
    """
    if not fmt.get("url"):
        return None

    if (fmt.get("protocol") or "") in STREAMING_PROTOCOLS:
        return "stream"

    has_video = fmt.get("vcodec") not in (None, "none")
    has_audio = fmt.get("acodec") not in (None, "none")
    ext = (fmt.get("ext") or "").lower()

    if has_video:
        return "video"
    if has_audio:
        return "audio"
    # Photo posts and TikTok slideshows arrive with no codecs at all. They used
    # to be dropped here, which is why some posts reported "nothing to download".
    if ext in IMAGE_EXTS or (fmt.get("width") and not fmt.get("fps")):
        return "image"
    return None


def build_formats(info: dict) -> list[dict]:
    """
    Reduce yt-dlp's format list to the handful worth showing a human.

    Keeps one entry per resolution, prefers progressive files (video+audio in a
    single stream), and appends audio-only and image entries when they exist.
    Adaptive (HLS/DASH) formats are held back and only used when they are the
    only thing available.
    """
    formats = info.get("formats") or []

    if not formats:
        # Some extractors return a bare url with no format list at all.
        if info.get("url"):
            return [
                {
                    "format_id": info.get("format_id") or "0",
                    "url": info["url"],
                    "label": "Original",
                    "ext": info.get("ext") or "mp4",
                    "filesize": human_size(info.get("filesize")),
                    "kind": "image" if (info.get("ext") or "") in IMAGE_EXTS else "video",
                }
            ]
        return []

    best_by_label: dict[str, dict] = {}
    audio_only: dict | None = None
    images: list[dict] = []
    streams: list[dict] = []

    for fmt in formats:
        kind = classify(fmt)
        if kind is None:
            continue

        if kind == "stream":
            streams.append(fmt)
            continue

        if kind == "image":
            images.append(fmt)
            continue

        if kind == "audio":
            if audio_only is None or (fmt.get("abr") or 0) > (audio_only.get("abr") or 0):
                audio_only = fmt
            continue

        height = fmt.get("height")
        label = f"{height}p" if height else (fmt.get("format_note") or "Video")
        candidate = {
            "format_id": str(fmt.get("format_id") or ""),
            "url": fmt["url"],
            "label": label,
            "ext": fmt.get("ext") or "mp4",
            "filesize": human_size(fmt.get("filesize") or fmt.get("filesize_approx")),
            "kind": "video",
            "_progressive": fmt.get("acodec") not in (None, "none"),
            "_height": height or 0,
            "_tbr": fmt.get("tbr") or 0,
        }

        current = best_by_label.get(label)
        # Progressive beats video-only at the same resolution; otherwise take the
        # higher bitrate.
        if (
            current is None
            or (candidate["_progressive"], candidate["_tbr"])
            > (current["_progressive"], current["_tbr"])
        ):
            best_by_label[label] = candidate

    ordered = sorted(best_by_label.values(), key=lambda f: f["_height"], reverse=True)
    result = [{k: v for k, v in fmt.items() if not k.startswith("_")} for fmt in ordered[:6]]

    for img in images[:6]:
        result.append(
            {
                "format_id": str(img.get("format_id") or ""),
                "url": img["url"],
                "label": f"{img['width']}px" if img.get("width") else "Image",
                "ext": img.get("ext") or "jpg",
                "filesize": human_size(img.get("filesize") or img.get("filesize_approx")),
                "kind": "image",
            }
        )

    if audio_only:
        result.append(
            {
                "format_id": str(audio_only.get("format_id") or ""),
                "url": audio_only["url"],
                "label": "Audio only",
                "ext": audio_only.get("ext") or "m4a",
                "filesize": human_size(
                    audio_only.get("filesize") or audio_only.get("filesize_approx")
                ),
                "kind": "audio",
            }
        )

    # Last resort: the post only publishes adaptive streams. Offer the best one
    # rather than failing outright — it will not save as a playable file in every
    # player, so it is labelled honestly.
    if not result and streams:
        best = max(streams, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        result.append(
            {
                "format_id": str(best.get("format_id") or ""),
                "url": best["url"],
                "label": f"{best.get('height') or ''}p stream".strip("p ").strip() or "Stream",
                "ext": best.get("ext") or "mp4",
                "filesize": None,
                "kind": "stream",
            }
        )

    return result


def build_item(info: dict, index: int) -> dict:
    """Shape one media entry (a single post, or one slide of a carousel)."""
    return {
        "index": index,
        "title": info.get("title") or info.get("description") or "Untitled media",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel") or info.get("uploader_id"),
        "formats": build_formats(info),
    }


def pick_raw_format(item: dict, format_id: str | None) -> dict:
    """
    Find the raw yt-dlp format dict to stream.

    The raw dict is what carries `http_headers`, which the CDN requires. Falls
    back to the best available format when the requested id has expired.
    """
    formats = item.get("formats") or []

    if format_id:
        for fmt in formats:
            if str(fmt.get("format_id")) == str(format_id) and fmt.get("url"):
                return fmt

    usable = [f for f in formats if classify(f) in ("video", "image", "audio")]
    if usable:
        return max(
            usable,
            key=lambda f: (
                f.get("acodec") not in (None, "none"),
                f.get("height") or 0,
                f.get("tbr") or 0,
            ),
        )

    if item.get("url"):
        return item

    raise HTTPException(
        status_code=400,
        detail="No downloadable file is available for this post.",
    )


def content_disposition(filename: str) -> str:
    """
    Build an attachment header that survives non-ASCII titles.

    Sends a stripped ASCII name for old clients plus the RFC 5987 `filename*`
    that modern browsers prefer.
    """
    ascii_name = re.sub(r"[^\x20-\x7e]", "_", filename).replace('"', "'")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def healthcheck():
    """Liveness probe — also what a visitor sees when they open the API URL."""
    return {
        "status": "ok",
        "message": "Saveflow API is running.",
        "docs": "/docs",
        "endpoint": "POST /api/extract",
    }


@app.post("/api/extract")
def extract(payload: ExtractRequest):
    url = (payload.url or "").strip()
    info = extract_info(url)

    items = [build_item(entry, i) for i, entry in enumerate(entries_of(info))]
    items = [item for item in items if item["formats"]]

    if not items:
        raise HTTPException(
            status_code=400,
            detail="Media found, but no directly downloadable file is available for it.",
        )

    return {
        "platform": friendly_platform(info),
        "source_url": info.get("webpage_url") or url,
        # Top-level fields mirror the first item so simple clients can ignore
        # `items` entirely.
        "title": items[0]["title"],
        "thumbnail": items[0]["thumbnail"],
        "duration": items[0]["duration"],
        "items": items,
    }


@app.get("/api/download")
def download(
    url: str = Query(..., description="The original post URL, not the CDN link."),
    index: int = Query(0, ge=0, description="Which carousel item to fetch."),
    format_id: str | None = Query(None, description="Format id from /api/extract."),
):
    """
    Stream one media file back to the browser as an attachment.

    Takes the original post URL rather than a CDN link on purpose. Re-resolving
    means the link is always fresh (CDN URLs expire within hours) and it makes
    this endpoint unusable as an open proxy — it can only ever fetch URLs that
    yt-dlp itself produced for a supported platform.

    The fetch goes through the *same* yt-dlp session that did the extraction.
    That matters: platform CDNs validate the cookies handed out during
    extraction (TikTok's `ttwid`, for one), so a hand-rolled request carrying
    only the headers — however correct — still gets a 403.
    """
    ydl = yt_dlp.YoutubeDL(YDL_OPTS)
    try:
        info = run_extraction(ydl, url)
        items = entries_of(info)

        if index >= len(items):
            raise HTTPException(status_code=400, detail="That item is not part of this post.")

        item = items[index]
        fmt = pick_raw_format(item, format_id)
        filename = safe_filename(item.get("title"), fmt.get("ext"))

        headers = {**(info.get("http_headers") or {}), **(fmt.get("http_headers") or {})}
        # Several CDNs (TikTok's especially) reject a plain GET but serve the
        # same file happily for a range request, which is what a browser sends.
        headers.setdefault("Range", "bytes=0-")

        upstream = ydl.urlopen(YdlRequest(fmt["url"], headers=headers))
    except HTTPException:
        ydl.close()
        raise
    except YdlHTTPError as exc:
        ydl.close()
        host = urlsplit(fmt["url"]).hostname if fmt.get("url") else "the CDN"
        raise HTTPException(
            status_code=502,
            detail=(
                f"{host} refused the download (HTTP {exc.status}). "
                "The post may be region-locked, or the platform is blocking this server's IP."
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001 — network failures of every shape
        ydl.close()
        raise HTTPException(
            status_code=502, detail=f"Could not reach the media file: {exc}"
        ) from exc

    def stream():
        try:
            while chunk := upstream.read(CHUNK_SIZE):
                yield chunk
        finally:
            upstream.close()
            ydl.close()

    response_headers = {"Content-Disposition": content_disposition(filename)}
    length = upstream.headers.get("Content-Length")
    if length:
        # Lets the browser show a real progress bar instead of an unknown size.
        response_headers["Content-Length"] = length

    media_type = (
        upstream.headers.get("Content-Type")
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )

    return StreamingResponse(stream(), media_type=media_type, headers=response_headers)


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------

def _self_check() -> None:
    """`python main.py` runs this — smoke test for the format-picking logic."""
    video_info = {
        "extractor_key": "TikTok:user",
        "formats": [
            # Same resolution twice: the progressive one must win.
            {"format_id": "v1", "url": "a", "height": 1080, "vcodec": "h264",
             "acodec": "none", "tbr": 9000, "ext": "mp4"},
            {"format_id": "v2", "url": "b", "height": 1080, "vcodec": "h264",
             "acodec": "mp4a", "tbr": 3000, "ext": "mp4"},
            {"format_id": "v3", "url": "c", "height": 720, "vcodec": "h264",
             "acodec": "mp4a", "tbr": 1500, "ext": "mp4", "filesize": 8_400_000},
            # Adaptive stream: held back while progressive formats exist.
            {"format_id": "hls", "url": "d", "height": 480, "vcodec": "h264",
             "acodec": "mp4a", "protocol": "m3u8_native"},
            {"format_id": "a1", "url": "e", "vcodec": "none", "acodec": "mp4a",
             "abr": 128, "ext": "m4a"},
        ],
    }
    fmts = build_formats(video_info)
    assert [f["label"] for f in fmts] == ["1080p", "720p", "Audio only"], fmts
    assert fmts[0]["url"] == "b", "progressive stream should beat video-only at 1080p"
    assert fmts[0]["format_id"] == "v2"
    assert fmts[1]["filesize"] == "8.0 MB", fmts[1]["filesize"]
    assert fmts[-1]["kind"] == "audio"
    assert friendly_platform(video_info) == "TikTok"

    # Photo posts carry no codecs at all — these used to be dropped, which made
    # TikTok slideshows report "nothing to download".
    photo_info = {
        "formats": [
            {"format_id": "i1", "url": "p1", "ext": "jpg", "width": 1080},
            {"format_id": "i2", "url": "p2", "ext": "webp", "width": 720},
        ]
    }
    photos = build_formats(photo_info)
    assert [f["kind"] for f in photos] == ["image", "image"], photos
    assert photos[0]["label"] == "1080px"

    # HLS-only posts still return something rather than failing outright.
    hls_only = {"formats": [{"format_id": "h", "url": "s", "height": 720,
                             "vcodec": "h264", "acodec": "mp4a",
                             "protocol": "m3u8_native"}]}
    fallback = build_formats(hls_only)
    assert len(fallback) == 1 and fallback[0]["kind"] == "stream", fallback

    # Bare-url extractors.
    assert build_formats({"url": "z", "ext": "jpg"})[0]["kind"] == "image"
    assert build_formats({}) == []

    # Format selection for the download endpoint.
    assert pick_raw_format(video_info, "v3")["url"] == "c"
    assert pick_raw_format(video_info, "gone")["url"] == "b", "falls back to best"

    # Filenames must be safe and never empty, and headers must not be injectable.
    assert safe_filename('a/b:c*d"e', "mp4") == "a b c d e.mp4"
    assert safe_filename("   ", None) == "saveflow.mp4"
    assert safe_filename("x\r\nSet-Cookie: y", "mp4") == "x Set-Cookie y.mp4"
    header = content_disposition(safe_filename("Kucing 🐈 lucu", "mp4"))
    assert "\r" not in header and "\n" not in header
    assert "filename*=UTF-8''" in header

    assert human_size(None) is None
    print("self-check ok")


if __name__ == "__main__":
    _self_check()
