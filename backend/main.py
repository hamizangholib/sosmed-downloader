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
from http.cookiejar import Cookie
from html.parser import HTMLParser
from urllib.error import HTTPError as UrlHTTPError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yt_dlp
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
VIDEO_EXTS = ("mp4", "webm", "mov", "m4v", "mkv", "avi", "m3u8")

CHUNK_SIZE = 64 * 1024
MAX_HTML_BYTES = 2_000_000


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
        self._in_title = False
        self._in_name = False
        self._name_parts: list[str] = []
        self._entry: dict | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "title":
            self._in_title = True
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

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._in_name:
            self._in_name = False
            if self._entry is not None and not self._entry.get("title"):
                self._entry["title"] = "".join(self._name_parts).strip()
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

    if not formats:
        raise yt_dlp.utils.DownloadError("No public video source was found in that page.")

    thumbnail = urljoin(page_url, parser.thumbnail) if parser.thumbnail else None
    if thumbnail:
        try:
            ensure_public_url(thumbnail)
        except HTTPException:
            thumbnail = None
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()[:300] or "Untitled media"
    return {
        "id": urlsplit(page_url).path.rstrip("/").rsplit("/", 1)[-1] or "public-video",
        "title": title,
        "thumbnail": thumbnail,
        "webpage_url": page_url,
        "extractor": "generic-public-page",
        "extractor_key": "PublicPage",
        "formats": formats,
    }


def extract_public_page(url: str) -> dict:
    return parse_public_media(*fetch_public_html(url))


def is_streamrizz_url(url: str) -> bool:
    parts = urlsplit(url)
    return (
        (parts.hostname or "").lower() in ("streamrizz.com", "www.streamrizz.com")
        and re.fullmatch(r"/d/[A-Za-z0-9_-]+/?", parts.path) is not None
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


def videeyss_id(url: str) -> str | None:
    """Return the public clip id used by Videeyss' click-to-load player."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in ("videeyss.shop", "www.videeyss.shop"):
        return None
    match = re.fullmatch(r"/([A-Za-z0-9_-]+)/?", parts.path)
    return match.group(1) if match else None


def extract_videeyss(ydl, url: str) -> dict:
    """Resolve the MP4 that Videeyss inserts only after its Play button is clicked."""
    clip_id = videeyss_id(url)
    if not clip_id:
        raise yt_dlp.utils.DownloadError("Invalid Videeyss video URL.")

    media_url = f"https://cdn2.videy.co/{clip_id}.mp4"
    ensure_public_url(media_url)
    try:
        response = ydl.urlopen(YdlRequest(media_url, headers={
            "Referer": url,
            "Range": "bytes=0-0",
        }))
        try:
            ensure_public_url(response.url)
            content_type = (response.headers.get("Content-Type") or "").lower()
            if not content_type.startswith("video/"):
                raise yt_dlp.utils.DownloadError("Videeyss did not return a video file.")
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
        raise yt_dlp.utils.DownloadError(f"Could not resolve Videeyss media: {exc}") from exc

    return {
        "id": clip_id,
        "title": clip_id,
        "webpage_url": url,
        "extractor": "videeyss",
        "extractor_key": "Videeyss",
        "formats": [{
            "format_id": "videeyss-mp4",
            "format_note": "MP4",
            "url": media_url,
            "ext": "mp4",
            "protocol": "https",
            "vcodec": "unknown",
            "acodec": "unknown",
            "filesize": filesize,
            "http_headers": {"Referer": url},
        }],
    }


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
    response = ydl.urlopen(YdlRequest(url, headers=request_headers))
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

    entries = []
    for raw in parser.entries[:100]:
        video_page = urljoin(folder_url, raw["url"])
        if not is_streamrizz_url(video_page):
            continue
        raw_thumbnail = raw.get("thumbnail")
        thumbnail = urljoin(folder_url, raw_thumbnail) if raw_thumbnail else None
        try:
            ensure_public_url(video_page)
            if thumbnail:
                ensure_public_url(thumbnail)
        except HTTPException:
            continue
        video_id = urlsplit(video_page).path.rstrip("/").rsplit("/", 1)[-1]
        format_id = f"streamrizz-{video_id}"
        entries.append({
            "id": video_id,
            "title": raw.get("title") or video_id,
            "thumbnail": thumbnail,
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

    if not entries:
        raise yt_dlp.utils.DownloadError("No videos were found in that folder.")
    title = re.sub(r"^\s*📂\s*", "", "".join(parser.title_parts)).strip()
    return {
        "id": folder_id,
        "title": title or "Video folder",
        "webpage_url": url,
        "extractor": "streamrizz:folder",
        "extractor_key": "StreamrizzFolder",
        "entries": entries,
    }


def extract_streamrizz(ydl, url: str) -> dict:
    """Resolve Streamrizz's short-lived player URL, then let yt-dlp parse HTML5 media."""
    try:
        page = read_page(ydl, url)
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


def media_proxy_urls(url: str | None) -> list[str]:
    """Build the ordered proxy list for sites that need alternate outbound IPs."""
    if not url or not preview_reader_target(url):
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
    username = os.getenv("MEDIA_PROXY_USERNAME", "").strip()
    password = os.getenv("MEDIA_PROXY_PASSWORD", "").strip()
    if bool(username) != bool(password):
        raise RuntimeError(
            "MEDIA_PROXY_USERNAME and MEDIA_PROXY_PASSWORD must be set together."
        )
    auth = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username else ""
    proxies.extend(f"http://{auth}{server}" for server in servers)

    return list(dict.fromkeys(validate_proxy_url(proxy) for proxy in proxies))


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
        if streamrizz_folder_id(url):
            info = extract_streamrizz_folder(ydl, url)
        elif is_streamrizz_url(url):
            info = extract_streamrizz(ydl, url)
        elif videeyss_id(url):
            info = extract_videeyss(ydl, url)
        else:
            try:
                info = ydl.extract_info(url, download=False)
            except yt_dlp.utils.DownloadError as exc:
                if preview_fallback and preview_reader_target(url):
                    try:
                        info = extract_preview_only(ydl, url)
                    except Exception:
                        raise exc
                elif "unsupported url" in str(exc).lower():
                    info = extract_public_page(url)
                else:
                    raise
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
    proxies = media_proxy_urls(url)
    if proxies:
        last_error = None
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
                ydl.close()

        ydl = make_ydl()
        try:
            return ydl, extract_preview_only(ydl, url)
        except Exception:
            ydl.close()
            raise last_error

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
        "thumbnail_proxy": bool(info.get("_thumbnail_proxy")),
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
    auth_token, ct0 = x_cookie_values()
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
    }


@app.post("/api/extract")
def extract(payload: ExtractRequest):
    url = (payload.url or "").strip()
    info = extract_info(url)

    items = [build_item(entry, i) for i, entry in enumerate(entries_of(info))]
    items = [item for item in items if item["formats"] or item["thumbnail"]]

    if not items:
        raise HTTPException(
            status_code=400,
            detail="No downloadable media or preview image was found at that link.",
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
    assert not is_streamrizz_url("https://streamrizz.example/d/abc_123-x")
    assert streamrizz_folder_id("https://tribunvideo.com/f/abc_123-x") == "abc_123-x"
    assert streamrizz_folder_id("https://streamrizz.com/f/abc_123-x") == "abc_123-x"
    assert streamrizz_folder_id("https://tribunvideo.com/d/abc_123-x") is None
    assert videeyss_id("https://videeyss.shop/DFNXJw7g1") == "DFNXJw7g1"
    assert videeyss_id("https://www.videeyss.shop/abc_123-x/") == "abc_123-x"
    assert videeyss_id("https://videeyss.shop/a/b") is None
    assert videeyss_id("https://videeyss.example/DFNXJw7g1") is None
    assert js_string(
        r"const playerPath = 'https://streamrizz.com/stream.php?bucket=x\u0026id=y';",
        "playerPath",
    ) == "https://streamrizz.com/stream.php?bucket=x&id=y"

    folder_parser = StreamrizzFolderParser()
    folder_parser.feed('''
        <title>📂 Sample folder</title>
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
    try:
        ensure_public_url("http://127.0.0.1/video.mp4")
        raise AssertionError("loopback URLs must be rejected")
    except HTTPException as exc:
        assert exc.status_code == 400
    assert normalize_cookie_value(' "auth_token=test-auth" ', "auth_token") == "test-auth"
    assert normalize_cookie_value("test-csrf", "ct0") == "test-csrf"

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
        assert media_proxy_urls("https://www.youtube.com/watch?v=abc123") == []
        os.environ.pop("MEDIA_PROXY_SERVERS")
        os.environ.pop("MEDIA_PROXY_USERNAME")
        os.environ.pop("MEDIA_PROXY_PASSWORD")
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
