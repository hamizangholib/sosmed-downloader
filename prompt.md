Build a complete, production-ready full-stack Social Media Downloader web application. 



\### Project Specifications \& Tech Stack:

1\. \*\*Backend (`/backend`)\*\*:

&#x20;  - Framework: Python 3.10 + FastAPI + Uvicorn.

&#x20;  - Core Engine: `yt-dlp` for media extraction.

&#x20;  - Target Platform for Hosting: Hugging Face Spaces (Docker Space).

&#x20;  - Features \& Requirements:

&#x20;    - Enable CORS middleware allowing all origins (`\*`) so it can accept requests from GitHub Pages.

&#x20;    - Endpoint `GET /`: Healthcheck returning status and welcome message.

&#x20;    - Endpoint `POST /api/extract`:

&#x20;      - Request body JSON: `{"url": "string"}`

&#x20;      - Extract direct video/photo download links, media title, thumbnail URL, duration, and platform name from TikTok, Instagram, Facebook, X (Twitter), and Threads using `yt-dlp`.

&#x20;      - Handle errors gracefully (invalid URL, private post, unsupported site) and return meaningful HTTP 400/500 JSON error responses.

&#x20;    - Port configuration: Hugging Face Spaces default port `7860`.



2\. \*\*Dockerfile (`/backend/Dockerfile`)\*\*:

&#x20;  - Base image: `python:3.10-slim`

&#x20;  - Install required system packages: `ffmpeg` and `curl`.

&#x20;  - Set working directory to `/code`.

&#x20;  - Install dependencies from `requirements.txt`.

&#x20;  - Expose port `7860`.

&#x20;  - CMD to run `uvicorn main:app --host 0.0.0.0 --port 7860`.



3\. \*\*Frontend (`/frontend` or root directory for GitHub Pages)\*\*:

&#x20;  - Tech Stack: Pure HTML5, CSS3, Vanilla JavaScript (no heavy build framework needed, ready for GitHub Pages hosting).

&#x20;  - Design \& UI/UX:

&#x20;    - Modern, clean, responsive UI with dark mode styling.

&#x20;    - Centered layout with a prominent URL input box, paste button, and "Download" trigger button.

&#x20;    - Supported platforms badge list (TikTok, Instagram, Facebook, X/Twitter, Threads).

&#x20;    - Loading state with animated spinner/skeleton during extraction.

&#x20;    - Results section displaying thumbnail preview, title, platform name, and a "Download File" button (direct link/blob trigger).

&#x20;    - Error alert popup/banner for failed extractions.

&#x20;  - Configuration:

&#x20;    - At the top of `script.js`, include a configurable global constant `const API\_BASE\_URL = "YOUR\_HUGGINGFACE\_SPACE\_URL";` with clear comments on how to replace it.



4\. \*\*Documentation (`README.md`)\*\*:

&#x20;  - Clear step-by-step instructions on:

&#x20;    1. How to run the backend locally.

&#x20;    2. How to deploy the backend to Hugging Face Spaces using Docker.

&#x20;    3. How to configure `script.js` with the live Hugging Face API URL and deploy the frontend to GitHub Pages.



Please generate all necessary project files with full, clean, and commented code.

