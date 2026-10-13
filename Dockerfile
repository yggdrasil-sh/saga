# Saga - Dashboard, Seiten, Aufgaben, Kalender, Lesezeichen (PostgreSQL)
FROM python:3.13-slim

LABEL org.opencontainers.image.title="Saga" \
      org.opencontainers.image.description="Selbst gehostetes Dashboard mit Seiten, Datenbanken, Aufgaben, Kalender und Lesezeichen" \
      org.opencontainers.image.source="https://github.com/yggdrasil-sh/saga" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STATE_DIR=/data \
    PORT=8080 \
    ADMIN_PORT=8081

WORKDIR /app

# pg_dump/pg_restore fuer Backups (automatisch vor jedem Datenbank-Update, "python run.py backup")
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt \
 && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin saga \
 && mkdir -p /data /certs \
 && chown 10001:10001 /data

COPY VERSION app.py admin.py auth.py db.py hkcal.py run.py version.py ./
COPY saga ./saga
COPY templates ./templates
COPY static ./static
COPY docker/entrypoint.sh /entrypoint.sh

# Erst nach pip install setzen (Datei entsteht beim Start im Entrypoint).
ENV CA_FILE=/tmp/ca-bundle.pem \
    SSL_CERT_FILE=/tmp/ca-bundle.pem

USER 10001
EXPOSE 8080 8081
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=3)" || exit 1

ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
