package main

// Eigene Push-Verbindung der Apps (2.5, saga/push_stream.py): GET /api/push/stream mit WebSocket-Upgrade.
//
// httputil.ReverseProxy reicht WebSocket-Verbindungen auch durch, haelt dabei aber je Richtung einen 32-KiB-Puffer
// (gemessen ~84 KB je ruhender Verbindung in saga-media). Diese Verbindungen sind fast immer still (ein Lebenszeichen
// alle paar Minuten, selten eine Mitteilung von wenigen KB) - deshalb hier ein schlanker Tunnel: Anfrage mit denselben
// Weiterleitungs-Koepfen wie beim Proxy an Saga schreiben, dann Bytes in beide Richtungen mit kleinen Puffern
// kopieren (Antwort 101 bzw. Ablehnung kommt unveraendert von Saga). Keine eigene Logik, kein eigener Ping.
// Die Verbindung zaehlt weiter gegen MEDIA_MAX_CONNS und die Grenze je Adresse.

import (
	"bufio"
	"io"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"
)

const streamPath = "/api/push/stream"

// streamBuf: Mitteilungen sind hoechstens ~4 KB (Web-Push-Grenze), App -> Server nur 1-9 Byte.
const streamBufDown, streamBufUp = 4096, 512

var (
	downPool = sync.Pool{New: func() any { b := make([]byte, streamBufDown); return &b }}
	upPool   = sync.Pool{New: func() any { b := make([]byte, streamBufUp); return &b }}
)

func isStreamUpgrade(r *http.Request) bool {
	return r.Method == http.MethodGet && r.URL.Path == streamPath &&
		strings.EqualFold(r.Header.Get("Upgrade"), "websocket") && headerHasToken(r.Header, "Connection", "upgrade")
}

func headerHasToken(h http.Header, name, tok string) bool {
	for _, v := range h.Values(name) {
		for _, p := range strings.Split(v, ",") {
			if strings.EqualFold(strings.TrimSpace(p), tok) {
				return true
			}
		}
	}
	return false
}

// copyIdle: kopiert mit einem Puffer aus dem Pool, der nur kurz belegt ist, wenn
// Daten kommen - im Leerlauf wartet die Goroutine mit einem 1-Byte-Lesevorgang (kein grosser Puffer je Verbindung).
func copyIdle(dst io.Writer, src io.Reader, pool *sync.Pool) {
	var one [1]byte
	for {
		n, err := src.Read(one[:])
		if n > 0 {
			bp := pool.Get().(*[]byte)
			b := *bp
			b[0] = one[0]
			m := 1
			// was schon da ist, gleich mitnehmen (ohne zu warten): ein Rahmen kommt meist in einem Stueck
			if br, ok := src.(*bufio.Reader); ok && br.Buffered() > 0 {
				k, _ := br.Read(b[1:])
				m += k
			} else if c, ok := src.(net.Conn); ok {
				// Frist knapp in der Zukunft: Go liest dann sofort, was im Socket liegt (Frist "jetzt" liest gar nicht)
				_ = c.SetReadDeadline(time.Now().Add(time.Millisecond))
				k, _ := c.Read(b[1:])
				m += k
				_ = c.SetReadDeadline(time.Time{})
			}
			_, werr := dst.Write(b[:m])
			pool.Put(bp)
			if werr != nil {
				return
			}
		}
		if err != nil {
			return
		}
	}
}

// serveStream: Tunnel fuer die eigene Push-Verbindung (siehe oben).
func (m *media) serveStream(w http.ResponseWriter, r *http.Request) {
	up, err := net.DialTimeout("tcp", m.c.upstream, 5*time.Second)
	if err != nil {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Retry-After", "5")
		http.Error(w, "Saga startet gerade oder ist nicht erreichbar.", http.StatusBadGateway)
		return
	}
	out := r.Clone(r.Context())
	setForwarded(out.Header, r, m.c.trusted)
	out.Header.Del(authHint)
	out.RequestURI = ""
	if err := out.Write(up); err != nil { // Kopf (GET ohne Koerper) an Saga
		_ = up.Close()
		http.Error(w, "Saga nicht erreichbar.", http.StatusBadGateway)
		return
	}
	rc := http.NewResponseController(w)
	cl, brw, err := rc.Hijack()
	if err != nil {
		_ = up.Close()
		return
	}
	_ = cl.SetDeadline(time.Time{})
	var src io.Reader = cl
	if brw.Reader.Buffered() > 0 { // schon gelesene Bytes des Clients nicht verlieren
		src = brw.Reader
	}
	// Der Handler kehrt sofort zurueck: so endet die Goroutine von net/http samt ihrem (gewachsenen) Stapel, Anfrage-
	// und Antwort-Objekten - fuer die Dauer der Verbindung bleiben nur die zwei kleinen Kopier-Goroutinen.
	brw = nil
	go func() {
		copyIdle(up, src, &upPool)
		if tc, ok := up.(*net.TCPConn); ok {
			_ = tc.CloseWrite()
		}
	}()
	go func() {
		copyIdle(cl, up, &downPool) // Saga hat geschlossen (oder der Client, dann scheitert Write)
		_ = cl.Close()
		_ = up.Close()
	}()
}
