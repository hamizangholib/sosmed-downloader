/* =========================================================================
   Saveflow — frontend logic
   ========================================================================= */

/* -------------------------------------------------------------------------
   CONFIGURATION
   -------------------------------------------------------------------------
   Point this at your deployed backend, WITHOUT a trailing slash.

   URL shape depends on where you deployed it:
       Vercel        https://<project-name>.vercel.app
       Render        https://<service-name>.onrender.com
       Cloud Run     https://<service>-<hash>-<region>.a.run.app
       Koyeb         https://<service>-<org>.koyeb.app
       Hugging Face  https://<username>-<space-name>.hf.space

   Example:
       const API_BASE_URL = "https://saveflow-api.vercel.app";

   For local development against `uvicorn main:app --port 7860`, use:
       const API_BASE_URL = "http://127.0.0.1:7860";
   ------------------------------------------------------------------------- */
const API_BASE_URL = ["localhost", "127.0.0.1"].includes(location.hostname)
  ? "http://127.0.0.1:8765"
  : "https://saveflow-ten.vercel.app/";

// A trailing slash here would produce `...app//api/extract`, which is a
// different route and 404s. Strip it once so either spelling works.
const API_ROOT = API_BASE_URL.replace(/\/+$/, "");
const HELPER_INSTALL_URL = "https://github.com/hamizangholib/sosmed-downloader/tree/main/browser-extension";

// ---- Element handles -------------------------------------------------------
const form = document.getElementById("downloadForm");
const urlInput = document.getElementById("urlInput");
const pasteBtn = document.getElementById("pasteBtn");
const submitBtn = document.getElementById("submitBtn");
const loadingState = document.getElementById("loadingState");
const results = document.getElementById("results");
const errorAlert = document.getElementById("errorAlert");
const errorText = document.getElementById("errorText");
const errorClose = document.getElementById("errorClose");
const header = document.getElementById("siteHeader");
const nav = document.getElementById("siteNav");
const navToggle = document.getElementById("navToggle");
let helperReady = false;
let helperSessionUrl = null;

// ---- Small helpers ---------------------------------------------------------

/** Seconds to `m:ss` / `h:mm:ss`. Returns null when duration is unknown. */
function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return null;
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** Escape user/API-supplied strings before they touch innerHTML. */
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/** Turn a title into a safe-ish filename for the download attribute. */
function toFilename(title, ext) {
  let base = String(title || "saveflow")
    .replace(/[\\/:*?"<>|\n\r]+/g, " ")
    .trim()
    .slice(0, 80) || "saveflow";
  const suffix = `.${ext || "mp4"}`;
  if (base.toLowerCase().endsWith(suffix.toLowerCase())) base = base.slice(0, -suffix.length);
  return `${base}.${ext || "mp4"}`;
}

function showError(message) {
  errorText.textContent = message;
  errorAlert.hidden = false;
}

function clearError() {
  errorAlert.hidden = true;
}

function postToHelper(type, payload = {}) {
  window.postMessage({ source: "saveflow-web", type, ...payload }, location.origin);
}

function mediaExtension(url, kind) {
  const allowed = {
    image: new Set(["avif", "bmp", "gif", "heic", "jpeg", "jpg", "png", "svg", "tif", "tiff", "webp"]),
    stream: new Set(["m3u8"]),
    video: new Set(["avi", "m4v", "mkv", "mov", "mp4", "webm"]),
  };
  try {
    const file = new URL(url).pathname.split("/").pop() || "";
    const ext = file.includes(".") ? file.split(".").pop().toLowerCase() : "";
    if (allowed[kind]?.has(ext)) return ext;
  } catch {
    // Use a sensible display fallback below.
  }
  return kind === "image" ? "jpg" : kind === "stream" ? "m3u8" : "mp4";
}

function helperData(message) {
  const candidates = (message.candidates || []).flatMap((candidate, index) => {
    try {
      const url = new URL(candidate.url);
      if (!/^https?:$/.test(url.protocol)) return [];
      const kind = ["image", "stream", "video"].includes(candidate.kind)
        ? candidate.kind
        : "video";
      const ext = mediaExtension(url.href, kind);
      const pathName = decodeURIComponent(url.pathname.split("/").pop() || "");
      const title = pathName || `Detected ${kind} ${index + 1}`;
      return [{
        index,
        helper: true,
        title,
        uploader: url.hostname,
        duration: null,
        thumbnail: kind === "image" ? url.href : candidate.thumbnail,
        thumbnail_proxy: false,
        formats: [{
          format_id: `helper-${index}`,
          label: kind === "image" ? "Original image" : kind === "stream" ? "HLS stream" : "Detected video",
          ext,
          kind,
          direct_url: url.href,
        }],
      }];
    } catch {
      return [];
    }
  });
  return {
    platform: "Browser helper",
    source_url: message.pageUrl || message.sourceUrl,
    title: message.title || "Detected media",
    items: candidates,
    folders: [],
  };
}

window.addEventListener("message", (event) => {
  if (event.source !== window || event.origin !== location.origin || event.data?.source !== "saveflow-helper") return;
  if (event.data.type === "SAVEFLOW_HELPER_READY") {
    helperReady = true;
    document.querySelectorAll(".helper-state").forEach((node) => {
      node.textContent = "Helper ready";
    });
    return;
  }
  if (event.data.type === "SAVEFLOW_HELPER_ERROR") {
    showError(event.data.error || "Saveflow Helper could not complete that action.");
    return;
  }
  if (event.data.type === "SAVEFLOW_HELPER_RESULTS") {
    const data = helperData(event.data);
    if (data.items.length) {
      clearError();
      renderResults(data, event.data.sourceUrl || helperSessionUrl);
    } else {
      const count = document.querySelector(".helper-waiting-count");
      if (count) count.textContent = "No media yet — click or play something in the source tab.";
    }
  }
});

postToHelper("SAVEFLOW_HELPER_PING");
setTimeout(() => postToHelper("SAVEFLOW_HELPER_PING"), 800);

const loadingNote = loadingState.querySelector(".skeleton-note");
const DEFAULT_NOTE = loadingNote.textContent;
let wakeHintTimer = null;

function setLoading(isLoading) {
  loadingState.hidden = !isLoading;
  submitBtn.disabled = isLoading;
  submitBtn.querySelector(".btn-label").textContent = isLoading ? "Working…" : "Download";

  clearTimeout(wakeHintTimer);
  loadingNote.textContent = DEFAULT_NOTE;

  if (isLoading) {
    // Free hosting tiers idle the container out. The first request after a nap
    // spends ~30-60s waking it, which otherwise looks like a hang.
    wakeHintTimer = setTimeout(() => {
      loadingNote.textContent =
        "Still working — the server may be waking up from idle. This only happens on the first request.";
    }, 8000);
  }
}

// ---- Paste button ----------------------------------------------------------
pasteBtn.addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      urlInput.value = text.trim();
      pasteBtn.classList.add("is-done");
      setTimeout(() => pasteBtn.classList.remove("is-done"), 1200);
    }
  } catch {
    // Clipboard read needs permission and a secure context (https/localhost).
    // Focus the field so the user can paste manually instead.
    urlInput.focus();
    showError("Clipboard access was blocked by the browser — paste the link manually with Ctrl+V.");
  }
});

errorClose.addEventListener("click", clearError);

// ---- Rendering -------------------------------------------------------------

function downloadUrl(sourceUrl, item, format) {
  return `${API_ROOT}/api/download` +
    `?url=${encodeURIComponent(sourceUrl)}` +
    `&index=${encodeURIComponent(item.index ?? 0)}` +
    `&format_id=${encodeURIComponent(format?.format_id || "")}`;
}

/** Build one result card using only the formats from the active media tab. */
function renderCard(item, formats, platform, index, total, sourceUrl) {
  const card = document.createElement("article");
  card.className = "result-card";

  const duration = formatDuration(item.duration);
  const slideLabel = total > 1 ? `<span class="tag tag-muted">Item ${index + 1} of ${total}</span>` : "";
  const proxiedThumbnail = `${API_ROOT}/api/thumbnail?url=${encodeURIComponent(sourceUrl)}&index=${encodeURIComponent(item.index ?? 0)}`;
  const thumbnailUrl = item.thumbnail_proxy ? proxiedThumbnail : item.thumbnail;
  const previewFormat = formats.find((fmt) => fmt.kind === "image") ||
    formats.find((fmt) => fmt.kind === "video");

  const directPreview = previewFormat?.direct_url;
  const thumb = thumbnailUrl
    ? `<div class="result-thumb">
         <img src="${escapeHtml(thumbnailUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer"
              data-fallback-thumbnail="${escapeHtml(item.helper || item.thumbnail_proxy ? "" : proxiedThumbnail)}" />
         ${duration ? `<span class="result-duration">${duration}</span>` : ""}
       </div>`
    : previewFormat && previewFormat.kind !== "stream"
      ? `<div class="result-thumb">
           ${previewFormat.kind === "image"
             ? `<img src="${escapeHtml(directPreview || downloadUrl(sourceUrl, item, previewFormat))}" alt="" loading="lazy" referrerpolicy="no-referrer" />`
             : `<video src="${escapeHtml(directPreview || downloadUrl(sourceUrl, item, previewFormat))}" controls muted preload="metadata" playsinline></video>`}
           ${duration ? `<span class="result-duration">${duration}</span>` : ""}
         </div>`
    : `<div class="result-thumb is-empty">No preview</div>`;

  const formatButtons = formats.length
    ? formats.map((fmt, i) => {
      const size = fmt.filesize ? `<small>${escapeHtml(fmt.filesize)}</small>` : "";
      const icon = { audio: "♪", image: "▣", stream: "≋" }[fmt.kind] || "↓";
      if (fmt.direct_url) {
        return `<button type="button" class="format-btn helper-download ${i === 0 ? "is-primary" : ""}"
                  data-helper-url="${escapeHtml(fmt.direct_url)}"
                  data-helper-filename="${escapeHtml(toFilename(item.title, fmt.ext))}">
                  <span aria-hidden="true">${icon}</span>
                  ${escapeHtml(fmt.label)} · ${escapeHtml(fmt.ext)} ${size}
                </button>`;
      }
      const href = downloadUrl(sourceUrl, item, fmt);
      return `<a class="format-btn ${i === 0 ? "is-primary" : ""}"
                 href="${escapeHtml(href)}"
                 download="${escapeHtml(toFilename(item.title, fmt.ext))}">
                <span aria-hidden="true">${icon}</span>
                ${escapeHtml(fmt.label)} · ${escapeHtml(fmt.ext)} ${size}
              </a>`;
      }).join("")
    : `<p class="preview-only">${escapeHtml(item.warning || "Preview available, but this source blocked direct download from the server.")}</p>`;

  card.innerHTML = `
    ${thumb}
    <div class="result-body">
      <div class="result-meta">
        <span class="tag">${escapeHtml(platform)}</span>
        ${slideLabel}
      </div>
      <h3 class="result-title">${escapeHtml(item.title)}</h3>
      ${item.uploader ? `<p class="result-uploader">by ${escapeHtml(item.uploader)}</p>` : ""}
      <div class="format-list">${formatButtons}</div>
    </div>`;

  const image = card.querySelector("img");
  image?.addEventListener("error", () => {
    const fallback = image.dataset.fallbackThumbnail;
    if (fallback) {
      delete image.dataset.fallbackThumbnail;
      image.src = fallback;
      return;
    }
    image.parentElement.classList.add("is-empty");
    image.remove();
  });
  const video = card.querySelector("video");
  video?.addEventListener("error", () => {
    video.parentElement.classList.add("is-empty");
    video.remove();
  });

  return card;
}

function renderFolders(data) {
  const section = document.createElement("section");
  section.className = "folder-panel";
  const parent = data.parent_url
    ? `<button type="button" class="folder-btn folder-back" data-folder-url="${escapeHtml(data.parent_url)}">
         <span aria-hidden="true">←</span><span>Back</span>
       </button>`
    : "";
  const folders = (data.folders || []).map((folder) => `
    <button type="button" class="folder-btn" data-folder-url="${escapeHtml(folder.url)}">
      <span class="folder-icon" aria-hidden="true">▰</span>
      <span>${escapeHtml(folder.title)}</span>
    </button>`).join("");
  section.innerHTML = `
    <div class="folder-heading">
      <span class="tag">Folder</span>
      <h3>${escapeHtml(data.title || "Media folder")}</h3>
    </div>
    <div class="folder-grid">${parent}${folders}</div>`;
  return section;
}

function renderPagePreview(preview) {
  const section = document.createElement("section");
  section.className = "page-preview-panel";
  section.innerHTML = `
    <div class="page-preview-heading">
      <div>
        <span class="tag">Source page</span>
        <h3>${escapeHtml(preview.title || "No direct media detected")}</h3>
      </div>
      <div class="page-preview-actions">
        <button type="button" class="page-helper" data-helper-url="${escapeHtml(preview.url)}">Detect from browser tab</button>
        <button type="button" class="page-rescan" data-rescan-url="${escapeHtml(preview.url)}">Scan again</button>
        <a href="${escapeHtml(preview.url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a>
      </div>
    </div>
    <p class="page-preview-note">No downloadable file was exposed yet. Use the browser helper to open the source, interact with it, and return detected media automatically. <span class="helper-state">${helperReady ? "Helper ready" : `Helper not detected · <a href="${HELPER_INSTALL_URL}" target="_blank" rel="noopener noreferrer">setup</a>`}</span>.</p>
    <iframe src="${escapeHtml(preview.url)}" title="${escapeHtml(preview.title || "Source page")}"
            sandbox="allow-scripts allow-presentation" allow="autoplay; fullscreen"
            loading="lazy" referrerpolicy="no-referrer"></iframe>`;
  return section;
}

function renderHelperFallback(url, reason) {
  const section = document.createElement("section");
  section.className = "helper-panel";
  section.innerHTML = `
    <div>
      <span class="tag">Browser fallback</span>
      <h3>Try interactive detection</h3>
      <p>${escapeHtml(reason || "The server did not expose media from this page.")}</p>
    </div>
    <button type="button" class="page-helper" data-helper-url="${escapeHtml(url)}">Detect from browser tab</button>
    <small class="helper-state">${helperReady ? "Helper ready" : `Helper not detected · <a href="${HELPER_INSTALL_URL}" target="_blank" rel="noopener noreferrer">setup instructions</a>`}</small>`;
  results.innerHTML = "";
  results.appendChild(section);
  results.hidden = false;
}

function renderHelperWaiting(url) {
  helperSessionUrl = url;
  const section = document.createElement("section");
  section.className = "helper-panel helper-waiting";
  section.innerHTML = `
    <div>
      <span class="tag">Browser helper</span>
      <h3>Interact with the source tab</h3>
      <p class="helper-waiting-count">Waiting for media. Press play, open a folder, or click the content you want.</p>
    </div>`;
  results.innerHTML = "";
  results.appendChild(section);
  results.hidden = false;
}

function renderMedia(data, sourceUrl) {
  const items = data.items || [];
  const groups = [
    {
      key: "video",
      label: "Video",
      entries: items.map((item) => ({
        item,
        formats: (item.formats || []).filter((fmt) => fmt.kind !== "image"),
      })).filter(({ item, formats }) => formats.length || (!(item.formats || []).length && item.thumbnail)),
    },
    {
      key: "photo",
      label: "Photo",
      entries: items.map((item) => ({
        item,
        formats: (item.formats || []).filter((fmt) => fmt.kind === "image"),
      })).filter(({ formats }) => formats.length),
    },
  ].filter((group) => group.entries.length);

  if (!groups.length) return null;
  const section = document.createElement("section");
  section.className = "media-results";
  const tabs = document.createElement("div");
  tabs.className = "media-tabs";
  tabs.setAttribute("role", "tablist");
  groups.forEach((group, groupIndex) => {
    const active = groupIndex === 0;
    tabs.insertAdjacentHTML("beforeend", `
      <button type="button" class="media-tab ${active ? "is-active" : ""}"
              role="tab" aria-selected="${active}" data-media-tab="${group.key}">
        ${group.label} <span>${group.entries.length}</span>
      </button>`);
    const pane = document.createElement("div");
    pane.className = "media-pane";
    pane.dataset.mediaPane = group.key;
    pane.hidden = !active;
    group.entries.forEach(({ item, formats }, index) => {
      pane.appendChild(renderCard(item, formats, data.platform, index, group.entries.length, sourceUrl));
    });
    section.appendChild(pane);
  });
  section.prepend(tabs);
  return section;
}

function renderResults(data, requestedUrl) {
  results.innerHTML = "";
  const sourceUrl = data.source_url || requestedUrl;
  if ((data.folders || []).length || data.parent_url) {
    results.appendChild(renderFolders(data));
  }
  const media = renderMedia(data, sourceUrl);
  if (media) results.appendChild(media);
  if (data.page_preview) {
    results.appendChild(renderPagePreview(data.page_preview));
  }
  results.hidden = !results.children.length;
  if (results.children.length) {
    results.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

// The download endpoint re-resolves the post before any bytes arrive, so the
// browser sits silent for a few seconds. Acknowledge the click so it does not
// look ignored. There is no completion event for a plain navigation download,
// so the label simply reverts on a timer.
results.addEventListener("click", async (event) => {
  const helper = event.target.closest(".page-helper");
  if (helper) {
    if (!helperReady) {
      showError("Saveflow Helper is not installed. Load the browser-extension folder from chrome://extensions or edge://extensions first.");
      return;
    }
    clearError();
    renderHelperWaiting(helper.dataset.helperUrl);
    postToHelper("SAVEFLOW_HELPER_OPEN", { url: helper.dataset.helperUrl });
    return;
  }

  const helperDownload = event.target.closest(".helper-download");
  if (helperDownload) {
    const original = helperDownload.innerHTML;
    helperDownload.disabled = true;
    helperDownload.innerHTML = '<span aria-hidden="true">⏳</span> Starting…';
    postToHelper("SAVEFLOW_HELPER_DOWNLOAD", {
      url: helperDownload.dataset.helperUrl,
      filename: helperDownload.dataset.helperFilename,
    });
    setTimeout(() => {
      helperDownload.innerHTML = original;
      helperDownload.disabled = false;
    }, 2500);
    return;
  }

  const rescan = event.target.closest(".page-rescan");
  if (rescan) {
    await loadMedia(rescan.dataset.rescanUrl);
    return;
  }

  const tab = event.target.closest(".media-tab");
  if (tab) {
    const key = tab.dataset.mediaTab;
    results.querySelectorAll(".media-tab").forEach((button) => {
      const active = button === tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    results.querySelectorAll(".media-pane").forEach((pane) => {
      pane.hidden = pane.dataset.mediaPane !== key;
    });
    return;
  }

  const folder = event.target.closest(".folder-btn");
  if (folder) {
    await loadMedia(folder.dataset.folderUrl);
    return;
  }

  const btn = event.target.closest(".format-btn");
  if (!btn || btn.dataset.busy) return;
  const original = btn.innerHTML;
  btn.dataset.busy = "1";
  btn.innerHTML = '<span aria-hidden="true">⏳</span> Preparing…';
  setTimeout(() => {
    btn.innerHTML = original;
    delete btn.dataset.busy;
  }, 6000);
});

async function loadMedia(url) {
  clearError();
  results.hidden = true;
  results.innerHTML = "";
  setLoading(true);
  try {
    const response = await fetch(`${API_ROOT}/api/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(payload?.detail || `Request failed with status ${response.status}.`);
    }
    renderResults(payload, url);
  } catch (err) {
    const message = err instanceof TypeError
      ? "Could not reach the Saveflow API. Check that the backend is running and API_BASE_URL is correct."
      : err.message;
    showError(message);
    renderHelperFallback(url, message);
  } finally {
    setLoading(false);
  }
}

// ---- Submit ----------------------------------------------------------------
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = urlInput.value.trim();

  if (!url) {
    showError("Paste a link first.");
    urlInput.focus();
    return;
  }
  if (!/^https?:\/\//i.test(url)) {
    showError("That does not look like a link. It should start with http:// or https://");
    return;
  }
  if (API_BASE_URL === "YOUR_BACKEND_URL") {
    showError("Backend not configured yet — set API_BASE_URL at the top of script.js to your deployed API URL.");
    return;
  }

  await loadMedia(url);
});

// ---- Chrome: sticky header shadow + mobile nav -----------------------------
window.addEventListener("scroll", () => {
  header.classList.toggle("is-scrolled", window.scrollY > 10);
}, { passive: true });

navToggle.addEventListener("click", () => {
  const open = nav.classList.toggle("is-open");
  navToggle.setAttribute("aria-expanded", String(open));
});

nav.addEventListener("click", (event) => {
  if (event.target.tagName === "A") {
    nav.classList.remove("is-open");
    navToggle.setAttribute("aria-expanded", "false");
  }
});
