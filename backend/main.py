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

import ast
import ipaddress
import json
import mimetypes
import os
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import Cookie
from html.parser import HTMLParser
from urllib.error import HTTPError as UrlHTTPError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yt_dlp
from curl_cffi import requests as curl_requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from yt_dlp.networking import Request as YdlRequest
from yt_dlp.networking.exceptions import HTTPError as YdlHTTPError
from yt_dlp.version import __version__ as YT_DLP_VERSION

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
    "streamrizz": "Video",
    "vid3y": "Video",
    "directmedia": "Media",
    "publicpage": "Web",
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
    # YouTube now serves JavaScript challenges that yt-dlp must solve. Vercel's
    # runtime includes Node; the matching EJS scripts come from yt-dlp[default].
    "js_runtimes": {"node": {}},
    # mweb still exposes a progressive MP4 for public videos. Without it, the
    # default client may return only separate video/audio tracks that this
    # streaming API cannot merge without writing a large temporary file.
    "extractor_args": {"youtube": {"player_client": ["mweb", "default"]}},
}

# Adaptive streaming protocols. A browser cannot save these as one file without
# remuxing, so they are only ever offered when nothing else exists.
STREAMING_PROTOCOLS = ("m3u8", "m3u8_native", "http_dash_segments", "mhtml")

IMAGE_EXTS = ("jpg", "jpeg", "png", "webp", "heic", "gif", "avif", "bmp", "tif", "tiff", "svg")
VIDEO_EXTS = ("mp4", "webm", "mov", "m4v", "mkv", "avi", "m3u8")

CHUNK_SIZE = 64 * 1024
MAX_HTML_BYTES = 2_000_000
MAX_STREAMRIZZ_FOLDER_PAGES = 25
MAX_STREAMRIZZ_FOLDER_ENTRIES = 500


class ExtractRequest(BaseModel):
    url: str


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


class MediaPageParser(HTMLParser):
    """Collect public video hints without executing page JavaScript."""

    META_VIDEO = {
        "og:video", "og:video:url", "og:video:secure_url",
        "twitter:player:stream",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.media: list[tuple[str, str | None]] = []
        self.thumbnail: str | None = None
        self.title_parts: list[str] = []
        self.json_ld: list[str] = []
        self._in_title = False
        self._json_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag in ("video", "source"):
            src = values.get("src") or values.get("data-src")
            if src:
                self.media.append((src, values.get("type")))
            if tag == "video" and values.get("poster") and not self.thumbnail:
                self.thumbnail = values["poster"]
        elif tag == "img" and not self.thumbnail:
            classes = set((values.get("class") or "").lower().split())
            alt = (values.get("alt") or "").lower()
            if "thumbnail" in classes or alt == "thumbnail":
                self.thumbnail = values.get("src") or values.get("data-src")
        elif tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content")
            if content and key in self.META_VIDEO:
                self.media.append((content, values.get("type")))
            elif content and key in ("og:image", "twitter:image") and not self.thumbnail:
                self.thumbnail = content
        elif tag == "link":
            rel = (values.get("rel") or "").lower().split()
            if "preload" in rel and (values.get("as") or "").lower() == "video":
                if values.get("href"):
                    self.media.append((values["href"], values.get("type")))
        elif tag == "script" and "ld+json" in (values.get("type") or "").lower():
            self._json_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._json_parts is not None:
            self._json_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._json_parts is not None:
            self.json_ld.append("".join(self._json_parts))
            self._json_parts = None


class StreamrizzFolderParser(HTMLParser):
    """Collect the file links and thumbnails already rendered by a folder page."""

    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.entries: list[dict] = []
        self.folders: list[dict] = []
        self.page_urls: list[str] = []
        self.parent_url: str | None = None
        self._in_title = False
        self._in_name = False
        self._in_folder = False
        self._name_parts: list[str] = []
        self._folder_parts: list[str] = []
        self._folder: dict | None = None
        self._entry: dict | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "title":
            self._in_title = True
        elif tag == "a" and "back-btn" in classes and values.get("href"):
            self.parent_url = values["href"]
        elif tag == "a" and "page-btn" in classes and values.get("href"):
            self.page_urls.append(values["href"])
        elif tag == "a" and "folder-chip" in classes and values.get("href"):
            self._in_folder = True
            self._folder_parts = []
            self._folder = {"url": values["href"]}
        elif tag == "article" and "drive-file-card" in classes:
            self._entry = {}
        elif self._entry is not None:
            if tag == "a" and "thumb-link" in classes and values.get("href"):
                self._entry["url"] = values["href"]
            elif tag == "img" and values.get("src"):
                self._entry["thumbnail"] = values["src"]
            elif tag == "a" and "file-name" in classes:
                self._in_name = True
                self._name_parts = []
                if values.get("title"):
                    self._entry["title"] = values["title"]

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_name:
            self._name_parts.append(data)
        if self._in_folder:
            self._folder_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._in_name:
            self._in_name = False
            if self._entry is not None and not self._entry.get("title"):
                self._entry["title"] = "".join(self._name_parts).strip()
        elif tag == "a" and self._in_folder:
            self._in_folder = False
            if self._folder is not None:
                self._folder["title"] = "".join(self._folder_parts).strip()
                if self._folder["title"]:
                    self.folders.append(self._folder)
            self._folder = None
        elif tag == "article" and self._entry is not None:
            if self._entry.get("url"):
                self.entries.append(self._entry)
            self._entry = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_x_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in ("x.com", "twitter.com") or host.endswith((".x.com", ".twitter.com"))


def ensure_public_url(url: str) -> str:
    """Reject credentials, unusual ports, and hosts resolving outside public internet."""
    if len(url) > 4096:
        raise HTTPException(status_code=400, detail="That URL is too long.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(status_code=400, detail="Please enter a valid public http(s) URL.")
    if parts.username or parts.password:
        raise HTTPException(status_code=400, detail="URLs containing login credentials are not allowed.")
    try:
        port = parts.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="That URL contains an invalid port.") from exc
    if port not in (None, 80, 443):
        raise HTTPException(status_code=400, detail="Only standard HTTP and HTTPS ports are allowed.")

    try:
        addresses = {
            ipaddress.ip_address(item[4][0].split("%", 1)[0])
            for item in socket.getaddrinfo(
                parts.hostname,
                port or (443 if parts.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="That hostname could not be resolved.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise HTTPException(
            status_code=400,
            detail="Private, local, and reserved network addresses are not allowed.",
        )
    return url


def fetch_public_html(url: str) -> tuple[str, str]:
    """Fetch a small HTML page while validating every redirect target."""
    opener = build_opener(NoRedirect())
    current = url
    for _ in range(6):
        ensure_public_url(current)
        request = Request(current, headers={
            "User-Agent": yt_dlp.utils.std_headers["User-Agent"],
            "Accept": "text/html,application/xhtml+xml",
        })
        try:
            response = opener.open(request, timeout=30)
        except UrlHTTPError as exc:
            location = exc.headers.get("Location")
            status = exc.code
            exc.close()
            if status in (301, 302, 303, 307, 308) and location:
                current = urljoin(current, location)
                continue
            raise yt_dlp.utils.DownloadError(
                f"The public page returned HTTP {status}."
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise yt_dlp.utils.DownloadError(f"Could not fetch the public page: {exc}") from exc

        try:
            final_url = response.geturl()
            ensure_public_url(final_url)
            content_type = (response.headers.get("Content-Type") or "").lower()
            if content_type and "html" not in content_type and "xhtml" not in content_type:
                raise yt_dlp.utils.DownloadError("The URL is not an HTML video page.")
            body = response.read(MAX_HTML_BYTES + 1)
            if len(body) > MAX_HTML_BYTES:
                raise yt_dlp.utils.DownloadError("The page is too large to inspect safely.")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                page = body.decode(charset, "replace")
            except LookupError:
                page = body.decode("utf-8", "replace")
            return final_url, page
        finally:
            response.close()

    raise yt_dlp.utils.DownloadError("The page redirected too many times.")


def json_media_urls(value) -> list[str]:
    found = []
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            found.extend(
                child for key, child in item.items()
                if key.lower() == "contenturl" and isinstance(child, str)
            )
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return found


def parse_public_media(page_url: str, page: str) -> dict:
    parser = MediaPageParser()
    parser.feed(page)
    for payload in parser.json_ld:
        try:
            parser.media.extend((url, None) for url in json_media_urls(json.loads(payload)))
        except (json.JSONDecodeError, TypeError):
            continue

    formats = []
    seen = set()
    for raw_url, mime_type in parser.media[:12]:
        if len(raw_url) > 4096:
            continue
        media_url = urljoin(page_url, raw_url.strip())
        if media_url in seen:
            continue
        try:
            ensure_public_url(media_url)
        except HTTPException:
            continue

        mime = (mime_type or "").split(";", 1)[0].lower()
        ext = (urlsplit(media_url).path.rsplit(".", 1)[-1] or "").lower()
        if ext not in VIDEO_EXTS:
            ext = {
                "video/webm": "webm",
                "video/quicktime": "mov",
                "application/vnd.apple.mpegurl": "m3u8",
                "application/x-mpegurl": "m3u8",
            }.get(mime, "mp4")

        seen.add(media_url)
        formats.append({
            "format_id": f"public-{len(formats) + 1}",
            "format_note": "HLS" if ext == "m3u8" else ext.upper(),
            "url": media_url,
            "ext": ext,
            "protocol": "m3u8_native" if ext == "m3u8" else urlsplit(media_url).scheme,
            "vcodec": "unknown",
            "acodec": "unknown",
            "http_headers": {"Referer": page_url},
        })

    thumbnail = urljoin(page_url, parser.thumbnail) if parser.thumbnail else None
    if thumbnail:
        try:
            ensure_public_url(thumbnail)
        except HTTPException:
            thumbnail = None
    # Open Graph/Twitter images are the most reliable generic signal that an
    # otherwise unknown page is an image post. Do not also expose a video
    # poster as a photo when a real video source was already discovered.
    if not formats and thumbnail:
        ext = direct_image_ext(thumbnail) or "jpg"
        formats.append({
            "format_id": "public-image-1",
            "format_note": "Image",
            "url": thumbnail,
            "ext": ext,
            "protocol": urlsplit(thumbnail).scheme,
            "vcodec": "none",
            "acodec": "none",
            "http_headers": {"Referer": page_url},
        })
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()[:300] or "Untitled media"
    return {
        "id": urlsplit(page_url).path.rstrip("/").rsplit("/", 1)[-1] or "public-video",
        "title": title,
        "thumbnail": thumbnail,
        "webpage_url": page_url,
        "extractor": "generic-public-page",
        "extractor_key": "PublicPage",
        "formats": formats,
        "_page_preview_url": page_url if not formats else None,
    }


def extract_public_page(url: str) -> dict:
    return parse_public_media(*fetch_public_html(url))


def is_streamrizz_url(url: str) -> bool:
    parts = urlsplit(url)
    return (
        (parts.hostname or "").lower() in ("streamrizz.com", "www.streamrizz.com")
        and re.fullmatch(r"/[de]/[A-Za-z0-9_-]+/?", parts.path) is not None
    )


def streamrizz_folder_id(url: str) -> str | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in (
        "streamrizz.com",
        "www.streamrizz.com",
        "tribunvideo.com",
        "www.tribunvideo.com",
    ):
        return None
    match = re.fullmatch(r"/f/([A-Za-z0-9_-]+)/?", parts.path)
    return match.group(1) if match else None


def streamrizz_folder_page_numbers(folder_url: str, page_urls: list[str]) -> list[int]:
    """Return the bounded page range advertised by one Streamrizz folder page."""
    folder_id = streamrizz_folder_id(folder_url)
    numbers = {1}
    for raw_url in page_urls:
        page_url = urljoin(folder_url, raw_url)
        if streamrizz_folder_id(page_url) != folder_id:
            continue
        values = parse_qs(urlsplit(page_url).query).get("p", [])
        if len(values) != 1 or not values[0].isdigit():
            continue
        number = int(values[0])
        if 1 <= number <= MAX_STREAMRIZZ_FOLDER_PAGES:
            numbers.add(number)
    return list(range(1, min(max(numbers), MAX_STREAMRIZZ_FOLDER_PAGES) + 1))


def videeyss_id(url: str) -> str | None:
    """Return the public clip id used by Videeyss' click-to-load player."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in ("videeyss.shop", "www.videeyss.shop"):
        return None
    match = re.fullmatch(r"/([A-Za-z0-9_-]+)/?", parts.path)
    return match.group(1) if match else None


def aceimg_id(url: str) -> str | None:
    """Return the clip id shared by Aceimg and Slicdrve pages."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in (
        "aceimg.ink", "www.aceimg.ink", "slicdrve.ink", "www.slicdrve.ink",
    ):
        return None
    match = re.fullmatch(r"/([A-Za-z0-9_-]+)/?", parts.path)
    return match.group(1) if match else None


def aceimg_cdn_page(url: str) -> str | None:
    """Map an AceImg CDN file back to its interactive public player page."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in (
        "cdn.aceimg.com", "www.cdn.aceimg.com",
        "cdn2.aceimg.com", "www.cdn2.aceimg.com",
    ):
        return None
    match = re.fullmatch(r"/([A-Za-z0-9_-]+)\.mp4", parts.path, re.IGNORECASE)
    return f"https://aceimg.ink/{match.group(1)}" if match else None


def is_acegimg_url(url: str) -> bool:
    parts = urlsplit(url)
    return (
        (parts.hostname or "").lower() in ("cdn2.acegimg.com", "www.cdn2.acegimg.com")
        and re.fullmatch(r"/[A-Za-z0-9_-]+\.[A-Za-z0-9]+", parts.path) is not None
    )


def aceimg_upload_media(url: str) -> tuple[str, str] | None:
    """Resolve AceImg's upload page query to its public media CDN URL."""
    parts = urlsplit(url)
    if (
        (parts.hostname or "").lower() not in ("aceimg.com", "www.aceimg.com")
        or parts.path.rstrip("/") != "/upload"
    ):
        return None
    filename = (parse_qs(parts.query).get("f") or [None])[0]
    extensions = "|".join((*IMAGE_EXTS, *VIDEO_EXTS))
    match = re.fullmatch(
        rf"([A-Za-z0-9_-]+)\.({extensions})",
        filename or "",
        re.IGNORECASE,
    )
    return (
        match.group(1), f"https://cdn.aceimg.com/{filename}"
    ) if match else None


def extract_acegimg(ydl, url: str) -> dict:
    final_url, _ = fetch_public_html(url)
    resolved = aceimg_upload_media(final_url)
    if not resolved:
        raise yt_dlp.utils.DownloadError("Acegimg did not redirect to a media file.")
    _, media_url = resolved
    info = probe_direct_media(ydl, media_url)
    if not info:
        raise yt_dlp.utils.DownloadError("Acegimg media is unavailable.")
    return {
        **info,
        "webpage_url": url,
        "extractor": "acegimg",
        "extractor_key": "Acegimg",
    }


def vid3y_media(url: str) -> tuple[str, str] | None:
    """Resolve Vid3y's click-through page to its predictable media CDN URL."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in ("cdn.vid3y.my.id", "www.cdn.vid3y.my.id"):
        match = re.fullmatch(r"/([A-Za-z0-9_-]+)/?", parts.path)
        clip_id = match.group(1) if match else None
        source = "videy"
    elif host in ("vid3y.my.id", "www.vid3y.my.id"):
        clip_id = (parse_qs(parts.query).get("v") or [None])[0]
        source = (parse_qs(parts.query).get("sumber") or ["videy"])[0].lower()
    else:
        return None
    if not clip_id or not re.fullmatch(r"[A-Za-z0-9_-]+", clip_id):
        return None
    bases = {
        "videy": "https://cdn2.videy.co/",
        "slicedrive": "https://cdn.slicerdive.online/",
        "box": "https://box.vchod.cc/videos/",
        "aceimg": "https://cdn.aceimg.com/",
        "aceimg2": "https://cdn2.aceimg.com/",
    }
    base = bases.get(source)
    return (clip_id, f"{base}{clip_id}.mp4") if base else None


def direct_image_ext(url: str) -> str | None:
    ext = (urlsplit(url).path.rsplit(".", 1)[-1] or "").lower()
    return ext if ext in IMAGE_EXTS else None


def probe_direct_media(ydl, url: str) -> dict | None:
    """Return media metadata when a URL itself responds with image/video bytes."""
    ensure_public_url(url)
    try:
        response = ydl.urlopen(YdlRequest(url, headers={"Range": "bytes=0-0"}))
        try:
            ensure_public_url(response.url)
            content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
            kind = "image" if content_type.startswith("image/") else (
                "video" if content_type.startswith("video/") else None
            )
            path_ext = (urlsplit(response.url).path.rsplit(".", 1)[-1] or "").lower()
            if kind is None and content_type not in ("text/html", "application/xhtml+xml"):
                if path_ext in IMAGE_EXTS:
                    kind = "image"
                elif path_ext in VIDEO_EXTS:
                    kind = "video"
            if kind is None:
                return None
            ext = path_ext if path_ext in (*IMAGE_EXTS, *VIDEO_EXTS) else (
                mimetypes.guess_extension(content_type or "") or f".{kind}"
            ).lstrip(".")
            content_range = response.headers.get("Content-Range") or ""
            size_match = re.search(r"/(\d+)$", content_range)
            filesize = int(size_match.group(1)) if size_match else None
            media_url = response.url
        finally:
            response.close()
    except HTTPException:
        raise
    except Exception:
        # A failed probe must not prevent the site extractor or HTML scanner
        # from trying the same URL through their normal paths.
        return None
    filename = urlsplit(media_url).path.rstrip("/").rsplit("/", 1)[-1] or f"media.{ext}"
    if "." not in filename:
        filename = f"{filename}.{ext}"
    return {
        "id": filename.rsplit(".", 1)[0],
        "title": filename,
        "thumbnail": media_url if kind == "image" else None,
        "webpage_url": url,
        "extractor": "direct-media",
        "extractor_key": "DirectMedia",
        "url": media_url,
        "ext": ext,
        "filesize": filesize,
        "vcodec": "none" if kind == "image" else "unknown",
        "acodec": "none" if kind == "image" else "unknown",
    }


def extract_direct_mp4(
    ydl,
    page_url: str,
    clip_id: str,
    media_url: str,
    extractor_key: str,
) -> dict:
    """Probe a predictable public MP4 and return normal yt-dlp-shaped metadata."""
    ensure_public_url(media_url)
    try:
        response = ydl.urlopen(YdlRequest(media_url, headers={
            "Referer": page_url,
            "Range": "bytes=0-0",
        }))
        try:
            ensure_public_url(response.url)
            content_type = (response.headers.get("Content-Type") or "").lower()
            if not content_type.startswith("video/"):
                raise yt_dlp.utils.DownloadError(f"{extractor_key} did not return a video file.")
            content_range = response.headers.get("Content-Range") or ""
            size_match = re.search(r"/(\d+)$", content_range)
            filesize = int(size_match.group(1)) if size_match else None
        finally:
            response.close()
    except HTTPException:
        raise
    except yt_dlp.utils.DownloadError:
        raise
    except Exception as exc:
        raise yt_dlp.utils.DownloadError(f"Could not resolve {extractor_key} media: {exc}") from exc

    return {
        "id": clip_id,
        "title": clip_id,
        "webpage_url": page_url,
        "extractor": extractor_key.lower(),
        "extractor_key": extractor_key,
        "formats": [{
            "format_id": f"{extractor_key.lower()}-mp4",
            "format_note": "MP4",
            "url": media_url,
            "ext": "mp4",
            "protocol": "https",
            "vcodec": "unknown",
            "acodec": "unknown",
            "filesize": filesize,
            "http_headers": {"Referer": page_url},
        }],
    }


def extract_videeyss(ydl, url: str) -> dict:
    """Resolve the MP4 that Videeyss inserts only after its Play button is clicked."""
    clip_id = videeyss_id(url)
    if not clip_id:
        raise yt_dlp.utils.DownloadError("Invalid Videeyss video URL.")
    return extract_direct_mp4(
        ydl, url, clip_id, f"https://cdn2.videy.co/{clip_id}.mp4", "Videeyss"
    )


def extract_aceimg(ydl, url: str) -> dict:
    """Resolve the MP4 that Aceimg and Slicdrve construct from the page path."""
    clip_id = aceimg_id(url)
    if not clip_id:
        raise yt_dlp.utils.DownloadError("Invalid Aceimg/Slicdrve video URL.")
    return extract_direct_mp4(
        ydl, url, clip_id, f"https://cdn2.aceimg.com/{clip_id}.mp4", "Aceimg"
    )


def extract_vid3y(ydl, url: str) -> dict:
    resolved = vid3y_media(url)
    if not resolved:
        raise yt_dlp.utils.DownloadError("Invalid Vid3y video URL.")
    clip_id, media_url = resolved
    return extract_direct_mp4(ydl, url, clip_id, media_url, "Vid3y")


def js_string(page: str, name: str) -> str:
    match = re.search(
        rf"\b(?:const|let|var)\s+{re.escape(name)}\s*=\s*(['\"])(.*?)\1",
        page,
        re.DOTALL,
    )
    if not match:
        raise yt_dlp.utils.DownloadError(f"Streamrizz did not provide {name}.")
    try:
        return ast.literal_eval(f"{match.group(1)}{match.group(2)}{match.group(1)}")
    except (SyntaxError, ValueError) as exc:
        raise yt_dlp.utils.DownloadError(f"Streamrizz returned an invalid {name}.") from exc


def read_page(
    ydl,
    url: str,
    referer: str | None = None,
    headers: dict | None = None,
) -> str:
    request_headers = {**(headers or {})}
    if referer:
        request_headers["Referer"] = referer
    response = None
    for attempt in range(3):
        try:
            response = ydl.urlopen(YdlRequest(url, headers=request_headers))
            break
        except YdlHTTPError as exc:
            if exc.status not in (429, 500, 502, 503, 504) or attempt == 2:
                raise yt_dlp.utils.DownloadError(
                    f"The media page returned HTTP {exc.status}."
                ) from exc
            time.sleep(0.2 * (attempt + 1))
    if response is None:
        raise yt_dlp.utils.DownloadError("Could not fetch the media page.")
    try:
        return response.read(MAX_HTML_BYTES).decode("utf-8", "replace")
    finally:
        response.close()


def extract_streamrizz_folder(ydl, url: str) -> dict:
    """Expose a folder as lightweight entries; resolve only the chosen video later."""
    folder_id = streamrizz_folder_id(url)
    if not folder_id:
        raise yt_dlp.utils.DownloadError("Invalid Streamrizz folder URL.")

    folder_url = f"https://streamrizz.com/f/{folder_id}"
    parser = StreamrizzFolderParser()
    parser.feed(read_page(ydl, folder_url, url))

    parsers = [parser]
    for page_number in streamrizz_folder_page_numbers(folder_url, parser.page_urls)[1:]:
        page_parser = StreamrizzFolderParser()
        page_parser.feed(read_page(ydl, f"{folder_url}?p={page_number}", folder_url))
        parsers.append(page_parser)

    entries = []
    seen_entries = set()
    raw_entries = (raw for page_parser in parsers for raw in page_parser.entries)
    for raw in raw_entries:
        video_page = urljoin(folder_url, raw["url"])
        if not is_streamrizz_url(video_page) or video_page in seen_entries:
            continue
        raw_thumbnail = raw.get("thumbnail")
        thumbnail = urljoin(folder_url, raw_thumbnail) if raw_thumbnail else None
        try:
            ensure_public_url(video_page)
            if thumbnail:
                ensure_public_url(thumbnail)
        except HTTPException:
            continue
        seen_entries.add(video_page)
        video_id = urlsplit(video_page).path.rstrip("/").rsplit("/", 1)[-1]
        format_id = f"streamrizz-{video_id}"
        entries.append({
            "id": video_id,
            "title": raw.get("title") or video_id,
            "thumbnail": thumbnail,
            "_thumbnail_proxy": False,
            "webpage_url": video_page,
            "_streamrizz_url": video_page,
            "_streamrizz_format_id": format_id,
            "formats": [{
                "format_id": format_id,
                "format_note": "MP4",
                "url": video_page,
                "ext": "mp4",
                "protocol": "https",
                "vcodec": "unknown",
                "acodec": "unknown",
            }],
        })
        if len(entries) >= MAX_STREAMRIZZ_FOLDER_ENTRIES:
            break

    folders = []
    seen_folders = set()
    raw_folders = (raw for page_parser in parsers for raw in page_parser.folders)
    for raw in raw_folders:
        child_url = urljoin(folder_url, raw["url"])
        if not streamrizz_folder_id(child_url) or child_url in seen_folders:
            continue
        try:
            ensure_public_url(child_url)
        except HTTPException:
            continue
        seen_folders.add(child_url)
        folders.append({"title": raw["title"], "url": child_url})
        if len(folders) >= 100:
            break

    parent_path = next((page_parser.parent_url for page_parser in parsers if page_parser.parent_url), None)
    parent_url = urljoin(folder_url, parent_path) if parent_path else None
    if parent_url and not streamrizz_folder_id(parent_url):
        parent_url = None

    if not entries and not folders:
        raise yt_dlp.utils.DownloadError("No media or subfolders were found in that folder.")
    title = re.sub(r"^\s*📂\s*", "", "".join(parser.title_parts)).strip()
    return {
        "id": folder_id,
        "title": title or "Video folder",
        "webpage_url": url,
        "extractor": "streamrizz:folder",
        "extractor_key": "StreamrizzFolder",
        "entries": entries,
        "_folders": folders,
        "_parent_url": parent_url,
    }


def extract_streamrizz(ydl, url: str) -> dict:
    """Resolve Streamrizz's short-lived player URL, then let yt-dlp parse HTML5 media."""
    try:
        page = read_page(ydl, url)
        outer = MediaPageParser()
        outer.feed(page)
        iframe_url = urljoin(url, "/ip129jk?" + urlencode({
            "id": js_string(page, "iframeId"),
            "t": js_string(page, "embedToken"),
        }))
        player_url = js_string(read_page(ydl, iframe_url, url), "playerPath")
        player = urlsplit(player_url)
        if player.scheme not in ("http", "https") or player.hostname != "streamrizz.com":
            raise yt_dlp.utils.DownloadError("Streamrizz returned an invalid player URL.")

        info = ydl.extract_info(player_url, download=False)
    except yt_dlp.utils.DownloadError:
        raise
    except Exception as exc:
        raise yt_dlp.utils.DownloadError(f"Could not resolve Streamrizz media: {exc}") from exc

    if not info:
        raise yt_dlp.utils.DownloadError("Streamrizz did not return a video.")

    for fmt in info.get("formats") or [info]:
        if fmt.get("ext") in (None, "unknown_video"):
            fmt["ext"] = "mp4"
        if fmt.get("vcodec") is None:
            fmt["vcodec"] = "unknown"
        if fmt.get("acodec") is None:
            fmt["acodec"] = "unknown"

    info.update({
        "extractor": "streamrizz",
        "extractor_key": "Streamrizz",
        "webpage_url": url,
    })
    if outer.thumbnail:
        thumbnail = urljoin(url, outer.thumbnail)
        ensure_public_url(thumbnail)
        info["thumbnail"] = thumbnail
        info["_thumbnail_proxy"] = True
    return info


def preview_reader_target(url: str) -> tuple[str, str] | None:
    """Return a public embed/page target used only when the source blocks extraction."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in ("www.xnxx.com", "xnxx.com", "www.xnxx3.com", "xnxx3.com"):
        match = re.fullmatch(r"/video-?([0-9a-z]+)/[^?#]*", parts.path, re.IGNORECASE)
        if match:
            return "XNXX", f"http://www.xnxx.com{parts.path}"
    if host in (
        "www.pornhub.com",
        "pornhub.com",
        "www.pornhub.net",
        "pornhub.net",
        "www.pornhub.org",
        "pornhub.org",
    ):
        video_id = (parse_qs(parts.query).get("viewkey") or [None])[0]
        if not video_id:
            match = re.fullmatch(r"/embed/([0-9a-z]+)", parts.path, re.IGNORECASE)
            video_id = match.group(1) if match else None
        if video_id and re.fullmatch(r"[0-9a-z]+", video_id, re.IGNORECASE):
            return "PornHub", f"http://www.pornhub.com/embed/{video_id}"
    return None


def parse_reader_preview(page: str, platform: str) -> tuple[str, str]:
    title_match = re.search(r"^Title:\s*(.+)$", page, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else f"{platform} video"
    images = re.finditer(
        r"!\[(?P<alt>[^\]]*)\]\((?P<url>https?://.*?\.(?:jpe?g|webp|png)(?:\?[^)\s]*)?)\)",
        page,
        re.IGNORECASE,
    )
    for match in images:
        image_url = match.group("url")
        lowered = image_url.lower()
        if any(part in lowered for part in ("/logo", "/skins/", "/feed.png")):
            continue
        alt = re.sub(r"^Image\s+\d+:\s*", "", match.group("alt"), flags=re.IGNORECASE).strip()
        if alt and not re.fullmatch(r"Image\s+\d+", alt, re.IGNORECASE):
            title = alt
        return title, image_url
    raise yt_dlp.utils.DownloadError(f"No public {platform} preview was found.")


def extract_preview_only(ydl, url: str) -> dict:
    target = preview_reader_target(url)
    if not target:
        raise yt_dlp.utils.DownloadError("No preview fallback is available for that URL.")
    platform, source = target
    reader_url = "https://r.jina.ai/" + source
    ensure_public_url(reader_url)
    title, thumbnail = parse_reader_preview(
        read_page(
            ydl,
            reader_url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain"},
        ),
        platform,
    )
    ensure_public_url(thumbnail)
    return {
        "id": urlsplit(source).path.rstrip("/").rsplit("/", 1)[-1],
        "title": title,
        "thumbnail": thumbnail,
        "_thumbnail_proxy": True,
        "webpage_url": url,
        "extractor": platform.lower(),
        "extractor_key": platform,
        "formats": [],
    }


def is_x_auth_gate(url: str, error: Exception) -> bool:
    """Retry when X explicitly asks for authentication or an age/NSFW gate."""
    message = str(error).lower()
    return is_x_url(url) and any(marker in message for marker in (
        "nsfw", "sensitive content", "sensitive media", "stated age", "age-restricted",
        "requires authentication", "login required",
    ))


def should_retry_x_auth(url: str, error: Exception) -> bool:
    message = str(error).lower()
    return is_x_auth_gate(url, error) or (
        is_x_url(url) and "no video could be found in this tweet" in message
    )


def normalize_cookie_value(value: str | None, cookie_name: str) -> str | None:
    """Accept a raw DevTools value or a pasted `name=value` pair."""
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    prefix = f"{cookie_name}="
    if value.lower().startswith(prefix):
        value = value[len(prefix):].strip()
    return value or None


def normalize_env_value(value: str | None, *prefixes: str) -> str:
    """Clean values copied from a dashboard without exposing their contents."""
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    lowered = value.lower()
    for prefix in prefixes:
        marker = f"{prefix.lower()}="
        if lowered.startswith(marker):
            return value[len(marker):].strip()
    return value


def x_cookie_values() -> tuple[str | None, str | None]:
    return (
        normalize_cookie_value(os.getenv("X_AUTH_TOKEN"), "auth_token"),
        normalize_cookie_value(os.getenv("X_CT0"), "ct0"),
    )


def x_auth_configured() -> bool:
    return all(x_cookie_values())


def add_x_auth_cookies(ydl, auth_token: str | None, ct0: str | None) -> bool:
    """Attach an optional X session to yt-dlp without writing secrets to disk."""
    if not auth_token or not ct0:
        return False

    for domain in (".x.com", ".twitter.com"):
        for name, value in (("auth_token", auth_token), ("ct0", ct0)):
            ydl.cookiejar.set_cookie(Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=True,
                domain_initial_dot=True,
                path="/",
                path_specified=True,
                secure=True,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": None},
                rfc2109=False,
            ))
    return True


def validate_proxy_url(proxy: str) -> str:
    parts = urlsplit(proxy)
    if (
        parts.scheme not in ("http", "https", "socks4", "socks4a", "socks5", "socks5h")
        or not parts.hostname
        or parts.query
        or parts.fragment
    ):
        raise RuntimeError(
            "Media proxies must be valid HTTP(S) or SOCKS proxy URLs."
        )
    return proxy


def is_youtube_url(url: str | None) -> bool:
    host = (urlsplit(url or "").hostname or "").lower().removeprefix("www.")
    return host in ("youtube.com", "youtu.be", "music.youtube.com")


def media_proxy_target(url: str | None) -> bool:
    return bool(url and (preview_reader_target(url) or is_youtube_url(url)))


def media_proxy_urls(url: str | None) -> list[str]:
    """Build the ordered proxy list for sites that need alternate outbound IPs."""
    if not media_proxy_target(url):
        return []

    proxies = [
        value.strip()
        for value in re.split(
            r"[\r\n,]+",
            os.getenv("MEDIA_PROXY_URLS", "") or os.getenv("MEDIA_PROXY_URL", ""),
        )
        if value.strip()
    ]

    servers = [
        value.strip()
        for value in re.split(r"[\r\n,]+", os.getenv("MEDIA_PROXY_SERVERS", ""))
        if value.strip()
    ]
    username = normalize_env_value(
        os.getenv("MEDIA_PROXY_USERNAME"), "MEDIA_PROXY_USERNAME", "username"
    )
    password = normalize_env_value(
        os.getenv("MEDIA_PROXY_PASSWORD"), "MEDIA_PROXY_PASSWORD", "password"
    )
    if bool(username) != bool(password):
        raise RuntimeError(
            "MEDIA_PROXY_USERNAME and MEDIA_PROXY_PASSWORD must be set together."
        )
    if servers and not username:
        raise RuntimeError(
            "MEDIA_PROXY_SERVERS requires MEDIA_PROXY_USERNAME and "
            "MEDIA_PROXY_PASSWORD. Use MEDIA_PROXY_URLS for proxies that do not need login."
        )
    auth = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username else ""
    proxies.extend(f"http://{auth}{server}" for server in servers)

    return list(dict.fromkeys(validate_proxy_url(proxy) for proxy in proxies))


def proxy_failure_kind(error: Exception) -> str:
    messages = []
    current: BaseException | None = error
    while current and len(messages) < 4:
        messages.append(str(current).lower())
        current = current.__cause__ or current.__context__
    message = " ".join(messages)
    if "407" in message or "proxy authentication" in message:
        return "proxy login rejected (HTTP 407)"
    if "403" in message or "forbidden" in message:
        return "destination rejected the proxy (HTTP 403)"
    if "timed out" in message or "timeout" in message:
        return "proxy connection timed out"
    if any(marker in message for marker in (
        "connection refused", "failed to connect", "unable to connect", "name resolution",
    )):
        return "proxy connection failed"
    return "site extraction was blocked"


def make_ydl(
    *,
    proxy: str | None = None,
    x_authenticated: bool = False,
    streamrizz: bool = False,
):
    opts = {**YDL_OPTS, **({"nocheckcertificate": True} if streamrizz else {})}
    if proxy:
        opts["proxy"] = proxy
    ydl = yt_dlp.YoutubeDL(opts)
    if x_authenticated:
        add_x_auth_cookies(ydl, *x_cookie_values())
    return ydl


def is_instagram_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in ("instagram.com", "www.instagram.com")


def best_thumbnail(info: dict) -> str | None:
    thumbnails = [item for item in (info.get("thumbnails") or []) if item.get("url")]
    if not thumbnails:
        return info.get("thumbnail")
    return max(
        enumerate(thumbnails),
        key=lambda item: (
            (item[1].get("width") or 0) * (item[1].get("height") or 0),
            item[0],
        ),
    )[1]["url"]


def normalize_instagram_media(info: dict, url: str) -> dict:
    """Keep Instagram photo carousel entries that yt-dlp otherwise drops."""
    raw_entries = info.get("entries") if info.get("entries") is not None else [info]
    entries = []
    total = len(raw_entries)
    for index, raw in enumerate(raw_entries):
        entry = {**raw}
        image_url = best_thumbnail(entry)
        if image_url and not entry.get("thumbnail"):
            entry["thumbnail"] = image_url
            entry["_thumbnail_proxy"] = True
        if not entry.get("formats") and not entry.get("url"):
            if not image_url:
                continue
            ensure_public_url(image_url)
            entry.update({
                "url": image_url,
                "thumbnail": image_url,
                "_thumbnail_proxy": True,
                "format_id": f"instagram-image-{index + 1}",
                "ext": direct_image_ext(image_url) or "jpg",
                "vcodec": "none",
                "acodec": "none",
            })
        if total > 1 and not entry.get("title"):
            entry["title"] = f"{info.get('title') or 'Instagram post'} #{index + 1}"
        entry.setdefault("webpage_url", url)
        entries.append(entry)

    if info.get("entries") is None:
        if not entries:
            raise yt_dlp.utils.DownloadError("Instagram returned no downloadable media.")
        return entries[0]
    if not entries:
        raise yt_dlp.utils.DownloadError("Instagram returned no downloadable media.")
    return {**info, "entries": entries, "webpage_url": url}


def extract_instagram(ydl, url: str) -> dict:
    # process=False preserves image-only entries. yt-dlp's normal processing
    # currently turns each of those valid carousel entries into null.
    return normalize_instagram_media(
        ydl.extract_info(url, download=False, process=False), url
    )


def instagram_reader_page(shortcode: str, index: int) -> str:
    reader_url = (
        "https://r.jina.ai/http://www.instagram.com/p/"
        f"{shortcode}/?img_index={index}"
    )
    try:
        response = curl_requests.get(
            reader_url,
            headers={"Accept": "text/plain"},
            timeout=25,
        )
        if response.status_code != 200 or len(response.content) > MAX_HTML_BYTES:
            return ""
        return response.text
    except Exception:
        return ""


def extract_instagram_reader(url: str) -> dict:
    """Use the public reader when Instagram blocks the deployment IP."""
    match = re.search(r"/(?:p|reels?|tv)/([^/?#]+)", urlsplit(url).path)
    if not match:
        raise yt_dlp.utils.DownloadError("Invalid Instagram post URL.")
    shortcode = match.group(1)
    with ThreadPoolExecutor(max_workers=5) as pool:
        pages = list(pool.map(
            lambda index: instagram_reader_page(shortcode, index),
            range(1, 11),
        ))

    title = "Instagram post"
    entries = []
    seen = set()
    for page in pages:
        if not page:
            continue
        title_match = re.search(r"^Title:\s*(.+)$", page, re.MULTILINE)
        if title_match and title == "Instagram post":
            title = title_match.group(1).strip()[:300]
        # Related posts appear after this marker and must not leak into results.
        post_only = page.split("\nMore posts from ", 1)[0]
        for image_match in re.finditer(
            r"!\[(?P<alt>[^\]]*)\]\((?P<url>https?://[^)\s]+)\)",
            post_only,
        ):
            alt = image_match.group("alt")
            image_url = image_match.group("url")
            if "profile picture" in alt.lower() or "-15/" not in image_url:
                continue
            asset_key = urlsplit(image_url).path.rsplit("/", 1)[-1]
            if not asset_key or asset_key in seen:
                continue
            try:
                ensure_public_url(image_url)
            except HTTPException:
                continue
            seen.add(asset_key)
            entries.append({
                "id": f"{shortcode}-image-{len(entries) + 1}",
                "title": re.sub(r"^Image\s+\d+:\s*", "", alt).strip()
                or f"Instagram photo {len(entries) + 1}",
                "webpage_url": url,
                "thumbnail": image_url,
                "_thumbnail_proxy": True,
                "url": image_url,
                "format_id": f"instagram-reader-{len(entries) + 1}",
                "ext": direct_image_ext(image_url) or "jpg",
                "vcodec": "none",
                "acodec": "none",
                "http_headers": {"Referer": "https://www.instagram.com/"},
            })
    if not entries:
        raise yt_dlp.utils.DownloadError("The public Instagram reader found no media.")
    return {
        "_type": "playlist",
        "id": shortcode,
        "title": title,
        "webpage_url": url,
        "extractor": "instagram",
        "extractor_key": "Instagram",
        "entries": entries[:10],
    }


def build_x_photo_info(status: dict, url: str, tweet_id: str) -> dict | None:
    user = status.get("user") or {}
    text = (status.get("full_text") or status.get("text") or "").replace("\n", " ").strip()
    uploader = user.get("name") or user.get("screen_name")
    title = " - ".join(value for value in (uploader, text) if value) or f"X post {tweet_id}"
    entries = []
    media = (status.get("extended_entities") or {}).get("media") or []
    for index, item in enumerate(media):
        if item.get("type") != "photo":
            continue
        image_url = item.get("media_url_https") or item.get("media_url")
        if not image_url:
            continue
        parts = urlsplit(image_url)
        query = parse_qs(parts.query)
        query["name"] = ["orig"]
        image_url = parts._replace(query=urlencode(query, doseq=True)).geturl()
        try:
            ensure_public_url(image_url)
        except HTTPException:
            continue
        entries.append({
            "id": f"{tweet_id}-photo-{index + 1}",
            "title": f"{title} #{index + 1}" if len(media) > 1 else title,
            "uploader": uploader,
            "webpage_url": url,
            "thumbnail": image_url,
            "_thumbnail_proxy": True,
            "url": image_url,
            "format_id": f"twitter-image-{index + 1}",
            "ext": direct_image_ext(image_url) or "jpg",
            "vcodec": "none",
            "acodec": "none",
            "http_headers": {"Referer": "https://x.com/"},
        })
    if not entries:
        return None
    return {
        "_type": "playlist",
        "id": tweet_id,
        "title": title,
        "uploader": uploader,
        "webpage_url": url,
        "extractor": "twitter",
        "extractor_key": "Twitter",
        "entries": entries,
    }


def x_photo_info(ydl, url: str) -> dict | None:
    match = re.search(r"/(?:status|statuses)/(\d+)", urlsplit(url).path)
    if not match:
        return None
    tweet_id = match.group(1)
    try:
        from yt_dlp.extractor.twitter import TwitterIE

        extractor = TwitterIE(ydl)
        extractor.initialize()
        status = extractor._extract_status(tweet_id)
    except Exception:
        return None
    return build_x_photo_info(status, url, tweet_id)


def extract_x_media(ydl, url: str) -> dict:
    primary = None
    primary_error = None
    try:
        primary = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        primary_error = exc
    photos = x_photo_info(ydl, url)
    if primary and photos:
        return {
            **primary,
            "_type": "playlist",
            "entries": [*entries_of(primary), *entries_of(photos)],
            "webpage_url": url,
        }
    if primary:
        return primary
    if photos:
        return photos
    raise primary_error or yt_dlp.utils.DownloadError("X returned no downloadable media.")

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
    suffix = (ext or "mp4").lower()
    base = re.sub(
        rf"\.{re.escape(suffix)}(?=(?: \(\d+\))?$)", "", base, flags=re.IGNORECASE
    ).rstrip()
    return f"{base or 'saveflow'}.{suffix}"


def run_extraction(
    ydl,
    url: str,
    *,
    x_authenticated: bool = False,
    preview_fallback: bool = True,
) -> dict:
    """Run yt-dlp on an existing session and map its failures to HTTP errors."""
    ensure_public_url(url)

    try:
        direct = probe_direct_media(ydl, url)
        if direct:
            info = direct
        elif streamrizz_folder_id(url):
            info = extract_streamrizz_folder(ydl, url)
        elif is_streamrizz_url(url):
            info = extract_streamrizz(ydl, url)
        elif videeyss_id(url):
            info = extract_videeyss(ydl, url)
        elif is_acegimg_url(url):
            info = extract_acegimg(ydl, url)
        elif aceimg_page := aceimg_cdn_page(url):
            info = extract_public_page(aceimg_page)
        elif aceimg_id(url):
            try:
                info = extract_aceimg(ydl, url)
            except yt_dlp.utils.DownloadError:
                info = extract_public_page(url)
        elif vid3y_media(url):
            info = extract_vid3y(ydl, url)
        elif is_instagram_url(url):
            try:
                info = extract_instagram(ydl, url)
            except yt_dlp.utils.DownloadError as exc:
                try:
                    info = extract_instagram_reader(url)
                except Exception:
                    try:
                        info = extract_public_page(url)
                    except Exception:
                        raise exc
        elif is_x_url(url):
            info = extract_x_media(ydl, url)
        else:
            try:
                info = ydl.extract_info(url, download=False)
            except yt_dlp.utils.DownloadError as exc:
                if preview_fallback and preview_reader_target(url):
                    try:
                        info = extract_preview_only(ydl, url)
                    except Exception:
                        raise exc
                else:
                    try:
                        info = extract_public_page(url)
                    except Exception:
                        raise exc
    except HTTPException:
        raise
    except yt_dlp.utils.DownloadError as exc:
        message = str(exc)
        lowered = message.lower()
        if is_x_auth_gate(url, exc) and not x_auth_configured():
            detail = (
                "This X post requires authentication. Configure X_AUTH_TOKEN and "
                "X_CT0 on the backend, then try again."
            )
        elif x_authenticated and (
            "login" in lowered or "cookies" in lowered or "auth" in lowered
        ):
            detail = (
                "X rejected the configured login session. Refresh X_AUTH_TOKEN and X_CT0 "
                "on the backend, then try again."
            )
        elif is_youtube_url(url) and any(marker in lowered for marker in (
            "confirm you're not a bot", "confirm you’re not a bot", "sign in to confirm",
            "player response", "po token",
        )):
            detail = (
                "YouTube rejected this server or proxy as automated traffic. "
                "Check the media proxy credentials or replace the blocked proxy."
            )
        elif "private" in lowered or "login" in lowered or "cookies" in lowered:
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
    ydl, info = extract_with_session(url)
    ydl.close()
    return info


def extract_with_session(url: str):
    """Extract anonymously, retrying with X cookies only for an explicit auth gate."""
    # Streamrizz's current media CDN has an expired certificate. Keep the bypass
    # scoped to this one integration instead of weakening every platform.
    streamrizz = bool(is_streamrizz_url(url) or streamrizz_folder_id(url))
    try:
        proxies = media_proxy_urls(url)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if proxies:
        last_error = None
        failure_counts: dict[str, int] = {}
        for proxy in proxies:
            ydl = make_ydl(proxy=proxy, streamrizz=streamrizz)
            try:
                info = run_extraction(ydl, url, preview_fallback=False)
                if not any(
                    entry.get("url") or entry.get("formats")
                    for entry in entries_of(info)
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="That proxy returned metadata without downloadable media.",
                    )
                return ydl, info
            except HTTPException as exc:
                last_error = exc
                reason = proxy_failure_kind(exc)
                failure_counts[reason] = failure_counts.get(reason, 0) + 1
                ydl.close()

        failure_summary = ", ".join(
            f"{count}× {reason}" for reason, count in failure_counts.items()
        ) or "unknown proxy error"
        ydl = make_ydl()
        try:
            info = extract_preview_only(ydl, url)
            info["_media_warning"] = (
                f"Preview only: all {len(proxies)} configured media proxies failed "
                f"({failure_summary})."
            )
            return ydl, info
        except Exception:
            ydl.close()
            raise HTTPException(
                status_code=502,
                detail=(
                    f"All {len(proxies)} configured media proxies failed "
                    f"({failure_summary})."
                ),
            ) from last_error

    ydl = make_ydl(streamrizz=streamrizz)
    try:
        return ydl, run_extraction(ydl, url)
    except HTTPException as exc:
        if not should_retry_x_auth(url, exc.__cause__ or exc) or not x_auth_configured():
            ydl.close()
            raise

    ydl.close()
    ydl = make_ydl(x_authenticated=True)
    try:
        info = run_extraction(ydl, url, x_authenticated=True)
    except Exception:
        ydl.close()
        raise

    return ydl, info


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
    if ext == "m3u8":
        return "stream"
    if ext in VIDEO_EXTS:
        return "video"
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
        progressive = fmt.get("acodec") not in (None, "none")
        candidate = {
            "format_id": str(fmt.get("format_id") or ""),
            "url": fmt["url"],
            "label": label if progressive else f"{label} · video only",
            "ext": fmt.get("ext") or "mp4",
            "filesize": human_size(fmt.get("filesize") or fmt.get("filesize_approx")),
            "kind": "video",
            "_progressive": progressive,
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

    # A complete video+audio file is useful immediately; make it the primary
    # button even when a taller video-only track also exists.
    ordered = sorted(
        best_by_label.values(),
        key=lambda f: (f["_progressive"], f["_height"]),
        reverse=True,
    )
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
    formats = build_formats(info)
    thumbnail = info.get("thumbnail")
    if not thumbnail:
        thumbnail = next(
            (fmt["url"] for fmt in formats if fmt.get("kind") == "image"),
            None,
        )
    return {
        "index": index,
        "title": info.get("title") or info.get("description") or "Untitled media",
        "thumbnail": thumbnail,
        "thumbnail_proxy": bool(info.get("_thumbnail_proxy")),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel") or info.get("uploader_id"),
        "warning": info.get("_media_warning"),
        "formats": formats,
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
    auth_token, ct0 = x_cookie_values()
    proxy_urls = [
        value.strip()
        for value in re.split(
            r"[\r\n,]+",
            os.getenv("MEDIA_PROXY_URLS", "") or os.getenv("MEDIA_PROXY_URL", ""),
        )
        if value.strip()
    ]
    proxy_servers = [
        value.strip()
        for value in re.split(r"[\r\n,]+", os.getenv("MEDIA_PROXY_SERVERS", ""))
        if value.strip()
    ]
    proxy_username = bool(normalize_env_value(
        os.getenv("MEDIA_PROXY_USERNAME"), "MEDIA_PROXY_USERNAME", "username"
    ))
    proxy_password = bool(normalize_env_value(
        os.getenv("MEDIA_PROXY_PASSWORD"), "MEDIA_PROXY_PASSWORD", "password"
    ))
    return {
        "status": "ok",
        "message": "Saveflow API is running.",
        "docs": "/docs",
        "endpoint": "POST /api/extract",
        "yt_dlp": YT_DLP_VERSION,
        "x_auth": {
            "configured": bool(auth_token and ct0),
            "auth_token_set": bool(auth_token),
            "ct0_set": bool(ct0),
        },
        "media_proxy": {
            "configured": bool(proxy_urls or proxy_servers)
            and (not proxy_servers or (proxy_username and proxy_password)),
            "proxy_count": len(proxy_urls) + len(proxy_servers),
            "username_set": proxy_username,
            "password_set": proxy_password,
        },
    }


@app.post("/api/extract")
def extract(payload: ExtractRequest):
    url = (payload.url or "").strip()
    info = extract_info(url)

    items = [build_item(entry, i) for i, entry in enumerate(entries_of(info))]
    items = [item for item in items if item["formats"] or item["thumbnail"]]
    folders = info.get("_folders") or []
    page_preview_url = info.get("_page_preview_url")

    if not items and not folders and not page_preview_url:
        raise HTTPException(
            status_code=400,
            detail="No downloadable media or preview image was found at that link.",
        )

    return {
        "platform": friendly_platform(info),
        "source_url": info.get("webpage_url") or url,
        # Top-level fields mirror the first item so simple clients can ignore
        # `items` entirely.
        "title": info.get("title") or (items[0]["title"] if items else "Media folder"),
        "thumbnail": items[0]["thumbnail"] if items else None,
        "duration": items[0]["duration"] if items else None,
        "items": items,
        "folders": folders,
        "parent_url": info.get("_parent_url"),
        "page_preview": ({
            "url": page_preview_url,
            "title": info.get("title") or "Source page",
        } if page_preview_url else None),
    }


@app.get("/api/thumbnail")
def thumbnail(
    url: str = Query(..., description="The original post URL."),
    index: int = Query(0, ge=0, description="Which item preview to fetch."),
):
    """Proxy an extractor-discovered thumbnail without becoming an open URL proxy."""
    ydl, info = extract_with_session(url)
    try:
        items = entries_of(info)
        if index >= len(items):
            raise HTTPException(status_code=400, detail="That item is not part of this post.")
        item = items[index]
        image_url = item.get("thumbnail") or info.get("thumbnail")
        if not image_url:
            raise HTTPException(status_code=404, detail="No preview image is available.")
        ensure_public_url(image_url)
        headers = {
            **(info.get("http_headers") or {}),
            **(item.get("http_headers") or {}),
            "Referer": item.get("webpage_url") or url,
        }
        upstream = ydl.urlopen(YdlRequest(image_url, headers=headers))
        try:
            ensure_public_url(upstream.url)
            content_type = (upstream.headers.get("Content-Type") or "").lower()
            if not content_type.startswith("image/"):
                raise HTTPException(status_code=502, detail="The preview source is not an image.")
        except HTTPException:
            upstream.close()
            raise
    except HTTPException:
        ydl.close()
        raise
    except Exception as exc:
        ydl.close()
        raise HTTPException(status_code=502, detail=f"Could not fetch the preview image: {exc}") from exc

    def stream_image():
        try:
            while chunk := upstream.read(CHUNK_SIZE):
                yield chunk
        finally:
            upstream.close()
            ydl.close()

    return StreamingResponse(
        stream_image(),
        media_type=content_type.split(";", 1)[0],
        headers={"Cache-Control": "public, max-age=3600"},
    )


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
    this endpoint unusable as an open proxy — it only fetches public media URLs
    produced by an extractor or discovered in a public video page.

    The fetch goes through the *same* yt-dlp session that did the extraction.
    That matters: platform CDNs validate the cookies handed out during
    extraction (TikTok's `ttwid`, for one), so a hand-rolled request carrying
    only the headers — however correct — still gets a 403.
    """
    ydl, info = extract_with_session(url)
    try:
        items = entries_of(info)

        folder_item = next(
            (entry for entry in items if entry.get("_streamrizz_format_id") == format_id),
            None,
        )
        if folder_item is None and index >= len(items):
            raise HTTPException(status_code=400, detail="That item is not part of this post.")

        item = folder_item or items[index]
        selected_title = item.get("title")
        if item.get("_streamrizz_url"):
            resolved = extract_streamrizz(ydl, item["_streamrizz_url"])
            fmt = pick_raw_format(resolved, None)
            source_headers = resolved.get("http_headers") or {}
        else:
            fmt = pick_raw_format(item, format_id)
            source_headers = item.get("http_headers") or {}
        ensure_public_url(fmt["url"])
        filename = safe_filename(selected_title, fmt.get("ext"))

        headers = {
            **(info.get("http_headers") or {}),
            **source_headers,
            **(fmt.get("http_headers") or {}),
        }
        # Several CDNs (TikTok's especially) reject a plain GET but serve the
        # same file happily for a range request, which is what a browser sends.
        headers.setdefault("Range", "bytes=0-")

        upstream = ydl.urlopen(YdlRequest(fmt["url"], headers=headers))
        try:
            ensure_public_url(upstream.url)
            if "text/html" in (upstream.headers.get("Content-Type") or "").lower():
                raise HTTPException(
                    status_code=502,
                    detail="The discovered video source points to another webpage, not a media file.",
                )
        except HTTPException:
            upstream.close()
            raise
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
            {"format_id": "v4", "url": "f", "height": 2160, "vcodec": "h264",
             "acodec": "none", "tbr": 12000, "ext": "mp4"},
            # Adaptive stream: held back while progressive formats exist.
            {"format_id": "hls", "url": "d", "height": 480, "vcodec": "h264",
             "acodec": "mp4a", "protocol": "m3u8_native"},
            {"format_id": "a1", "url": "e", "vcodec": "none", "acodec": "mp4a",
             "abr": 128, "ext": "m4a"},
        ],
    }
    fmts = build_formats(video_info)
    assert [f["label"] for f in fmts] == [
        "1080p", "720p", "2160p · video only", "Audio only",
    ], fmts
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
    direct_file = build_formats({
        "formats": [{"format_id": "mp4", "url": "z", "ext": "mp4", "protocol": "https"}]
    })
    assert direct_file[0]["kind"] == "video"

    # Format selection for the download endpoint.
    assert pick_raw_format(video_info, "v3")["url"] == "c"
    assert pick_raw_format(video_info, "gone")["url"] == "b", "falls back to best"

    # Filenames must be safe and never empty, and headers must not be injectable.
    assert safe_filename('a/b:c*d"e', "mp4") == "a b c d e.mp4"
    assert safe_filename("   ", None) == "saveflow.mp4"
    assert safe_filename("already.jpg", "jpg") == "already.jpg"
    assert safe_filename("already.mp4 (1)", "mp4") == "already (1).mp4"
    assert safe_filename("x\r\nSet-Cookie: y", "mp4") == "x Set-Cookie y.mp4"
    header = content_disposition(safe_filename("Kucing 🐈 lucu", "mp4"))
    assert "\r" not in header and "\n" not in header
    assert "filename*=UTF-8''" in header

    assert human_size(None) is None

    # Auth must only be considered for an explicit X authentication response.
    assert is_x_auth_gate(
        "https://x.com/user/status/1", Exception("NSFW tweet requires authentication")
    )
    assert is_x_auth_gate(
        "https://x.com/user/status/1", Exception("This post requires authentication")
    )
    assert not is_x_auth_gate("https://x.com/user/status/1", Exception("Protected tweet"))
    assert not is_x_auth_gate("https://example.com/1", Exception("NSFW"))
    assert should_retry_x_auth(
        "https://x.com/user/status/1", Exception("No video could be found in this tweet")
    )
    assert not should_retry_x_auth(
        "https://example.com/1", Exception("No video could be found in this tweet")
    )
    assert is_streamrizz_url("https://streamrizz.com/d/abc_123-x")
    assert is_streamrizz_url("https://streamrizz.com/e/abc_123-x")
    assert not is_streamrizz_url("https://streamrizz.example/d/abc_123-x")
    assert streamrizz_folder_id("https://tribunvideo.com/f/abc_123-x") == "abc_123-x"
    assert streamrizz_folder_id("https://streamrizz.com/f/abc_123-x") == "abc_123-x"
    assert streamrizz_folder_id("https://tribunvideo.com/d/abc_123-x") is None
    assert videeyss_id("https://videeyss.shop/DFNXJw7g1") == "DFNXJw7g1"
    assert videeyss_id("https://www.videeyss.shop/abc_123-x/") == "abc_123-x"
    assert videeyss_id("https://videeyss.shop/a/b") is None
    assert videeyss_id("https://videeyss.example/DFNXJw7g1") is None
    assert aceimg_id("https://slicdrve.ink/i71UvqVF6") == "i71UvqVF6"
    assert aceimg_id("https://aceimg.ink/2ZSqnueRR") == "2ZSqnueRR"
    assert aceimg_id("https://aceimg.example/2ZSqnueRR") is None
    assert aceimg_cdn_page("https://cdn2.aceimg.com/Vsbx6xpay.mp4") == (
        "https://aceimg.ink/Vsbx6xpay"
    )
    assert aceimg_cdn_page("https://cdn.example/Vsbx6xpay.mp4") is None
    assert is_acegimg_url("https://cdn2.acegimg.com/Vsbx6xpay.mp4")
    assert not is_acegimg_url("https://cdn2.aceimg.com/Vsbx6xpay.mp4")
    assert aceimg_upload_media("https://aceimg.com/upload/?f=KBTQCQpcx.mp4") == (
        "KBTQCQpcx", "https://cdn.aceimg.com/KBTQCQpcx.mp4"
    )
    assert aceimg_upload_media("https://example.com/upload/?f=KBTQCQpcx.mp4") is None
    assert direct_image_ext("https://cdn.example/photo.JPG?size=large") == "jpg"
    assert direct_image_ext("https://cdn.example/video.mp4") is None
    assert vid3y_media("https://cdn.vid3y.my.id/2bnwZ5S21") == (
        "2bnwZ5S21", "https://cdn2.videy.co/2bnwZ5S21.mp4"
    )
    assert vid3y_media("https://vid3y.my.id/?v=abc123&sumber=aceimg2") == (
        "abc123", "https://cdn2.aceimg.com/abc123.mp4"
    )
    instagram = normalize_instagram_media({
        "title": "Photo post",
        "entries": [{"thumbnails": [{"url": "https://93.184.216.34/photo.jpg"}]}],
    }, "https://www.instagram.com/p/example/")
    assert instagram["entries"][0]["format_id"] == "instagram-image-1"
    assert instagram["entries"][0]["ext"] == "jpg"
    twitter = build_x_photo_info({
        "text": "Photo tweet",
        "user": {"name": "Tester"},
        "extended_entities": {"media": [{
            "type": "photo", "media_url_https": "https://93.184.216.34/x.jpg",
        }]},
    }, "https://x.com/test/status/1", "1")
    assert twitter and twitter["entries"][0]["format_id"] == "twitter-image-1"
    assert js_string(
        r"const playerPath = 'https://streamrizz.com/stream.php?bucket=x\u0026id=y';",
        "playerPath",
    ) == "https://streamrizz.com/stream.php?bucket=x&id=y"

    folder_parser = StreamrizzFolderParser()
    folder_parser.feed('''
        <title>📂 Sample folder</title>
        <a class="back-btn" href="/f/parent">Back</a>
        <a class="page-btn active" href="/f/sample?p=1">1</a>
        <a class="page-btn" href="/f/sample?p=2">2</a>
        <a class="page-btn" href="/f/sample?p=3">3</a>
        <a class="page-btn" href="/f/other?p=20">Other folder</a>
        <a class="page-btn" href="/f/sample?p=999">Too far</a>
        <a class="folder-chip" href="/f/child">Child folder</a>
        <article class="drive-file-card">
          <a class="thumb-link" href="/d/clip1"><img src="https://img.example/1.jpg"></a>
          <a class="file-name" title="First clip.mp4" href="/d/clip1">First clip.mp4</a>
        </article>
    ''')
    assert folder_parser.entries == [{
        "url": "/d/clip1",
        "thumbnail": "https://img.example/1.jpg",
        "title": "First clip.mp4",
    }]
    assert folder_parser.folders == [{"url": "/f/child", "title": "Child folder"}]
    assert folder_parser.parent_url == "/f/parent"
    assert streamrizz_folder_page_numbers(
        "https://streamrizz.com/f/sample", folder_parser.page_urls
    ) == [1, 2, 3]
    assert preview_reader_target(
        "https://www.pornhub.com/view_video.php?viewkey=abc123"
    ) == ("PornHub", "http://www.pornhub.com/embed/abc123")
    preview_title, preview_image = parse_reader_preview(
        "Title: Embed Player\n![Image 1: Sample](https://cdn.example/a/(x=1)1.jpg)",
        "PornHub",
    )
    assert (preview_title, preview_image) == (
        "Sample",
        "https://cdn.example/a/(x=1)1.jpg",
    )

    sample_page = """
        <title>Sample clip</title>
        <video poster="/poster.jpg"><source src="/video.mp4" type="video/mp4"></video>
        <meta property="og:video" content="https://cdn.example/video.webm">
        <script type="application/ld+json">
          {"@type":"VideoObject","contentUrl":"https://cdn.example/master.m3u8"}
        </script>
    """
    parser = MediaPageParser()
    parser.feed(sample_page)
    assert parser.title_parts == ["Sample clip"]
    assert parser.thumbnail == "/poster.jpg"
    assert parser.media == [
        ("/video.mp4", "video/mp4"),
        ("https://cdn.example/video.webm", None),
    ]
    assert json_media_urls(json.loads(parser.json_ld[0])) == [
        "https://cdn.example/master.m3u8"
    ]
    public_image = parse_public_media(
        "https://93.184.216.34/post",
        '<title>Photo</title><meta property="og:image" content="/photo.jpg">',
    )
    assert public_image["formats"][0]["vcodec"] == "none"
    public_empty = parse_public_media(
        "https://93.184.216.34/post", "<title>Unknown page</title>"
    )
    assert public_empty["_page_preview_url"] == "https://93.184.216.34/post"
    try:
        ensure_public_url("http://127.0.0.1/video.mp4")
        raise AssertionError("loopback URLs must be rejected")
    except HTTPException as exc:
        assert exc.status_code == 400
    assert normalize_cookie_value(' "auth_token=test-auth" ', "auth_token") == "test-auth"
    assert normalize_cookie_value("test-csrf", "ct0") == "test-csrf"
    assert normalize_env_value(' "username=test-user" ', "username") == "test-user"
    assert normalize_env_value("MEDIA_PROXY_PASSWORD=test-pass", "MEDIA_PROXY_PASSWORD") == "test-pass"
    assert proxy_failure_kind(Exception("HTTP Error 407: Proxy Authentication Required")) == (
        "proxy login rejected (HTTP 407)"
    )

    proxy_env_names = (
        "MEDIA_PROXY_URL",
        "MEDIA_PROXY_URLS",
        "MEDIA_PROXY_SERVERS",
        "MEDIA_PROXY_USERNAME",
        "MEDIA_PROXY_PASSWORD",
    )
    previous_proxy_env = {name: os.environ.get(name) for name in proxy_env_names}
    try:
        for name in proxy_env_names:
            os.environ.pop(name, None)
        os.environ["MEDIA_PROXY_SERVERS"] = "proxy-a.example:8080,proxy-b.example:8081"
        os.environ["MEDIA_PROXY_USERNAME"] = "user@example"
        os.environ["MEDIA_PROXY_PASSWORD"] = "pass:word"
        expected_proxies = [
            "http://user%40example:pass%3Aword@proxy-a.example:8080",
            "http://user%40example:pass%3Aword@proxy-b.example:8081",
        ]
        assert media_proxy_urls("https://www.xnxx.com/video-abc/sample") == expected_proxies
        assert media_proxy_urls(
            "https://www.pornhub.com/view_video.php?viewkey=abc123"
        ) == expected_proxies
        assert media_proxy_urls("https://www.youtube.com/watch?v=abc123") == expected_proxies
        os.environ.pop("MEDIA_PROXY_USERNAME")
        os.environ.pop("MEDIA_PROXY_PASSWORD")
        try:
            media_proxy_urls("https://www.xnxx.com/video-abc/sample")
            raise AssertionError("server lists without credentials must be rejected")
        except RuntimeError as exc:
            assert "requires MEDIA_PROXY_USERNAME" in str(exc)
        os.environ.pop("MEDIA_PROXY_SERVERS")
        os.environ["MEDIA_PROXY_URLS"] = (
            "http://proxy-a.example:8080\nhttp://proxy-a.example:8080,"
            "socks5://proxy-b.example:1080"
        )
        assert media_proxy_urls("https://www.xnxx.com/video-abc/sample") == [
            "http://proxy-a.example:8080",
            "socks5://proxy-b.example:1080",
        ]
        os.environ["MEDIA_PROXY_URLS"] = "file:///not-a-proxy"
        try:
            media_proxy_urls("https://www.xnxx.com/video-abc/sample")
            raise AssertionError("invalid proxy URLs must be rejected")
        except RuntimeError:
            pass
    finally:
        for name, value in previous_proxy_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    cookie_ydl = make_ydl()
    try:
        assert add_x_auth_cookies(cookie_ydl, "test-auth", "test-csrf")
        cookies = {(c.domain, c.name): c.value for c in cookie_ydl.cookiejar}
        assert cookies[(".x.com", "auth_token")] == "test-auth"
        assert cookies[(".x.com", "ct0")] == "test-csrf"
    finally:
        cookie_ydl.close()

    print("self-check ok")


if __name__ == "__main__":
    _self_check()
