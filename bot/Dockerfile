# ── Stage: base image ──────────────────────────────────────────────────────────
# Official Playwright Python image ships with Chromium + all system deps.
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

# ── System deps for VNC / virtual display (Option B: noVNC) ───────────────────
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    supervisor \
    procps \
    tzdata \
    fluxbox \
    && ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Jakarta

# ── App setup ─────────────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium


COPY . bot/

# ── Runtime dirs ──────────────────────────────────────────────────────────────
RUN mkdir -p /data/shopee-profile /data/logs

# ── Supervisor config path ────────────────────────────────────────────────────
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose noVNC web port
EXPOSE 6080

# Health check (also defined in docker-compose, but this is a fallback for standalone usage)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080')"

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
