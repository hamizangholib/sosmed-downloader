# 🌊 Saveflow — Social Media Media Downloader

<p align="center">
  <img src="docs/assets/logo.svg" alt="Saveflow Logo" width="100" height="100" />
</p>

<p align="center">
  <strong>Extract & Download videos, photos, and audio from TikTok, Instagram, Facebook, X (Twitter), and Threads.</strong>
</p>

<p align="center">
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-0.115+-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI"></a>
  <a href="https://github.com/yt-dlp/yt-dlp"><img src="https://img.shields.io/badge/yt--dlp-2025.1+-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="yt-dlp"></a>
  <a href="https://docker.com"><img src="https://img.shields.io/badge/Docker-Supported-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker"></a>
  <a href="https://pages.github.com/"><img src="https://img.shields.io/badge/GitHub_Pages-Frontend-222222?style=for-the-badge&logo=github&logoColor=white" alt="GitHub Pages"></a>
  <a href="https://vercel.com"><img src="https://img.shields.io/badge/Vercel-Ready-000000?style=for-the-badge&logo=vercel&logoColor=white" alt="Vercel"></a>
</p>

---

## 🌐 Language / Bahasa

- 🇮🇩 [**Bahasa Indonesia**](#-bahasa-indonesia)
- 🇬🇧 [**English**](#-english)

---

<a name="-bahasa-indonesia"></a>
# 🇮🇩 Bahasa Indonesia

Saveflow adalah aplikasi pengunduh media media sosial serbaguna yang cepat, tanpa watermark, dan tanpa perlu login. Aplikasi ini mengonversi tautan dari **TikTok, Instagram, Facebook, X (Twitter), dan Threads** menjadi tautan unduhan langsung untuk video, foto, maupun audio.

- **Backend**: Python 3.10 + FastAPI + `yt-dlp`, dikontainerisasi dengan Docker dan siap dideploy ke Vercel, Render, Cloud Run, atau server Anda sendiri.
- **Frontend**: Responsive UI berbasis HTML/CSS/Vanilla JS modern yang dirancang untuk dihosting secara gratis melalui **GitHub Pages**.

---

## 📋 Daftar Isi (Indonesian)

- [Platform yang Didukung](#platform-yang-didukung)
- [Fitur Utama](#fitur-utama)
- [Struktur Proyek](#struktur-proyek)
- [Cara Menjalankan di Lokal](#cara-menjalankan-di-lokal)
  - [1. Menggunakan Python venv](#1-menggunakan-python-venv)
  - [2. Menggunakan Docker](#2-menggunakan-docker)
- [Panduan Deploy Backend](#panduan-deploy-backend)
  - [Vercel (Rekomendasi - Gratis & Tanpa Kartu Kredit)](#vercel-rekomendasi---gratis--tanpa-kartu-kredit)
  - [Render (Container Docker)](#render-container-docker)
  - [Google Cloud Run](#google-cloud-run)
  - [Hugging Face Spaces](#hugging-face-spaces)
- [Panduan Deploy Frontend ke GitHub Pages](#panduan-deploy-frontend-ke-github-pages)
- [Dokumentasi API](#dokumentasi-api)
- [Batasan Sistem](#batasan-sistem)
- [Ketentuan Hukum](#ketentuan-hukum)

---

### Platform yang Didukung

| Platform | Video | Foto | Audio / Sound | Carousel (Multi-item) |
| :--- | :---: | :---: | :---: | :---: |
| **TikTok** | ✅ (Tanpa Watermark) | ✅ | ✅ | ✅ |
| **Instagram** | ✅ (Reels & Feed) | ✅ | ✅ | ✅ |
| **Facebook** | ✅ | ✅ | ✅ | ✅ |
| **X (Twitter)** | ✅ | ✅ | ❌ | ✅ |
| **Threads** | ✅ | ✅ | ❌ | ✅ |

---

### Fitur Utama

- 🚀 **Ekstraksi Cepat & Akut**: Mengambil metadata, resolusi (1080p, 720p, dll.), durasi, dan format audio secara akurat.
- 🖼️ **Dukungan Carousel (Multi-Media)**: Ekstraksi otomatis untuk postingan Instagram/TikTok yang memiliki banyak slide foto atau video.
- 📥 **Direct Download Streaming**: Fitur proxy `GET /api/download` memaksa peramban mengunduh media dengan nama berkas resmi dan header `Content-Disposition`, melewati batasan CORS & blokir CDN.
- 🎨 **Tampilan UI Premium**: UI modern, responsif, dilengkapi animasi halus, papan klip otomatis, serta indikator loading status server.
- 🔒 **Tanpa Autentikasi & Privasi Terjaga**: Bebas pendaftaran, tidak menyimpan data pengguna atau riwayat unduhan.

---

### Struktur Proyek

```text
.
├── backend/
│   ├── main.py            # FastAPI app: GET /, POST /api/extract, GET /api/download
│   ├── requirements.txt   # FastAPI, uvicorn, yt-dlp, pydantic
│   └── Dockerfile         # Docker build (Python 3.10 slim + ffmpeg)
├── docs/                  # Frontend statis yang dipublikasikan oleh GitHub Pages
│   ├── index.html         # Struktur UI aplikasi
│   ├── style.css          # Styling kustom (Design System & Responstivitas)
│   ├── script.js          # Logika frontend & integrasi API
│   └── assets/            # Logo & aset visual
└── README.md              # Dokumentasi proyek
```

---

### Cara Menjalankan di Lokal

#### 1. Menggunakan Python venv

**Prasyarat**: Python 3.10+ dipasang di sistem.

1. Masuk ke direktori `backend`:
   ```bash
   cd backend
   ```

2. Buat dan aktifkan virtual environment:
   - **Windows**:
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\activate
     ```
   - **macOS / Linux**:
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```

3. Pasang dependensi:
   ```bash
   pip install -r requirements.txt
   ```

4. Jalankan server Uvicorn:
   ```bash
   uvicorn main:app --reload --port 7860
   ```
   - Healthcheck: `http://127.0.0.1:7860/`
   - Dokumentasi Swagger UI: `http://127.0.0.1:7860/docs`

#### 2. Menggunakan Docker

```bash
# Build image
docker build -t saveflow-api ./backend

# Jalankan kontainer
docker run --rm -p 7860:7860 saveflow-api
```

---

### Panduan Deploy Backend

Backend berbasis FastAPI dapat dideploy ke berbagai layanan cloud secara gratis:

#### Mengaktifkan video sensitif X (Twitter)

X mewajibkan sesi login untuk sebagian media yang ditandai sensitif. Tambahkan
dua environment variable berikut pada backend:

```text
X_AUTH_TOKEN=<nilai cookie auth_token dari x.com>
X_CT0=<nilai cookie ct0 dari x.com>
```

Di Vercel, buka **Project Settings → Environment Variables**, simpan keduanya,
lalu redeploy backend. Ambil nilainya dari DevTools browser (**Application →
Cookies → https://x.com**) saat akun X sedang login. Jangan commit nilainya ke
Git atau mengirimkannya melalui chat.

Setelah redeploy, buka URL backend dan pastikan `x_auth.configured` bernilai
`true`. Nilai cookie tidak pernah ditampilkan oleh endpoint tersebut.

Saveflow hanya memakai sesi tersebut setelah permintaan anonim X secara
eksplisit meminta autentikasi atau menyembunyikan media. Hasil retry yang
berhasil tidak bergantung pada metadata `age_limit`, karena field tersebut
tidak selalu konsisten pada postingan X. Error eksplisit untuk tweet privat
tetap tidak dicoba ulang memakai sesi akun.

#### Vercel (Rekomendasi - Gratis & Tanpa Kartu Kredit)

1. Push repositori ini ke GitHub.
2. Buka [Vercel Dashboard](https://vercel.com/new) dan impor repositori Anda.
3. Pada opsi **Root Directory**, klik **Edit** lalu pilih direktori **`backend`**.
4. Klik **Deploy**. API Anda akan aktif di `https://<nama-proyek>.vercel.app`.

#### Render (Container Docker)

1. Buat Web Service baru di [Render Dashboard](https://dashboard.render.com/).
2. Hubungkan repositori GitHub Anda.
3. Atur **Language** ke `Docker` dan **Root Directory** ke `backend`.
4. Pilih paket **Free** dan klik **Create Web Service**.

#### Google Cloud Run

```bash
gcloud run deploy saveflow-api \
  --source ./backend \
  --region asia-southeast2 \
  --allow-unauthenticated \
  --memory 1Gi
```

---

### Panduan Deploy Frontend ke GitHub Pages

1. **Sesuaikan URL API Backend**:
   Buka file [`docs/script.js`](docs/script.js) dan ubah konstanta `API_BASE_URL` di bagian paling atas:
   ```javascript
   const API_BASE_URL = "https://nama-api-anda.vercel.app";
   ```

2. **Uji Frontend di Lokal**:
   ```bash
   cd docs
   python -m http.server 5500
   ```
   Buka `http://127.0.0.1:5500` di peramban.

3. **Aktifkan GitHub Pages**:
   - Commit dan Push repositori ke GitHub.
   - Buka repositori di GitHub → **Settings** → **Pages**.
   - Pada **Source**, pilih **Deploy from a branch**.
   - Pilih Branch `main` dan folder **`/docs`**, lalu klik **Save**.
   - Website Anda akan aktif di `https://<username>.github.io/<repo-name>/`.

---

### Dokumentasi API

#### 1. `GET /`
Mengembalikan status kesehatan API.
```json
{
  "status": "ok",
  "message": "Saveflow API is running.",
  "docs": "/docs",
  "endpoint": "POST /api/extract"
}
```

#### 2. `POST /api/extract`
Mengekstrak metadata dan format unduhan dari URL sosial media.

- **Request Body**:
  ```json
  {
    "url": "https://www.instagram.com/p/XXXXXXXXXXX/"
  }
  ```

- **Response Body (`200 OK`)**:
  ```json
  {
    "platform": "Instagram",
    "source_url": "https://www.instagram.com/p/XXXXXXXXXXX/",
    "title": "Judul atau caption postingan",
    "thumbnail": "https://...",
    "duration": 30,
    "items": [
      {
        "title": "Judul postingan",
        "thumbnail": "https://...",
        "duration": 30,
        "uploader": "username",
        "formats": [
          {
            "url": "https://...",
            "label": "1080p",
            "ext": "mp4",
            "filesize": "12.5 MB",
            "kind": "video"
          },
          {
            "url": "https://...",
            "label": "Audio only",
            "ext": "m4a",
            "filesize": "1.2 MB",
            "kind": "audio"
          }
        ]
      }
    ]
  }
  ```

#### 3. `GET /api/download`
Mengalirkan (stream) media dari server untuk memaksa pengunduhan langsung di peramban pengguna.
```http
GET /api/download?url=<URL_ASLI_POSTINGAN>&index=0&format_id=<FORMAT_ID>
```

---

### Batasan Sistem

- **Konten Privat**: Konten dari akun pribadi/terkunci tidak dapat diunduh karena API berjalan tanpa kredensial pengguna.
- **Stream HLS (.m3u8)**: Stream HLS tanpa file langsung disaring untuk memastikan peramban dapat menyimpan file utuh secara otomatis.
- **Pembatasan IP Cloud (Rate Limit)**: Beberapa platform (seperti Instagram & Facebook) dapat memblokir rentang IP pusat data cloud. Jika ini terjadi, gunakan instance server mandiri atau proxy rotasi.

---

### Ketentuan Hukum

**Saveflow** dibuat hanya untuk tujuan edukasi dan penggunaan pribadi yang sah. Mengunduh materi berhak cipta tanpa izin dapat melanggar Syarat dan Ketentuan platform atau undang-undang hak cipta di wilayah Anda. Pastikan Anda memiliki hak yang sah atas media yang Anda unduh.

---

<a name="-english"></a>
# 🇬🇧 English

Saveflow is a lightweight, fast, and modern social media media downloader. Paste a link from **TikTok, Instagram, Facebook, X (Twitter), or Threads** and instantly receive direct download links for videos, photos, or audio without any watermark or account sign-up.

- **Backend**: Python 3.10 + FastAPI + `yt-dlp`, containerized via Docker and ready for deployment to Vercel, Render, Cloud Run, or custom servers.
- **Frontend**: Clean Vanilla HTML5/CSS3/JS UI designed to be hosted for free on **GitHub Pages**.

---

## 📋 Table of Contents (English)

- [Supported Platforms](#supported-platforms-1)
- [Key Features](#key-features-1)
- [Project Architecture](#project-architecture-1)
- [Local Setup Guide](#local-setup-guide)
  - [1. Using Python venv](#1-using-python-venv-1)
  - [2. Using Docker](#2-using-docker-1)
- [Backend Deployment Guide](#backend-deployment-guide)
  - [Vercel (Recommended - Free & No Credit Card)](#vercel-recommended---free--no-credit-card)
  - [Render (Docker Container)](#render-docker-container)
  - [Google Cloud Run](#google-cloud-run-1)
  - [Hugging Face Spaces](#hugging-face-spaces-1)
- [Frontend Deployment to GitHub Pages](#frontend-deployment-to-github-pages)
- [API Reference](#api-reference)
- [Known Limitations](#known-limitations)
- [Legal & License](#legal--license)

---

### Supported Platforms

| Platform | Video | Photos | Audio / Sound | Carousel (Multi-item) |
| :--- | :---: | :---: | :---: | :---: |
| **TikTok** | ✅ (No Watermark) | ✅ | ✅ | ✅ |
| **Instagram** | ✅ (Reels & Feed) | ✅ | ✅ | ✅ |
| **Facebook** | ✅ | ✅ | ✅ | ✅ |
| **X (Twitter)** | ✅ | ✅ | ❌ | ✅ |
| **Threads** | ✅ | ✅ | ❌ | ✅ |

---

### Key Features

- ⚡ **Fast & Reliable Extraction**: Resolves video qualities (1080p, 720p), thumbnail preview, audio streams, and original post metadata.
- 📸 **Carousel Support**: Automatically parses multi-slide photo and video posts from Instagram and TikTok.
- 📥 **Streamed Downloads**: `GET /api/download` proxy endpoint forces browser downloads with valid filenames and `Content-Disposition` headers, bypassing CORS and expired CDN URL issues.
- 🎨 **Modern Design**: Built with clean CSS tokens, responsive layouts, micro-animations, clipboard auto-paste, and server wake-up notifications.
- 🛡️ **Privacy-First**: No tracking, no user login required, and no persistent media storage on the server.

---

### Project Architecture

```text
.
├── backend/
│   ├── main.py            # FastAPI app: GET /, POST /api/extract, GET /api/download
│   ├── requirements.txt   # Dependencies: FastAPI, uvicorn, yt-dlp, pydantic
│   └── Dockerfile         # Docker image configuration (Python 3.10 slim + ffmpeg)
├── docs/                  # Static web app published by GitHub Pages
│   ├── index.html         # Main Web Interface
│   ├── style.css          # Design system & responsive styles
│   ├── script.js          # API client & UI event handling
│   └── assets/            # Logos & SVG graphics
└── README.md              # Project documentation
```

---

### Local Setup Guide

#### 1. Using Python venv

**Prerequisite**: Python 3.10+ installed on your machine.

1. Navigate to the `backend` directory:
   ```bash
   cd backend
   ```

2. Create and activate a virtual environment:
   - **Windows**:
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\activate
     ```
   - **macOS / Linux**:
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```

3. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```

4. Launch the Uvicorn ASGI server:
   ```bash
   uvicorn main:app --reload --port 7860
   ```
   - Healthcheck: `http://127.0.0.1:7860/`
   - Interactive OpenAPI Docs: `http://127.0.0.1:7860/docs`

#### 2. Using Docker

```bash
# Build Docker image
docker build -t saveflow-api ./backend

# Run container
docker run --rm -p 7860:7860 saveflow-api
```

---

### Backend Deployment Guide

The Python FastAPI backend can be easily deployed to zero-cost cloud hosts:

#### Enabling sensitive X (Twitter) videos

X requires a logged-in session for some media marked as sensitive. Add these
two environment variables to the backend:

```text
X_AUTH_TOKEN=<the auth_token cookie value from x.com>
X_CT0=<the ct0 cookie value from x.com>
```

On Vercel, open **Project Settings → Environment Variables**, save both values,
and redeploy the backend. Copy the values from browser DevTools (**Application
→ Cookies → https://x.com**) while the X account is logged in. Never commit the
values to Git or send them through chat.

After redeploying, open the backend URL and verify that `x_auth.configured` is
`true`. The endpoint never exposes the cookie values.

Saveflow only uses this session after an anonymous X request is explicitly
asks to authenticate or hides the media. A successful retry does not depend on
the `age_limit` metadata because X does not report that field consistently.
Explicit protected-tweet errors are still not retried with the account session.

#### Vercel (Recommended - Free & No Credit Card)

1. Push your repository to GitHub.
2. Go to [Vercel Dashboard](https://vercel.com/new) and import your repository.
3. Expand **Root Directory**, click **Edit**, and select the **`backend`** folder.
4. Click **Deploy**. Your API endpoint will be live at `https://<project-name>.vercel.app`.

#### Render (Docker Container)

1. Create a new Web Service at [Render Dashboard](https://dashboard.render.com/).
2. Connect your GitHub repository.
3. Set **Language** to `Docker` and **Root Directory** to `backend`.
4. Select the **Free** instance tier and click **Create Web Service**.

#### Google Cloud Run

```bash
gcloud run deploy saveflow-api \
  --source ./backend \
  --region asia-southeast2 \
  --allow-unauthenticated \
  --memory 1Gi
```

---

### Frontend Deployment to GitHub Pages

1. **Configure API Base URL**:
   Open [`docs/script.js`](docs/script.js) and update the `API_BASE_URL` variable at the top:
   ```javascript
   const API_BASE_URL = "https://your-api-name.vercel.app";
   ```

2. **Test Locally**:
   ```bash
   cd docs
   python -m http.server 5500
   ```
   Open `http://127.0.0.1:5500` in your browser.

3. **Publish on GitHub Pages**:
   - Push your latest changes to GitHub.
   - Go to your repository on GitHub → **Settings** → **Pages**.
   - Under **Source**, select **Deploy from a branch**.
   - Choose the `main` branch and the **`/docs`** folder, then click **Save**.
   - Your frontend will be available at `https://<username>.github.io/<repo-name>/`.

---

### API Reference

#### 1. `GET /`
Healthcheck endpoint.
```json
{
  "status": "ok",
  "message": "Saveflow API is running.",
  "docs": "/docs",
  "endpoint": "POST /api/extract"
}
```

#### 2. `POST /api/extract`
Extracts post metadata and downloadable stream formats.

- **Request Payload**:
  ```json
  {
    "url": "https://www.instagram.com/p/XXXXXXXXXXX/"
  }
  ```

- **Response (`200 OK`)**:
  ```json
  {
    "platform": "Instagram",
    "source_url": "https://www.instagram.com/p/XXXXXXXXXXX/",
    "title": "Post caption or title",
    "thumbnail": "https://...",
    "duration": 30,
    "items": [
      {
        "title": "Post title",
        "thumbnail": "https://...",
        "duration": 30,
        "uploader": "username",
        "formats": [
          {
            "url": "https://...",
            "label": "1080p",
            "ext": "mp4",
            "filesize": "12.5 MB",
            "kind": "video"
          },
          {
            "url": "https://...",
            "label": "Audio only",
            "ext": "m4a",
            "filesize": "1.2 MB",
            "kind": "audio"
          }
        ]
      }
    ]
  }
  ```

#### 3. `GET /api/download`
Proxies and streams media files directly to the user to trigger download dialogs.
```http
GET /api/download?url=<ORIGINAL_POST_URL>&index=0&format_id=<FORMAT_ID>
```

---

### Known Limitations

- **Private & Age-Gated Content**: Private or login-protected posts cannot be extracted as the API operates unauthenticated.
- **HLS Streams (.m3u8)**: HLS-only manifests are excluded to prevent corrupted single-file download attempts in browsers.
- **Cloud IP Rate Limits**: Data center IP blocks may occasionally affect requests to strict platforms like Instagram. Redeploying or running on dedicated IPs fixes this issue.

---

### Legal & License

**Saveflow** is created for educational and lawful personal use only. Downloading copyrighted media without authorization may violate platform terms of service or copyright laws in your jurisdiction. Only download content you own or have explicit rights to save.

Distributed under the MIT License. See `LICENSE` for details.
