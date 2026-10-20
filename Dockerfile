# Saga - Dashboard, Seiten, Aufgaben, Kalender, Lesezeichen (PostgreSQL)

# saga-media (2.4, media/): Go nur mit Standardbibliothek, statisch gelinkt. Auf der Build-Plattform fuer die
# Zielarchitektur uebersetzt (amd64/arm64 ohne Emulation).
FROM --platform=$BUILDPLATFORM golang:1.24-alpine AS media
ARG TARGETOS TARGETARCH
ENV CGO_ENABLED=0 GOFLAGS=-trimpath
WORKDIR /src
COPY media/ ./
COPY VERSION /VERSION
RUN go vet ./... && go test ./... \
 && GOOS=$TARGETOS GOARCH=$TARGETARCH go build -ldflags="-s -w -X main.version=$(cat /VERSION)" -o /out/saga-media .

# gjallarhorn (2.5, gjallarhorn/): eingebauter Knoten fuer grosse Gruppenanrufe, startet saga-media als zweiten Kindprozess
# (nur mit Feature-Flag calls.horn). Statisch (CGO aus), wie saga-media ohne Emulation fuer amd64/arm64 uebersetzt.
FROM --platform=$BUILDPLATFORM golang:1.24-alpine AS horn
ARG TARGETOS TARGETARCH
ENV CGO_ENABLED=0 GOFLAGS=-trimpath
WORKDIR /src
COPY gjallarhorn/go.mod gjallarhorn/go.sum ./
RUN go mod download
COPY gjallarhorn/ ./
COPY VERSION /VERSION
RUN go vet ./... && go test ./internal/... \
 && GOOS=$TARGETOS GOARCH=$TARGETARCH go build -ldflags="-s -w -X main.version=$(cat /VERSION)" -o /out/gjallarhorn .

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

# pg_dump/pg_restore/psql fuer Backups (automatisch vor jedem Datenbank-Update, "python run.py backup") und
# "python run.py restore". Debian liefert Version 17; Postgres 16 als Server geht trotzdem (siehe db.restore).
# age: Verschluesselung der Backups auf WebDAV/Nextcloud (saga/backups.py, nur oeffentliche Schluessel).
# tini: kleiner Init-Prozess (PID 1) - reicht Signale weiter und raeumt verwaiste Prozesse ab.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client age tini \
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
# 2.5 (Gjallarhorn P6): oeffentlicher Release-Schluessel fuer das systemd-Deploy-Skript (saga/horn_ops.py release_pub)
COPY gjallarhorn/release.pub ./gjallarhorn/release.pub
COPY docker/entrypoint.sh /entrypoint.sh
COPY --from=media /out/saga-media /usr/local/bin/saga-media
COPY --from=horn /out/gjallarhorn /usr/local/bin/gjallarhorn

# Erst nach pip install setzen (Datei entsteht beim Start im Entrypoint).
ENV CA_FILE=/tmp/ca-bundle.pem \
    SSL_CERT_FILE=/tmp/ca-bundle.pem

USER 10001
# 8443: eingebauter Gjallarhorn-Knoten (HORN_PORT, TCP und optional UDP) - lauscht nur mit Feature-Flag calls.horn
EXPOSE 8080 8081 8443/tcp 8443/udp
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=3)" || exit 1

# tini -> entrypoint.sh -> saga-media (PORT, Dateien) -> python run.py (intern 127.0.0.1:MEDIA_INTERNAL_PORT, Standard 8079)
#                                        -> gjallarhorn node (HORN_PORT, nur mit Feature-Flag calls.horn)
ENTRYPOINT ["/usr/bin/tini", "--", "/bin/sh", "/entrypoint.sh"]
