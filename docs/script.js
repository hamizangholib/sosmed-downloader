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
const API_BASE_URL = "https://saveflow-ten.vercel.app/";

// A trailing slash here would produce `...app//api/extract`, which is a
// different route and 404s. Strip it once so either spelling works.
const API_ROOT = API_BASE_URL.replace(/\/+$/, "");

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
  const base = String(title || "saveflow")
    .replace(/[\\/:*?"<>|\n\r]+/g, " ")
    .trim()
    .slice(0, 80) || "saveflow";
  return `${base}.${ext || "mp4"}`;
}

function showError(message) {
  errorText.textContent = message;
  errorAlert.hidden = false;
}

function clearError() {
  errorAlert.hidden = true;
}

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

/** Build one result card. `index` is only used to label carousel slides. */
function renderCard(item, platform, index, total, sourceUrl) {
  const card = document.createElement("article");
  card.className = "result-card";

  const duration = formatDuration(item.duration);
  const slideLabel = total > 1 ? `<span class="tag tag-muted">Item ${index + 1} of ${total}</span>` : "";

  const thumb = item.thumbnail
    ? `<div class="result-thumb">
         <img src="${escapeHtml(item.thumbnail)}" alt="" loading="lazy"
              onerror="this.parentElement.classList.add('is-empty'); this.remove();" />
         ${duration ? `<span class="result-duration">${duration}</span>` : ""}
       </div>`
    : `<div class="result-thumb is-empty">No preview</div>`;

  const formats = item.formats
    .map((fmt, i) => {
      const size = fmt.filesize ? `<small>${escapeHtml(fmt.filesize)}</small>` : "";
      const icon = { audio: "♪", image: "▣", stream: "≋" }[fmt.kind] || "↓";
      // Point at our own endpoint rather than the CDN link. It re-resolves the
      // post (CDN URLs expire), replays the headers the CDN demands, and sends
      // Content-Disposition: attachment so the browser saves instead of playing.
      const href =
        `${API_ROOT}/api/download` +
        `?url=${encodeURIComponent(sourceUrl)}` +
        `&index=${encodeURIComponent(item.index ?? 0)}` +
        `&format_id=${encodeURIComponent(fmt.format_id || "")}`;
      return `<a class="format-btn ${i === 0 ? "is-primary" : ""}"
                 href="${escapeHtml(href)}"
                 download="${escapeHtml(toFilename(item.title, fmt.ext))}">
                <span aria-hidden="true">${icon}</span>
                ${escapeHtml(fmt.label)} · ${escapeHtml(fmt.ext)} ${size}
              </a>`;
    })
    .join("");

  card.innerHTML = `
    ${thumb}
    <div class="result-body">
      <div class="result-meta">
        <span class="tag">${escapeHtml(platform)}</span>
        ${slideLabel}
      </div>
      <h3 class="result-title">${escapeHtml(item.title)}</h3>
      ${item.uploader ? `<p class="result-uploader">by ${escapeHtml(item.uploader)}</p>` : ""}
      <div class="format-list">${formats}</div>
    </div>`;

  return card;
}

function renderResults(data, requestedUrl) {
  results.innerHTML = "";
  const items = data.items || [];
  const sourceUrl = data.source_url || requestedUrl;
  items.forEach((item, i) => {
    results.appendChild(renderCard(item, data.platform, i, items.length, sourceUrl));
  });
  results.hidden = items.length === 0;
  if (items.length) {
    results.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

// The download endpoint re-resolves the post before any bytes arrive, so the
// browser sits silent for a few seconds. Acknowledge the click so it does not
// look ignored. There is no completion event for a plain navigation download,
// so the label simply reverts on a timer.
results.addEventListener("click", (event) => {
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

// ---- Submit ----------------------------------------------------------------
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  results.hidden = true;
  results.innerHTML = "";

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

  setLoading(true);

  try {
    const response = await fetch(`${API_ROOT}/api/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });

    // FastAPI reports failures as {"detail": "..."} — surface that text as-is.
    const payload = await response.json().catch(() => null);

    if (!response.ok) {
      throw new Error(payload?.detail || `Request failed with status ${response.status}.`);
    }

    renderResults(payload, url);
  } catch (err) {
    // A network-level failure has no response body; name the likely cause.
    const message =
      err instanceof TypeError
        ? "Could not reach the Saveflow API. Check that the backend is running and API_BASE_URL is correct."
        : err.message;
    showError(message);
  } finally {
    setLoading(false);
  }
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
