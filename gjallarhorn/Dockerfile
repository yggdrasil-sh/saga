# Gjallarhorn (Saga 2.5) – Knoten-Image, hier als Horn-Probe.
# Statisch (CGO aus), auf der Build-Plattform fuer die Zielarchitektur
# uebersetzt (amd64/arm64 ohne Emulation). Laufzeit: distroless, ohne Shell.
#   docker build -t gjallarhorn:probe gjallarhorn/
#   docker buildx build --platform linux/amd64,linux/arm64 -t ... gjallarhorn/
FROM --platform=$BUILDPLATFORM golang:1.24-alpine AS build
ARG TARGETOS TARGETARCH
ARG VERSION=2.5.0-probe
ENV CGO_ENABLED=0 GOFLAGS=-trimpath
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go vet ./... && go test ./internal/... \
 && GOOS=$TARGETOS GOARCH=$TARGETARCH go build -ldflags="-s -w -X main.version=${VERSION}" -o /out/gjallarhorn . \
 && mkdir -p /out/data/results /out/data/tls

FROM gcr.io/distroless/static-debian12:nonroot
LABEL org.opencontainers.image.title="Gjallarhorn" \
      org.opencontainers.image.description="Weiterleiter-Knoten fuer Saga-Gruppenanrufe (Horn-Probe)" \
      org.opencontainers.image.source="https://github.com/yggdrasil-sh/saga" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY --from=build /out/gjallarhorn /usr/local/bin/gjallarhorn
COPY --from=build --chown=65532:65532 /out/data /data
ENV HORN_DATA=/data
VOLUME /data
EXPOSE 443/tcp 443/udp
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/gjallarhorn"]
# Standard (P6): Waechter als PID 1 - startet "node" und uebernimmt signierte Updates nach /data/bin (kein Docker-Socket).
# Horn-Probe weiterhin mit "... gjallarhorn:<tag> probe".
CMD ["watch"]
