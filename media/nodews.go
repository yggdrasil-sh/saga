package main

// Geraete-HTTP des eingebauten Knotens ueber PORT (Saga 2.5, A3 "Anrufe ueber die Saga-Adresse"):
//
// Auch ohne "Ein Port" (SAGA_TLS) legt saga-media fuer den eingebauten Knoten einen Unix-Socket fuer dessen internes
// HTTP an (node.sock in einem privaten Ordner, an den Knoten als fd 3, HORN_INNER_HTTP_FD) und leitet die
// Geraete-Pfade (/api/sfu/*, /probe) dorthin weiter - hinter einem TLS-beendenden Reverse Proxy (Nginx Proxy Manager)
// erreichen die Apps den Knoten so unter der normalen Saga-Adresse, ohne eigenen Port und ohne eigenes Zertifikat.
// Die echte Client-Adresse (X-Forwarded-For nur von TRUSTED_PROXIES) geht in X-Saga-Einport-For, gilt nur mit dem
// Schluessel X-Saga-Einport-Key (= HORN_CTL_KEY).
//
// Medien-Tunnel /api/sfu/ws (WebSocket, saga-tunnel.1; spaeter auch saga-frames.1 fuer den Browser-Weg): roh
// weitergereicht - Anfrage an den Knoten, Antwort zurueck, nach 101 nur noch Bytes in beide Richtungen kopieren.
// Bytes ja, Logik nein: kein eigener Puffer ausser dem Kopierpuffer, kein Ping, keine Rahmen. Der Knoten prueft
// Unterprotokoll, Zugang (erste Nachricht, Frist 10 s), Grenzen je Client-Adresse und Lebenszeichen. Die Verbindung
// zaehlt hier weiter gegen MEDIA_MAX_CONNS und MEDIA_MAX_CONNS_IP (vertraute Proxys ausgenommen, wie alle).
// Gilt in beiden Betriebsarten ("Ein Port": ueber dessen node.sock).

import (
	"bufio"
	"context"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const nodeWSPath = "/api/sfu/ws"

// nodeWSWait: Frist fuer die Antwort des Knotens auf die Upgrade-Anfrage (Tests aendern sie).
var nodeWSWait = 10 * time.Second

var wsCopyPool = sync.Pool{New: func() any { b := make([]byte, 32<<10); return &b }}

// isNodeWS: Upgrade-Anfrage an den Medien-Tunnel (ohne Upgrade beantwortet der Knoten mit 426, ueber den Proxy-Weg).
func isNodeWS(r *http.Request) bool {
	return r.Method == http.MethodGet && r.URL.Path == nodeWSPath &&
		strings.EqualFold(r.Header.Get("Upgrade"), "websocket") && headerHasToken(r.Header, "Connection", "upgrade")
}

// nodeLink: internes HTTP des eingebauten Knotens ohne "Ein Port" (privater Ordner 0700, node.sock 0600).
type nodeLink struct {
	c     *config
	key   string
	dir   string
	sock  string
	proxy *httputil.ReverseProxy
}

func newNodeLink(c *config, key string) (*nodeLink, error) {
	dir, err := os.MkdirTemp("", "saga-knoten-")
	if err != nil {
		return nil, err
	}
	_ = os.Chmod(dir, 0o700)
	n := &nodeLink{c: c, key: key, dir: dir, sock: filepath.Join(dir, "node.sock")}
	n.proxy = newNodeProxy(n.sock, key, c)
	return n, nil
}

// listenerFile: neuer Lauscher auf node.sock fuer einen Start des Knotens (der Aufrufer schliesst die Datei nach
// cmd.Start; der Knoten erbt sie als fd 3).
func (n *nodeLink) listenerFile() (*os.File, error) {
	_ = os.Remove(n.sock)
	ln, err := net.ListenUnix("unix", &net.UnixAddr{Name: n.sock, Net: "unix"})
	if err != nil {
		return nil, err
	}
	_ = os.Chmod(n.sock, 0o600)
	ln.SetUnlinkOnClose(false)
	f, err := ln.File()
	_ = ln.Close()
	return f, err
}

func (n *nodeLink) close() { _ = os.RemoveAll(n.dir) }

// nodeEnv: Umgebung des Knotens (ohne "Ein Port"). HORN_TUNNEL_URL nur mit https-BASE_URL (DNS-Name): dann darf der
// Knoten ohne eigenen Namen und ohne Zertifikat im Tunnel-Betrieb laufen (gjallarhorn server.go).
func (n *nodeLink) nodeEnv() []string {
	env := []string{"HORN_INNER_HTTP_FD=3"}
	if n.c.tunnelOrigin != "" {
		env = append(env, "HORN_TUNNEL_URL="+n.c.tunnelOrigin)
	}
	return env
}

// newNodeProxy: Geraete-HTTP an das interne HTTP des Knotens (Unix-Socket), mit Schluessel und echter Client-Adresse.
func newNodeProxy(sock, key string, c *config) *httputil.ReverseProxy {
	tr := &http.Transport{
		Proxy: nil,
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			var d net.Dialer
			return d.DialContext(ctx, "unix", sock)
		},
		MaxIdleConns: 64, MaxIdleConnsPerHost: 64, IdleConnTimeout: 30 * time.Second, DisableCompression: true,
	}
	return &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.Out.URL.Scheme = "http"
			pr.Out.URL.Host = "knoten"
			pr.Out.Host = pr.In.Host
			h := pr.Out.Header
			h.Del(keyHeader)
			h.Set(einKeyHeader, key)
			h.Set(einForHeader, clientIP(pr.In, c.trusted))
		},
		Transport: tr,
		ErrorLog:  log.New(io.Discard, "", 0),
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "knoten nicht bereit"})
		},
	}
}

// serveNodeWS: Medien-Tunnel roh an den Knoten (siehe oben). sock/key: node.sock und Schluessel der Betriebsart.
func (m *media) serveNodeWS(w http.ResponseWriter, r *http.Request, sock, key string) {
	notReady := func() {
		w.Header().Set("Retry-After", "5")
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "knoten nicht bereit"})
	}
	up, err := net.DialTimeout("unix", sock, 2*time.Second)
	if err != nil {
		notReady()
		return
	}
	out := r.Clone(r.Context())
	out.Header.Del(keyHeader)
	out.Header.Del(authHint)
	out.Header.Set(einKeyHeader, key)
	out.Header.Set(einForHeader, clientIP(r, m.c.trusted))
	out.Header.Del("X-Forwarded-For") // die Adresse steht im Schluessel-Kopf; der Knoten wertet XFF ohnehin nicht aus
	out.RequestURI = ""
	_ = up.SetDeadline(time.Now().Add(nodeWSWait))
	if err := out.Write(up); err != nil {
		_ = up.Close()
		notReady()
		return
	}
	ubr := bufio.NewReaderSize(up, 4096)
	resp, err := http.ReadResponse(ubr, out)
	if err != nil {
		_ = up.Close()
		notReady()
		return
	}
	_ = up.SetDeadline(time.Time{})
	if resp.StatusCode != http.StatusSwitchingProtocols { // 400/429/503 ...: unveraendert zurueck
		defer up.Close()
		defer resp.Body.Close()
		h := w.Header()
		for k, vs := range resp.Header {
			for _, v := range vs {
				h.Add(k, v)
			}
		}
		w.WriteHeader(resp.StatusCode)
		_, _ = io.Copy(w, io.LimitReader(resp.Body, 64<<10))
		return
	}
	rc := http.NewResponseController(w)
	cl, brw, err := rc.Hijack()
	if err != nil {
		_ = up.Close()
		return
	}
	_ = cl.SetDeadline(time.Time{})
	if err := resp.Write(cl); err != nil { // 101 samt Sec-WebSocket-Accept/-Protocol (kein Koerper)
		_ = cl.Close()
		_ = up.Close()
		return
	}
	var src io.Reader = cl
	if brw.Reader.Buffered() > 0 { // schon gelesene Bytes des Clients nicht verlieren
		src = io.MultiReader(io.LimitReader(brw.Reader, int64(brw.Reader.Buffered())), cl)
	}
	var usrc io.Reader = up
	if ubr.Buffered() > 0 {
		usrc = io.MultiReader(io.LimitReader(ubr, int64(ubr.Buffered())), up)
	}
	brw = nil
	// Handler kehrt sofort zurueck (wie stream.go); zwei Kopier-Goroutinen fuer die Dauer der Verbindung
	go func() {
		wsCopy(up, src)
		if uc, ok := up.(*net.UnixConn); ok {
			_ = uc.CloseWrite()
		}
	}()
	go func() {
		wsCopy(cl, usrc) // Knoten hat geschlossen (oder der Client, dann scheitert Write)
		_ = cl.Close()
		_ = up.Close()
	}()
}

// wsCopy: Bytes sofort weiter (was gerade da ist, ohne zu sammeln) - Medien, keine Verzoegerung durch Puffern.
func wsCopy(dst io.Writer, src io.Reader) {
	bp := wsCopyPool.Get().(*[]byte)
	defer wsCopyPool.Put(bp)
	_, _ = io.CopyBuffer(struct{ io.Writer }{dst}, struct{ io.Reader }{src}, *bp)
}
