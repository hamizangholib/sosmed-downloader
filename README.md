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
├── docs/                  # the website — GitHub Pages publishes this folder
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

The backend runs Python, so it cannot live on a static host. `backend/` deploys two ways with no code changes: serverless (Vercel detects `main.py` directly) or as a container (`backend/Dockerfile`).

**Why no `ffmpeg` is needed:** the API sets `skip_download: True` and never merges streams. It reads metadata and returns the platform's own CDN URLs; the media itself never passes through the server. `ffmpeg` only matters when yt-dlp has to mux or transcode, which this app never does. It stays in the `Dockerfile` so the image also works for anyone who later adds server-side downloading.

| Host | Free? | Card needed | Sleeps when idle | Notes |
| --- | --- | --- | --- | --- |
| **Vercel** | Hobby, forever | **No** | Scales to zero | Deploys `backend/` directly. Hobby is personal, non-commercial use only. |
| **Render** | 750 h/month | Often yes now | Yes, after ~15 min | Uses the `Dockerfile`. Asks new accounts to verify a card. |
| **Google Cloud Run** | 2M requests/month | Yes | Scales to zero | Fastest cold starts, billing must be enabled. |
| **Koyeb** | One free instance | Sometimes | After 1 h | Free tier availability changed post-acquisition. |
| **Hugging Face** | Docker needs PRO | — | Yes | Only worth it if you already pay for PRO. |

Free-tier terms change often — check the host's current pricing page before committing.

### 2a. Vercel (recommended — no credit card)

1. Push this repository to GitHub (see step 3b if you have not yet).
2. Go to <https://vercel.com/new> and import the repository.
3. Expand **Root Directory**, click **Edit**, and select **`backend`**. This is the only setting you need to change.
4. Click **Deploy**.
5. Your API goes live at `https://<project-name>.vercel.app` — open it and you should see the healthcheck JSON.

No `vercel.json` and no adapter file are involved. Vercel's Python runtime looks for an entrypoint named `app.py`, `index.py`, `server.py`, `main.py`, `wsgi.py` or `asgi.py` that defines a top-level `app`, and `backend/main.py` matches once `backend` is the root. It installs from `backend/requirements.txt` and routes every request into FastAPI, so `/` and `/api/extract` both work.

Detection is static — Vercel reads the file rather than importing it, so the entrypoint must contain a literal `app = FastAPI(...)`. A module that re-exports the app with `from … import app` will not be recognised.

Vercel's Hobby plan is for personal, non-commercial projects. Read their terms if this ever becomes something you charge for.

### 2b. Render (container)

1. Push the repository to GitHub.
2. <https://dashboard.render.com/> → **New** → **Web Service**, connect the repository.
3. Configure — **Language** `Docker`, **Root Directory** `backend`, **Instance Type** `Free`.
4. Deploy. The first build takes a few minutes because `ffmpeg` goes into the image.

Render injects `PORT` automatically; do not set it yourself. New accounts are increasingly asked to verify a credit card before the free instance type unlocks.

### 2c. Google Cloud Run

With the [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and a project selected:

```bash
gcloud run deploy saveflow-api --source ./backend --region asia-southeast2 --allow-unauthenticated --memory 1Gi
```

Cloud Run builds the `Dockerfile`, injects `PORT=8080` and prints the live URL when it finishes. Raise `--memory` if extraction of large videos gets killed.

### 2d. Hugging Face Spaces (needs a PRO plan)

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

Open [docs/script.js](docs/script.js) and edit the constant at the top:

```js
const API_BASE_URL = "https://saveflow-api.vercel.app";
```

A trailing slash is tolerated — the code strips it before building the request URL.

Test locally before deploying:

```bash
cd docs && python -m http.server 5500
```

Then open <http://127.0.0.1:5500>. Serve it over HTTP rather than opening `index.html` from disk — the clipboard API needs a secure context, and `file://` is not one.

### 3b. Publish

GitHub Pages can only publish from the repository root or from a folder named exactly `docs/` — no other folder name works without a build workflow. That is why the site lives in `docs/` rather than `frontend/`.

Push the repository, then in the repo on GitHub: **Settings → Pages → Source: Deploy from a branch → Branch `main` → Folder `/docs`** and Save.

Your site goes live at `https://<your-username>.github.io/<repo-name>/` within a minute or two.

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
