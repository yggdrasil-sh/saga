package main

import (
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const keyHeader = "X-Saga-Media-Key"

// peerAddr: Adresse der direkt verbundenen Gegenstelle.
func peerAddr(r *http.Request) string {
	if h, _, err := net.SplitHostPort(r.RemoteAddr); err == nil {
		return h
	}
	return r.RemoteAddr
}

// clientIP: dieselbe Regel wie uvicorn (fuer Log und Grenzen je Adresse).
func clientIP(r *http.Request, t trustSet) string {
	peer := peerAddr(r)
	if !t.has(hostOf(peer)) {
		return peer
	}
	var hosts []string
	for _, v := range r.Header.Values("X-Forwarded-For") {
		for _, h := range strings.Split(v, ",") {
			hosts = append(hosts, strings.TrimSpace(h))
		}
	}
	if len(hosts) == 0 {
		return peer
	}
	if t.all {
		return hosts[0]
	}
	for i := len(hosts) - 1; i >= 0; i-- {
		if !t.has(hostOf(hosts[i])) {
			return hosts[i]
		}
	}
	return hosts[0]
}

// setForwarded: Weiterleitungs-Koepfe sicher setzen. saga-media ermittelt den Client selbst nach derselben Regel wie
// uvicorn (TRUSTED_PROXIES) und schickt uvicorn nur diesen einen Eintrag - uvicorn vertraut hinter saga-media zusaetzlich
// 127.0.0.1 (run.py), eingeschleuste Eintraege nicht vertrauter Gegenstellen zaehlen also nie. X-Forwarded-Proto gilt nur
// von vertrauten Proxys. Hat eine nicht vertraute Gegenstelle X-Forwarded-For geschickt, geht er als X-Saga-Orig-For
// mit (nur fuer den Hinweis "Proxy nicht in TRUSTED_PROXIES", saga/web_auth.py). X-Forwarded-Host und Forwarded wertet
// Saga nur fuer Umleitungen auf die feste BASE_URL aus - sie gehen wie bisher unveraendert durch.
func setForwarded(out http.Header, in *http.Request, t trustSet) {
	trusted := t.has(hostOf(peerAddr(in)))
	out.Set("X-Forwarded-For", clientIP(in, t))
	out.Del("X-Forwarded-Proto")
	out.Del("X-Saga-Orig-For")
	if trusted {
		if p := in.Header.Get("X-Forwarded-Proto"); p != "" {
			out.Set("X-Forwarded-Proto", p)
		}
	} else if v := in.Header.Values("X-Forwarded-For"); len(v) > 0 {
		out.Set("X-Saga-Orig-For", strings.Join(v, ", "))
	}
	if v := in.Header.Values("X-Forwarded-Host"); len(v) > 0 {
		out["X-Forwarded-Host"] = append([]string(nil), v...)
	}
	if v := in.Header.Values("Forwarded"); len(v) > 0 {
		out["Forwarded"] = append([]string(nil), v...)
	}
	out.Del(keyHeader)
}

type bufPool struct{ p sync.Pool }

func (b *bufPool) Get() []byte  { return *(b.p.Get().(*[]byte)) }
func (b *bufPool) Put(x []byte) { b.p.Put(&x) }

func newBufPool() *bufPool { return newBufPoolSize(32 * 1024) }

func newBufPoolSize(n int) *bufPool {
	return &bufPool{p: sync.Pool{New: func() any { b := make([]byte, n); return &b }}}
}

func newTransport() *http.Transport {
	return &http.Transport{
		Proxy:                 nil,
		DialContext:           (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		MaxIdleConns:          512,
		MaxIdleConnsPerHost:   512,
		IdleConnTimeout:       30 * time.Second, // kuerzer als uvicorn timeout_keep_alive (75 s, run.py)
		DisableCompression:    true,
		ExpectContinueTimeout: time.Second,
		WriteBufferSize:       32 * 1024,
		ReadBufferSize:        32 * 1024,
	}
}

// newProxy: alles ausser den Dateien an uvicorn. Antworten ohne Laenge (SSE: Live-Kanal, KI-Stream) gibt
// ReverseProxy sofort weiter (FlushInterval -1 fuer text/event-stream und ContentLength -1).
func newProxy(c *config, tr http.RoundTripper) *httputil.ReverseProxy {
	return &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.Out.URL.Scheme = "http"
			pr.Out.URL.Host = c.upstream
			pr.Out.Host = pr.In.Host
			setForwarded(pr.Out.Header, pr.In, c.trusted)
		},
		ModifyResponse: func(resp *http.Response) error {
			if resp.Header.Get(authHint) == "1" && resp.Request != nil {
				markAuth(resp.Request) // Saga: Anfrage war angemeldet (S1b)
			}
			resp.Header.Del(authHint)
			return nil
		},
		Transport:  tr,
		BufferPool: newBufPool(),
		ErrorLog:   log.New(io.Discard, "", 0),
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			if b, ok := r.Body.(*stallBody); ok && b.stalled.Load() {
				// Upload stockt (Slowloris): Verbindung schliessen, nicht auf den Rest warten
				w.Header().Set("Connection", "close")
				w.Header().Set("Cache-Control", "no-store")
				w.WriteHeader(http.StatusRequestTimeout)
				return
			}
			if r.Context().Err() == nil {
				logf("Weiterleitung an Saga fehlgeschlagen: %s %s: %v", r.Method, r.URL.Path, err)
			}
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			w.Header().Set("Cache-Control", "no-store")
			w.Header().Set("Retry-After", "5")
			w.WriteHeader(http.StatusBadGateway)
			_, _ = io.WriteString(w, "Saga startet gerade oder ist nicht erreichbar.\n")
		},
	}
}

// stallBody: Upload-Koerper (Slowloris): je Lesevorgang hoechstens c.stall warten. Am Ende die Frist wieder
// aufheben - sonst beendet net/http beim Hintergrund-Lesen eine noch laufende Antwort (z. B. Stream).
type stallBody struct {
	rc      io.ReadCloser
	ctl     *http.ResponseController
	d       time.Duration
	done    atomic.Bool // Koerper ganz gelesen
	stalled atomic.Bool // Lesefrist abgelaufen
}

func (b *stallBody) Read(p []byte) (int, error) {
	if !b.done.Load() {
		_ = b.ctl.SetReadDeadline(time.Now().Add(b.d))
	}
	n, err := b.rc.Read(p)
	if err == io.EOF && b.done.CompareAndSwap(false, true) { // nur am Ende aufheben; nach Zeitueberschreitung bleibt sie
		_ = b.ctl.SetReadDeadline(time.Time{})
	}
	if err != nil && isTimeout(err) {
		b.stalled.Store(true)
	}
	return n, err
}

// Close: Antwortet Saga, bevor der Koerper da ist (z. B. 413 bei zu grosser Content-Length), haengt die Weiterleitung
// noch in Read - das Schliessen wartet sonst auf den Client. Frist auf "jetzt": Read kehrt sofort zurueck, die Antwort
// geht raus, die Verbindung wird danach geschlossen (wie bei uvicorn direkt).
func (b *stallBody) Close() error {
	if !b.done.Load() {
		_ = b.ctl.SetReadDeadline(time.Now())
	}
	return b.rc.Close()
}
