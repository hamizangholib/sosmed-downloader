(function exposeSaveflowMedia(global) {
  "use strict";

  const IMAGE_EXTENSIONS = new Set([
    "avif", "bmp", "gif", "heic", "jpeg", "jpg", "png", "svg", "tif", "tiff", "webp",
  ]);
  const VIDEO_EXTENSIONS = new Set([
    "avi", "m4v", "mkv", "mov", "mp4", "webm",
  ]);
  const IGNORE_EXTENSIONS = new Set([
    "css", "eot", "html", "js", "json", "map", "otf", "ttf", "woff", "woff2",
  ]);

  function asHttpUrl(value, baseUrl) {
    if (!value || String(value).startsWith("blob:")) return null;
    try {
      const url = new URL(String(value), baseUrl);
      if (!/^https?:$/.test(url.protocol)) return null;
      if (isPrivateHostname(url.hostname)) return null;
      url.hash = "";
      return url.href;
    } catch {
      return null;
    }
  }

  function isPrivateHostname(value) {
    const host = String(value || "").replace(/^\[|\]$/g, "").toLowerCase();
    if (!host || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) return true;
    if (host === "::1" || host.startsWith("fe8") || host.startsWith("fe9") ||
        host.startsWith("fea") || host.startsWith("feb") || host.startsWith("fc") || host.startsWith("fd")) {
      return true;
    }
    const parts = host.split(".");
    if (parts.length !== 4 || parts.some((part) => !/^\d{1,3}$/.test(part) || Number(part) > 255)) return false;
    const [a, b] = parts.map(Number);
    return a === 0 || a === 10 || a === 127 ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168);
  }

  function extensionOf(url) {
    try {
      const name = new URL(url).pathname.split("/").pop() || "";
      return name.includes(".") ? name.split(".").pop().toLowerCase() : "";
    } catch {
      return "";
    }
  }

  function classify(url, hint = "", mimeType = "") {
    const normalizedHint = String(hint).toLowerCase();
    const normalizedMime = String(mimeType).toLowerCase();
    const ext = extensionOf(url);
    if (normalizedHint === "image" || normalizedMime.startsWith("image/") || IMAGE_EXTENSIONS.has(ext)) {
      return "image";
    }
    if (normalizedHint === "stream" || normalizedMime.includes("mpegurl") || ext === "m3u8") {
      return "stream";
    }
    if (normalizedHint === "video" || normalizedMime.startsWith("video/") || VIDEO_EXTENSIONS.has(ext)) {
      return "video";
    }
    return "unknown";
  }

  function shouldIgnore(url) {
    const ext = extensionOf(url);
    return Boolean(ext && IGNORE_EXTENSIONS.has(ext));
  }

  function normalizeCandidate(candidate, baseUrl) {
    const url = asHttpUrl(candidate?.url, baseUrl);
    if (!url || shouldIgnore(url)) return null;
    const kind = classify(url, candidate?.kind, candidate?.mimeType);
    if (kind === "unknown" && !candidate?.trustedHint) return null;
    return {
      url,
      kind,
      source: String(candidate?.source || "page"),
      mimeType: String(candidate?.mimeType || ""),
      thumbnail: asHttpUrl(candidate?.thumbnail, baseUrl),
    };
  }

  function safeFilename(value, kind = "video") {
    const fallback = kind === "image" ? "saveflow-image" : "saveflow-video";
    const cleaned = String(value || fallback)
      .replace(/[\\/:*?"<>|\r\n\t]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 100);
    return cleaned || fallback;
  }

  function filenameFromUrl(url, kind = "video", index = 0) {
    try {
      const raw = decodeURIComponent(new URL(url).pathname.split("/").pop() || "");
      if (raw && raw.includes(".")) return safeFilename(raw, kind);
    } catch {
      // Fall through to a deterministic generic name.
    }
    const ext = kind === "image" ? "jpg" : kind === "stream" ? "m3u8" : "mp4";
    return `${kind === "image" ? "saveflow-image" : "saveflow-video"}-${index + 1}.${ext}`;
  }

  const api = {
    asHttpUrl,
    classify,
    extensionOf,
    filenameFromUrl,
    isPrivateHostname,
    normalizeCandidate,
    safeFilename,
  };

  global.SaveflowMedia = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(globalThis);
