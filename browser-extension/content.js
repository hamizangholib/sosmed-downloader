"use strict";

const SAVEFLOW_HOSTS = new Set([
  "download.xsaintz.my.id",
  "hamizangholib.github.io",
  "localhost",
  "127.0.0.1",
]);
const isSaveflowPage = SAVEFLOW_HOSTS.has(location.hostname);
const candidates = new Map();
const MAX_LOCAL_CANDIDATES = 300;
let sessionActive = false;
let scannerStarted = false;
let scanTimer = null;
let sendTimer = null;

function addCandidate(url, kind, source, extra = {}) {
  const candidate = SaveflowMedia.normalizeCandidate({
    url,
    kind,
    source,
    trustedHint: Boolean(kind),
    ...extra,
  }, location.href);
  if (!candidate) return;
  if (!candidates.has(candidate.url) && candidates.size >= MAX_LOCAL_CANDIDATES) return;
  const previous = candidates.get(candidate.url);
  candidates.set(candidate.url, {
    ...previous,
    ...candidate,
    thumbnail: candidate.thumbnail || previous?.thumbnail || null,
  });
}

function addSrcset(value, kind, source) {
  for (const entry of String(value || "").split(",")) {
    addCandidate(entry.trim().split(/\s+/)[0], kind, source);
  }
}

function scanDom() {
  document.querySelectorAll("video").forEach((video) => {
    addCandidate(video.currentSrc || video.src, "video", "dom-video", { thumbnail: video.poster });
    addCandidate(video.poster, "image", "video-poster");
  });
  document.querySelectorAll("video source, source[type^='video/']").forEach((source) => {
    addCandidate(source.src, "video", "dom-source", { mimeType: source.type });
    addSrcset(source.srcset, "video", "dom-source");
  });
  document.querySelectorAll("img").forEach((image) => {
    addCandidate(image.currentSrc || image.src, "image", "dom-image");
    addSrcset(image.srcset, "image", "dom-srcset");
  });
  document.querySelectorAll("picture source").forEach((source) => {
    addSrcset(source.srcset, "image", "dom-picture");
  });
  document.querySelectorAll("meta[property], meta[name]").forEach((meta) => {
    const key = (meta.getAttribute("property") || meta.getAttribute("name") || "").toLowerCase();
    if (/^(og:image|twitter:image)/.test(key)) addCandidate(meta.content, "image", "meta");
    if (/^(og:video|twitter:player:stream)/.test(key)) addCandidate(meta.content, "video", "meta");
  });
  document.querySelectorAll("a[href]").forEach((link) => {
    addCandidate(link.href, "", "dom-link");
  });
  document.querySelectorAll("[style*='background']").forEach((element) => {
    const value = getComputedStyle(element).backgroundImage;
    for (const match of value.matchAll(/url\(["']?(.*?)["']?\)/g)) {
      addCandidate(match[1], "image", "css-background");
    }
  });
  performance.getEntriesByType("resource").forEach((entry) => {
    addCandidate(entry.name, "", "performance");
  });
  scheduleSend();
}

function scheduleScan(delay = 250) {
  clearTimeout(scanTimer);
  scanTimer = setTimeout(scanDom, delay);
}

function scheduleSend() {
  if (!sessionActive) return;
  clearTimeout(sendTimer);
  sendTimer = setTimeout(() => {
    chrome.runtime.sendMessage({
      type: "SAVEFLOW_HELPER_CANDIDATES",
      pageUrl: location.href,
      title: document.title,
      candidates: [...candidates.values()],
    }).then((response) => {
      if (response?.ok) updateOverlay(response.count);
    }).catch(() => {});
  }, 180);
}

function ensureOverlay() {
  if (document.getElementById("saveflow-helper-overlay")) return;
  const overlay = document.createElement("div");
  overlay.id = "saveflow-helper-overlay";
  overlay.style.cssText = [
    "position:fixed", "right:16px", "bottom:16px", "z-index:2147483647",
    "display:flex", "align-items:center", "gap:10px", "padding:10px 12px",
    "background:#141a24", "color:#eef2f8", "border:1px solid #4b8ef1",
    "border-radius:12px", "box-shadow:0 12px 30px rgba(0,0,0,.4)",
    "font:13px system-ui,sans-serif",
  ].join(";");
  overlay.innerHTML = '<span data-saveflow-count>Saveflow: scanning…</span><button type="button" style="padding:7px 10px;border:0;border-radius:999px;background:#4b8ef1;color:white;cursor:pointer">Return</button>';
  overlay.querySelector("button").addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "SAVEFLOW_HELPER_RETURN" }).catch(() => {});
  });
  document.documentElement.appendChild(overlay);
}

function updateOverlay(count) {
  ensureOverlay();
  const label = document.querySelector("#saveflow-helper-overlay [data-saveflow-count]");
  if (label) label.textContent = `Saveflow: ${count} media found`;
}

if (isSaveflowPage) {
  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.source !== "saveflow-web") return;
    if (event.data.type === "SAVEFLOW_HELPER_PING") {
      window.postMessage({ source: "saveflow-helper", type: "SAVEFLOW_HELPER_READY" }, location.origin);
    }
    if (event.data.type === "SAVEFLOW_HELPER_OPEN") {
      chrome.runtime.sendMessage({ type: "SAVEFLOW_HELPER_OPEN", url: event.data.url })
        .then((response) => window.postMessage({
          source: "saveflow-helper",
          type: response?.ok ? "SAVEFLOW_HELPER_OPENED" : "SAVEFLOW_HELPER_ERROR",
          error: response?.error,
        }, location.origin))
        .catch((error) => window.postMessage({
          source: "saveflow-helper", type: "SAVEFLOW_HELPER_ERROR", error: error.message,
        }, location.origin));
    }
    if (event.data.type === "SAVEFLOW_HELPER_DOWNLOAD") {
      chrome.runtime.sendMessage({
        type: "SAVEFLOW_HELPER_DOWNLOAD",
        url: event.data.url,
        filename: event.data.filename,
      }).then((response) => window.postMessage({
        source: "saveflow-helper",
        type: response?.ok ? "SAVEFLOW_HELPER_DOWNLOAD_STARTED" : "SAVEFLOW_HELPER_ERROR",
        error: response?.error,
      }, location.origin)).catch(() => {});
    }
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type !== "SAVEFLOW_HELPER_RESULTS") return;
    window.postMessage({ source: "saveflow-helper", ...message }, location.origin);
  });

  window.postMessage({ source: "saveflow-helper", type: "SAVEFLOW_HELPER_READY" }, location.origin);
} else {
  const startSourceScanner = () => {
    if (scannerStarted) return;
    scannerStarted = true;
    sessionActive = true;
    ensureOverlay();

    const observer = new MutationObserver(() => scheduleScan());
    const startObserver = () => {
      if (!document.documentElement) return;
      observer.observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["src", "srcset", "href", "poster", "style"],
      });
      scanDom();
    };
    if (document.documentElement) startObserver();
    else document.addEventListener("DOMContentLoaded", startObserver, { once: true });
    document.addEventListener("click", () => {
      scheduleScan(500);
      setTimeout(scanDom, 1500);
    }, true);
  };

  chrome.runtime.sendMessage({ type: "SAVEFLOW_HELPER_SESSION" }).then((response) => {
    if (response?.session) startSourceScanner();
  }).catch(() => {});

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "SAVEFLOW_HELPER_STATUS") {
      startSourceScanner();
      updateOverlay(message.count || 0);
    }
  });
}
