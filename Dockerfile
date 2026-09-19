# Reels AI Hunter — reproducible local/container runtime.
# Keep this image tag aligned with the pinned playwright package in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

LABEL org.opencontainers.image.title="Reels AI Hunter"
LABEL org.opencontainers.image.description="Instagram Reels collector/evaluator with optional Xpra HTML5 desktop"

ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC \
    DISPLAY=:99 \
    SCREEN_WIDTH=1280 \
    SCREEN_HEIGHT=1024 \
    SCREEN_DEPTH=24 \
    WEB_PORT=8080 \
    WEB_BIND_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Playwright's official image already contains Chromium and its browser system
# dependencies. Install only the desktop/debugging tools the project actually uses.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        apt-transport-https \
        ca-certificates \
        dbus-x11 \
        ffmpeg \
        fluxbox \
        fontconfig \
        fonts-liberation \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        fonts-noto-core \
        procps \
        software-properties-common \
        supervisor \
        wget \
        x11-utils \
        xterm \
    && wget -O /usr/share/keyrings/xpra.asc https://xpra.org/xpra.asc \
    && wget -O /etc/apt/sources.list.d/xpra.sources \
        https://raw.githubusercontent.com/Xpra-org/xpra/master/packaging/repos/noble/xpra.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends xpra xpra-html5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first for better Docker caching.
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt \
    && python -m pip check

COPY app/ /app/
COPY entrypoint.sh /app/entrypoint.sh
COPY supervisord.conf /etc/supervisor/conf.d/reels-hunter.conf
RUN chmod 0755 /app/entrypoint.sh \
    && mkdir -p \
        /var/log/reels-hunter \
        /var/log/supervisor \
        /var/run/supervisor \
        /run/xpra \
        /tmp/reels_downloads \
        /tmp/reels_screenshots

EXPOSE 8080

# Socket-level check works even though the Xpra HTTP/WebSocket endpoint is
# password-protected.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,socket; s=socket.create_connection(('127.0.0.1',int(os.getenv('WEB_PORT','8080'))),3); s.close()" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
