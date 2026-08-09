# Saveflow backend — built for Hugging Face Spaces (Docker Space).
FROM python:3.10-slim

# ffmpeg: required by yt-dlp for muxing/probing.
# curl:   used by the container healthcheck below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Copy requirements first so Docker caches the dependency layer across rebuilds.
COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY . /code

# Every host names its port differently: Hugging Face expects 7860, Render
# injects PORT=10000, Cloud Run injects PORT=8080. Default to 7860 and let the
# platform override it, so one image runs everywhere unchanged.
ENV PORT=7860
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -fsS "http://localhost:${PORT}/" || exit 1

# Shell form on purpose — exec form would not expand ${PORT}.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}
