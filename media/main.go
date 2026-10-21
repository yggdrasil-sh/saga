// saga-media: kleiner Mediendienst von Saga (ab 2.4) im selben Container vor uvicorn.
//
// Er lauscht auf dem bisherigen PORT und leitet alles an uvicorn auf 127.0.0.1:MEDIA_INTERNAL_PORT weiter (Streaming,
// Server-Sent Events ungepuffert, WebSocket-Upgrade, X-Forwarded-* sicher gesetzt). Selbst bedient er nur
// GET/HEAD /api/chat/files/{fid} direkt von der Platte (Range, sendfile, ETag = fid) - so laufen keine Medienbytes
// mehr durch Python. Die Berechtigung bleibt in Python: einmal je Datei und Sitzung GET /internal/media/authz
// (nur mit dem gemeinsamen Geheimnis, von aussen gesperrt), hoechstens 60 s gemerkt.
//
// Aufruf im Container:  saga-media -- python /app/run.py
// saga-media startet Saga als Kindprozess (SAGA_INTERNAL_PORT, SAGA_MEDIA_KEY), reicht Signale weiter und endet
// mit Saga. Nur Standardbibliothek. Lizenz: AGPL-3.0-only wie Saga.
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path"
	"strconv"
	"strings"
	"syscall"
	"time"
)

var version = "dev"

func logf(format string, a ...any) {
	fmt.Fprintf(os.Stdout, "saga-media: "+format+"\n", a...)
}

func (m *media) access(r *http.Request, ip string, status int, n int64, t0 time.Time) {
	if !m.c.accessLog {
		return
	}
	// wie uvicorn ("INFO:     ip:port - "GET /pfad HTTP/1.1" 200 OK"), dazu Bytes und Dauer
	fmt.Fprintf(os.Stdout, "INFO:     %s - \"%s %s HTTP/%d.%d\" %d %s [saga-media %d B %d ms]\n", ip, r.Method, r.URL.RequestURI(),
		r.ProtoMajor, r.ProtoMinor, status, http.StatusText(status), n, time.Since(t0).Milliseconds())
}

// ServeHTTP: Dateien selbst, /internal/ nie von aussen, alles andere an uvicorn.
func (m *media) ServeHTTP(rw http.ResponseWriter, r *http.Request) {
	w := &lowerWriter{ResponseWriter: rw}
	p := r.URL.Path
	if p == "/internal/media/warm" && r.Method == http.MethodPost && keyOK(r, m.c.key) { // 2.4: nur Python (Vorababruf)
		m.serveWarm(w, r)
		return
	}
	if c := path.Clean("/" + p); c == "/internal" || strings.HasPrefix(c, "/internal/") {
		w.Header().Set("Cache-Control", "no-store")
		http.NotFound(w, r)
		return
	}
	r.Header.Del(keyHeader)
	// Was saga-media selbst bedient (alles andere geht an Saga): Dateien lesen (Range), 2.4 Uploads schreiben
	// (PUT/HEAD /api/chat/uploads/{id}) und Teile eigener Anhaenge fuer andere Server (/fed/v2/chat/files/{id}?range=).
	switch r.Method {
	case http.MethodGet, http.MethodHead:
		if fid := fileID(p); fid != "" {
			m.serveFile(w, r, fid)
			return
		}
		if id := uploadID(p); id != "" && r.Method == http.MethodHead {
			m.serveUpload(w, r, id)
			return
		}
		if r.Method == http.MethodGet && strings.HasPrefix(p, fedFilesPrefix) && r.URL.Query().Has("range") {
			if fid := p[len(fedFilesPrefix):]; fidRe.MatchString(fid) {
				m.serveFedOrigin(w, r, fid)
				return
			}
		}
	case http.MethodPut:
		if id := uploadID(p); id != "" {
			m.serveUpload(w, r, id)
			return
		}
	case http.MethodDelete:
		if id := uploadID(p); id != "" { // Abbrechen: Python loescht, die gemerkte Erlaubnis gilt nicht mehr
			m.az.forgetKind(r, kindUpload, id)
		}
	}
	if isStreamUpgrade(r) { // 2.5: eigene Push-Verbindung der Apps - schlanker Tunnel statt ReverseProxy
		m.serveStream(w, r)
		return
	}
	if r.Body != nil && r.Body != http.NoBody && r.ContentLength != 0 {
		r.Body = &stallBody{rc: r.Body, ctl: http.NewResponseController(w), d: m.c.stall}
	}
	m.proxy.ServeHTTP(w, r)
}

func randomKey() string {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

func main() {
	args := os.Args[1:]
	if len(args) > 0 && (args[0] == "-v" || args[0] == "--version" || args[0] == "version") {
		fmt.Println("saga-media", version)
		return
	}
	if len(args) > 0 && args[0] == "--" {
		args = args[1:]
	}
	log.SetFlags(0)
	c, err := loadConfig()
	if err != nil {
		logf("Konfiguration: %v", err)
		os.Exit(2)
	}
	var child *exec.Cmd
	if len(args) > 0 {
		c.key = randomKey()
		child = exec.Command(args[0], args[1:]...)
		child.Stdin, child.Stdout, child.Stderr = os.Stdin, os.Stdout, os.Stderr
		child.Env = append(os.Environ(), "SAGA_MEDIA_KEY="+c.key, "SAGA_INTERNAL_PORT="+strconv.Itoa(c.internalPort))
		child.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} // Signale nur ueber uns (kein doppeltes Strg+C)
	} else {
		c.key = os.Getenv("SAGA_MEDIA_KEY")
		if len(c.key) < 32 {
			logf("ohne Befehl braucht saga-media SAGA_MEDIA_KEY (mind. 32 Zeichen) und MEDIA_INTERNAL_PORT wie in Saga")
			os.Exit(2)
		}
	}

	tr := newTransport()
	m := &media{c: c, az: newAuthz(c, tr), proxy: newProxy(c, tr),
		lim:    &limits{perIP: map[string]int{}, max: c.maxStreams, maxIP: c.maxStreamsIP},
		client: &http.Client{Transport: tr, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }},
		ups:    &upLocks{m: map[string]bool{}}, fed: newFedCache(), bufs: newBufPoolSize(256 << 10)}
	lc := net.ListenConfig{KeepAlive: 30 * time.Second}
	ln, err := lc.Listen(context.Background(), "tcp", c.listen)
	if err != nil {
		logf("kann nicht auf %s lauschen: %v", c.listen, err)
		os.Exit(1)
	}
	srv := &http.Server{
		Handler:           m,
		ReadHeaderTimeout: 15 * time.Second, // Slowloris: Kopfzeilen muessen zuegig kommen
		IdleTimeout:       75 * time.Second,
		MaxHeaderBytes:    64 << 10,
		ErrorLog:          log.New(io.Discard, "", 0),
		ConnContext:       connContext,
	}
	lim := newLimitListener(ln, c)
	srv.ConnState = lim.connState
	srvDone := make(chan error, 1)
	go func() { srvDone <- srv.Serve(lim) }()
	logf("%s lauscht auf %s, Saga intern auf %s, Dateien aus %s (Berechtigung %d s gemerkt)", version, c.listen, c.upstream,
		c.blobDir, int(c.ttl/time.Second))

	sig := make(chan os.Signal, 2)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT, syscall.SIGHUP)
	childDone := make(chan error, 1)
	if child != nil {
		if err := child.Start(); err != nil {
			logf("kann Saga nicht starten (%s): %v", args[0], err)
			os.Exit(1)
		}
		go func() { childDone <- child.Wait() }()
	}

	code := 0
	select {
	case s := <-sig:
		logf("%v - beende", s)
		if child != nil {
			_ = child.Process.Signal(s)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
		go func() { _ = srv.Shutdown(ctx) }()
		if child != nil {
			select {
			case err := <-childDone:
				code = exitCode(err)
			case <-ctx.Done():
				logf("Saga beendet sich nicht - erzwinge")
				_ = child.Process.Kill()
				code = exitCode(<-childDone)
			}
		} else {
			<-ctx.Done()
		}
		cancel()
	case err := <-childDone:
		code = exitCode(err)
		if code == 0 {
			code = 1 // Saga darf nicht einfach wegfallen: Container neu starten lassen
		}
		logf("Saga (Python) hat sich beendet (%v) - beende ebenfalls", err)
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_ = srv.Shutdown(ctx)
		cancel()
	case err := <-srvDone:
		logf("Server beendet: %v", err)
		code = 1
		if child != nil {
			_ = child.Process.Signal(syscall.SIGTERM)
			select {
			case <-childDone:
			case <-time.After(10 * time.Second):
				_ = child.Process.Kill()
			}
		}
	}
	os.Exit(code)
}

func exitCode(err error) int {
	if err == nil {
		return 0
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		if ws, ok := ee.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
			if ws.Signal() == syscall.SIGTERM || ws.Signal() == syscall.SIGINT {
				return 0
			}
			return 128 + int(ws.Signal())
		}
		return ee.ExitCode()
	}
	return 1
}
