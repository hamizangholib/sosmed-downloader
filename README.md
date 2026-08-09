# Saveflow

Social media downloader. Paste a link from **TikTok, Instagram, Facebook, X (Twitter) or Threads** and get direct download links for the video, photo or audio.

- **Backend** — Python 3.10 + FastAPI + `yt-dlp`, containerised and deployable to any Docker host.
- **Frontend** — plain HTML/CSS/vanilla JS, static-hostable on GitHub Pages.

```
.
├── backend/
│   ├── main.py            # FastAPI app: GET / and POST /api/extract
│   ├── requirements.txt
│   └── Dockerfile         # python:3.10-slim + ffmpeg, port from $PORT (default 7860)
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── script.js          # API_BASE_URL lives at the top of this file
└── README.md
```

---

## 1. Run the backend locally

```bash
cd backend
python -m venv .venv
```

Activate the virtualenv — `.venv\Scripts\activate` on Windows, `source .venv/bin/activate` on macOS/Linux — then:

```bash
pip install -r requirements.txt
```

`ffmpeg` must be on your PATH. macOS: `brew install ffmpeg`. Debian/Ubuntu: `sudo apt install ffmpeg`. Windows: `winget install Gyan.FFmpeg`.

Start the server:

```bash
uvicorn main:app --reload --port 7860
```

Check it:

- Healthcheck — <http://127.0.0.1:7860/>
- Interactive docs — <http://127.0.0.1:7860/docs>

Try an extraction from the terminal:

```bash
curl -X POST http://127.0.0.1:7860/api/extract -H "Content-Type: application/json" -d "{\"url\":\"https://www.tiktok.com/@user/video/1234567890\"}"
```

### Running it with Docker instead

```bash
docker build -t saveflow-api ./backend
```

```bash
docker run --rm -p 7860:7860 saveflow-api
```

---

## 2. Deploy the backend

The backend needs a host that runs a container — `yt-dlp` and `ffmpeg` cannot run on a static host. Hugging Face **Docker Spaces now require a paid plan**, so the free route is elsewhere.

The `Dockerfile` reads the port from the `PORT` environment variable and falls back to `7860`, so the same image runs unchanged on every host below.

| Host | Free? | Card needed | Sleeps when idle | Notes |
| --- | --- | --- | --- | --- |
| **Render** | Yes, 750 h/month | No | Yes, after ~15 min | Easiest. Reads the `Dockerfile` directly. |
| **Google Cloud Run** | Yes, 2M requests/month | Yes | Scales to zero | Fastest cold starts, but billing must be enabled. |
| **Koyeb** | One free service | Sometimes | No | Good middle ground. |
| **Oracle Cloud** | Always Free ARM VM | Yes | No | Most generous, but you administer the VM yourself. |
| **Hugging Face** | Docker requires PRO | — | Yes | Only worth it if you already pay for PRO. |

Plan terms change often — check the host's current pricing page before committing.

### 2a. Render (recommended)

1. Push this repository to GitHub (see step 3b if you have not yet).
2. Go to <https://dashboard.render.com/> → **New** → **Web Service**, and connect the repository.
3. Configure:
   - **Language / Runtime**: `Docker`
   - **Root Directory**: `backend`
   - **Instance Type**: `Free`
4. Click **Deploy**. The first build takes a few minutes because `ffmpeg` is installed into the image.
5. Your API goes live at `https://<service-name>.onrender.com` — open it and you should see the healthcheck JSON.

Render injects `PORT` automatically; do not set it yourself.

### 2b. Google Cloud Run

With the [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and a project selected:

```bash
gcloud run deploy saveflow-api --source ./backend --region asia-southeast2 --allow-unauthenticated --memory 1Gi
```

Cloud Run builds the `Dockerfile`, injects `PORT=8080` and prints the live URL when it finishes. Raise `--memory` if extraction of large videos gets killed.

### 2c. Hugging Face Spaces (needs a PRO plan)

1. Go to <https://huggingface.co/new-space>, pick SDK **Docker** → **Blank**, visibility **Public**.
2. Clone the empty Space:

```bash
git clone https://huggingface.co/spaces/<your-username>/saveflow-api
```

3. Copy `main.py`, `requirements.txt` and `Dockerfile` from `backend/` into the clone — the `Dockerfile` **must** sit at the repository root, since that is where Spaces looks for it.
4. Commit and push:

```bash
git add . && git commit -m "Deploy Saveflow API" && git push
```

5. When the build log flips to **Running**, the API is live at `https://<your-username>-<space-name>.hf.space`.

Spaces expose port `7860` only — the image already defaults to it, so no configuration is required.

### Notes that apply to every host

- CORS is open to `*`, so GitHub Pages can call the API from a different origin.
- On free tiers the container sleeps when idle. The first request afterwards spends 30–60 s waking it; the frontend shows a "server may be waking up" hint once a request passes 8 seconds.
- Bandwidth stays cheap: the API only returns CDN links, and the media itself downloads straight from the platform to the visitor's browser. It never passes through your server.
- Platforms rate-limit datacenter IP ranges, and some block them outright. Instagram and Facebook are the strictest. This affects every cloud host equally — it is a limitation of the approach, not of the host you pick.
- `yt-dlp` breaks whenever a platform changes its internals. Bump the pinned version in `requirements.txt` and redeploy when extraction starts failing.

---

## 3. Configure and deploy the frontend to GitHub Pages

### 3a. Point the frontend at your API

Open [frontend/script.js](frontend/script.js) and edit the constant at the top — no trailing slash:

```js
const API_BASE_URL = "https://saveflow-api.onrender.com";
```

Test locally before deploying:

```bash
cd frontend && python -m http.server 5500
```

Then open <http://127.0.0.1:5500>. Serve it over HTTP rather than opening `index.html` from disk — the clipboard API needs a secure context, and `file://` is not one.

### 3b. Publish

```bash
git init && git add . && git commit -m "Saveflow"
```

```bash
git remote add origin https://github.com/<your-username>/saveflow.git && git branch -M main && git push -u origin main
```

Then in the repo: **Settings → Pages → Source: Deploy from a branch → Branch `main` → Folder `/ (root)`** and Save.

Because the site files live in `frontend/`, either:

- **Option A** — move `index.html`, `style.css` and `script.js` to the repository root, or
- **Option B** — keep the folder and select `/docs` in the Pages settings after renaming `frontend/` to `docs/`.

Your site goes live at `https://<your-username>.github.io/saveflow/` within a minute or two.

---

## API reference

### `GET /`

```json
{ "status": "ok", "message": "Saveflow API is running.", "docs": "/docs", "endpoint": "POST /api/extract" }
```

### `POST /api/extract`

Request:

```json
{ "url": "https://www.instagram.com/p/XXXXXXXXXXX/" }
```

Response `200`:

```json
{
  "platform": "Instagram",
  "source_url": "https://www.instagram.com/p/XXXXXXXXXXX/",
  "title": "Caption of the post",
  "thumbnail": "https://…jpg",
  "duration": 34,
  "items": [
    {
      "title": "Caption of the post",
      "thumbnail": "https://…jpg",
      "duration": 34,
      "uploader": "someone",
      "formats": [
        { "url": "https://…mp4", "label": "1080p", "ext": "mp4", "filesize": "8.4 MB", "kind": "video" },
        { "url": "https://…m4a", "label": "Audio only", "ext": "m4a", "filesize": "1.1 MB", "kind": "audio" }
      ]
    }
  ]
}
```

Carousels return one entry per slide in `items`. The top-level `title`/`thumbnail`/`duration` mirror the first item.

Errors come back as FastAPI's standard shape, and the frontend displays `detail` verbatim:

```json
{ "detail": "This post is private or requires a login, so it cannot be fetched." }
```

| Status | Cause |
| --- | --- |
| `400` | Malformed URL, private post, unsupported site, deleted post, no downloadable file |
| `500` | Unexpected server-side failure |

---

## Known limitations

- Private, age-gated and follower-only posts cannot be fetched — the API authenticates to nothing.
- HLS-only streams are filtered out because a browser cannot save a `.m3u8` manifest as a file.
- Some CDNs ignore the `download` attribute and play media inline. Right-click → *Save video as…* in that case.
- Platforms rate-limit datacenter IPs. A shared Hugging Face Space can be throttled during busy periods.

## Legal

Saveflow is a technical tool. Downloading content you do not own may breach a platform's terms of service or a creator's copyright. Only save media you have the right to save.

## Credits

Visual direction adapted from the *Chain App Dev* template by [TemplateMo](https://templatemo.com/tm-570-chain-app-dev) — used as a design reference only. All HTML, CSS and JavaScript in this project is original.
