"""
Saveflow API — social media media extractor.

Wraps yt-dlp metadata extraction (download=False) behind a small JSON API so a
static frontend on GitHub Pages can resolve direct media URLs without shipping
yt-dlp to the browser.
"""

import re

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="Saveflow API",
    description="Extract direct media links from TikTok, Instagram, Facebook, X and Threads.",
    version="1.0.0",
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

# Options shared by every extraction. `quiet` keeps container logs readable and
# `skip_download` guarantees we never write media to the Space's ephemeral disk.
YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "noplaylist": False,  # Instagram carousels arrive as a playlist of entries.
    "extract_flat": False,
}


class ExtractRequest(BaseModel):
    url: str


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


def build_formats(info: dict) -> list[dict]:
    """
    Reduce yt-dlp's format list to the handful worth showing a human.

    Keeps one entry per resolution, prefers progressive files (video+audio in a
    single stream) because those play straight from a browser download, and
    always appends an audio-only option when one exists.
    """
    formats = info.get("formats") or []
    if not formats:
        # Some extractors (single images, simple TikTok posts) return a bare url.
        if info.get("url"):
            return [
                {
                    "url": info["url"],
                    "label": "Original",
                    "ext": info.get("ext") or "mp4",
                    "filesize": human_size(info.get("filesize")),
                    "kind": "video",
                }
            ]
        return []

    best_by_label: dict[str, dict] = {}
    audio_only: dict | None = None

    for fmt in formats:
        url = fmt.get("url")
        if not url or fmt.get("protocol") in ("m3u8", "m3u8_native", "mhtml"):
            # HLS manifests are not directly downloadable from a browser.
            continue

        has_video = fmt.get("vcodec") not in (None, "none")
        has_audio = fmt.get("acodec") not in (None, "none")

        if not has_video and has_audio:
            if audio_only is None or (fmt.get("abr") or 0) > (audio_only.get("abr") or 0):
                audio_only = fmt
            continue

        if not has_video:
            continue

        height = fmt.get("height")
        label = f"{height}p" if height else (fmt.get("format_note") or "Video")
        candidate = {
            "url": url,
            "label": label,
            "ext": fmt.get("ext") or "mp4",
            "filesize": human_size(fmt.get("filesize") or fmt.get("filesize_approx")),
            "kind": "video",
            "_progressive": has_audio,
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
    result = [
        {k: v for k, v in fmt.items() if not k.startswith("_")} for fmt in ordered[:6]
    ]

    if audio_only:
        result.append(
            {
                "url": audio_only["url"],
                "label": "Audio only",
                "ext": audio_only.get("ext") or "m4a",
                "filesize": human_size(
                    audio_only.get("filesize") or audio_only.get("filesize_approx")
                ),
                "kind": "audio",
            }
        )

    return result


def build_item(info: dict) -> dict:
    """Shape one media entry (a single post, or one slide of a carousel)."""
    return {
        "title": info.get("title") or info.get("description") or "Untitled media",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel") or info.get("uploader_id"),
        "formats": build_formats(info),
    }


@app.get("/")
def healthcheck():
    """Liveness probe — also what a visitor sees when they open the Space URL."""
    return {
        "status": "ok",
        "message": "Saveflow API is running.",
        "docs": "/docs",
        "endpoint": "POST /api/extract",
    }


@app.post("/api/extract")
def extract(payload: ExtractRequest):
    url = (payload.url or "").strip()

    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Please enter a valid http(s) URL.")

    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        # yt-dlp packs the real reason into the message; map it to something a
        # human can act on instead of leaking a stack trace.
        message = str(exc)
        lowered = message.lower()
        if "private" in lowered or "login" in lowered or "cookies" in lowered:
            detail = "This post is private or requires a login, so it cannot be fetched."
        elif "unsupported url" in lowered:
            detail = "That link is not supported. Try TikTok, Instagram, Facebook, X or Threads."
        elif "not exist" in lowered or "404" in lowered or "unavailable" in lowered:
            detail = "The post could not be found. It may have been deleted."
        else:
            detail = "Could not extract media from that link. Double-check the URL and try again."
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001 — last-resort guard, never 500 blindly
        raise HTTPException(
            status_code=500, detail=f"Unexpected server error: {exc}"
        ) from exc

    if not info:
        raise HTTPException(status_code=400, detail="No media found at that link.")

    entries = info.get("entries")
    if entries:
        items = [build_item(e) for e in entries if e]
    else:
        items = [build_item(info)]

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


def _self_check() -> None:
    """`python main.py` runs this — smoke test for the format-picking logic."""
    info = {
        "extractor_key": "TikTok:user",
        "formats": [
            # Same resolution twice: the progressive one must win.
            {"url": "a", "height": 1080, "vcodec": "h264", "acodec": "none", "tbr": 9000, "ext": "mp4"},
            {"url": "b", "height": 1080, "vcodec": "h264", "acodec": "mp4a", "tbr": 3000, "ext": "mp4"},
            {"url": "c", "height": 720, "vcodec": "h264", "acodec": "mp4a", "tbr": 1500, "ext": "mp4",
             "filesize": 8_400_000},
            # HLS manifests are not browser-downloadable and must be dropped.
            {"url": "d", "height": 480, "vcodec": "h264", "acodec": "mp4a", "protocol": "m3u8_native"},
            {"url": "e", "vcodec": "none", "acodec": "mp4a", "abr": 128, "ext": "m4a"},
        ],
    }
    fmts = build_formats(info)
    labels = [f["label"] for f in fmts]
    assert labels == ["1080p", "720p", "Audio only"], labels
    assert fmts[0]["url"] == "b", "progressive stream should beat video-only at 1080p"
    assert fmts[1]["filesize"] == "8.0 MB", fmts[1]["filesize"]
    assert fmts[-1]["kind"] == "audio"
    assert friendly_platform(info) == "TikTok"
    assert build_formats({"url": "z", "ext": "jpg"})[0]["label"] == "Original"
    assert human_size(None) is None
    print("self-check ok")


if __name__ == "__main__":
    _self_check()
