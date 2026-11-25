# Saga-Push-Relais (2.6, Bugs-DB 2262) - eigenes Image ghcr.io/<owner>/saga-relay, gebaut aus dem Wurzelverzeichnis:
#   docker build -f relay/Dockerfile -t saga-relay .
# Aus saga/ kommt nur fedsig.py mit (Signaturen RFC 9421). Laeuft ohne Root, mit read_only-Dateisystem: geschrieben wird
# nur nach /data (SQLite relay.db mit WAL); der APNs-Schluessel kommt als Verzeichnis nach /run/secrets (Mountpunkt
# existiert, damit read_only auch mit podman/crun geht). Nach aussen nur ueber den Reverse-Proxy (HTTPS 443), im Container Port 8080.
FROM python:3.13-slim

LABEL org.opencontainers.image.title="Saga-Push-Relais" \
      org.opencontainers.image.description="Leitet Ende-zu-Ende-verschluesselte Mitteilungen von Saga-Servern an Apple (APNs) weiter" \
      org.opencontainers.image.source="https://github.com/yggdrasil-sh/saga" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RELAY_CONFIG=/data/relay.json \
    RELAY_DB=/data/relay.db \
    RELAY_LISTEN=0.0.0.0 \
    RELAY_PORT=8080

WORKDIR /app
COPY relay/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt \
 && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin relay \
 && mkdir -p /data /app/saga /run/secrets \
 && touch /app/saga/__init__.py \
 && chown 10001:10001 /data

COPY saga/fedsig.py ./saga/fedsig.py
COPY relay/sagarelay ./sagarelay
COPY relay/relayctl.py ./relayctl.py

USER 10001
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('RELAY_PORT', '8080') + '/v1/info', timeout=4).read()"]
ENTRYPOINT ["python", "-m", "sagarelay"]
