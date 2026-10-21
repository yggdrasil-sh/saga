package main

import (
	"bufio"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"
)

// TestStreamTunnel: /api/push/stream mit Upgrade geht als schlanker Tunnel an Saga - Kopf mit sicheren
// Weiterleitungs-Koepfen (kein Geheimnis), Antwort und Bytes unveraendert in beide Richtungen, Schliessen kommt durch.
func TestStreamTunnel(t *testing.T) {
	up, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer up.Close()
	got := make(chan *http.Request, 1)
	go func() {
		c, err := up.Accept()
		if err != nil {
			return
		}
		defer c.Close()
		br := bufio.NewReader(c)
		r, err := http.ReadRequest(br)
		if err != nil {
			return
		}
		got <- r
		_, _ = io.WriteString(c, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
		b := make([]byte, 3)
		if _, err := io.ReadFull(br, b); err != nil {
			return
		}
		_, _ = c.Write(append([]byte("pong:"), b...))
		_, _ = c.Write([]byte(strings.Repeat("x", 3000))) // eine grosse Mitteilung
	}()
	c := &config{upstream: up.Addr().String(), trusted: parseTrust("10.0.0.1")}
	m := &media{c: c}
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !isStreamUpgrade(r) {
			t.Errorf("kein Stream-Upgrade erkannt")
			return
		}
		m.serveStream(w, r)
	})}
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	go func() { _ = srv.Serve(ln) }()
	defer srv.Close()

	cl, err := net.Dial("tcp", ln.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer cl.Close()
	_, _ = io.WriteString(cl, "GET /api/push/stream HTTP/1.1\r\nHost: saga.test\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"+
		"Authorization: Bearer hkd_x\r\nX-Saga-Media-Key: boese\r\nX-Forwarded-For: 6.6.6.6\r\n\r\n")
	_ = cl.SetDeadline(time.Now().Add(5 * time.Second))
	br := bufio.NewReader(cl)
	resp, err := http.ReadResponse(br, nil)
	if err != nil || resp.StatusCode != 101 {
		t.Fatalf("Antwort: %v %v", resp, err)
	}
	r := <-got
	if r.Header.Get("Authorization") != "Bearer hkd_x" || r.Header.Get(keyHeader) != "" || r.Header.Get("X-Forwarded-For") != "127.0.0.1" ||
		r.Header.Get("X-Saga-Orig-For") != "6.6.6.6" || !strings.EqualFold(r.Header.Get("Upgrade"), "websocket") || r.Host != "saga.test" {
		t.Errorf("Kopf an Saga: %v host=%s", r.Header, r.Host)
	}
	_, _ = cl.Write([]byte("abc"))
	want := "pong:abc" + strings.Repeat("x", 3000)
	b := make([]byte, len(want))
	if _, err := io.ReadFull(br, b); err != nil || string(b) != want {
		t.Fatalf("Daten zurueck: %q %v", b[:16], err)
	}
	// Saga schliesst -> Client sieht EOF
	if _, err := br.ReadByte(); err != io.EOF {
		t.Errorf("Schliessen nicht durchgereicht: %v", err)
	}
}

func TestIsStreamUpgrade(t *testing.T) {
	mk := func(method, path, upg, conn string) *http.Request {
		r, _ := http.NewRequest(method, "http://x"+path, nil)
		if upg != "" {
			r.Header.Set("Upgrade", upg)
		}
		if conn != "" {
			r.Header.Set("Connection", conn)
		}
		return r
	}
	if !isStreamUpgrade(mk("GET", "/api/push/stream", "WebSocket", "keep-alive, Upgrade")) {
		t.Error("Upgrade nicht erkannt")
	}
	for _, r := range []*http.Request{mk("POST", "/api/push/stream", "websocket", "upgrade"), mk("GET", "/api/push/streamx", "websocket", "upgrade"),
		mk("GET", "/api/push/stream", "", ""), mk("GET", "/api/events", "websocket", "upgrade")} {
		if isStreamUpgrade(r) {
			t.Errorf("faelschlich erkannt: %s %s", r.Method, r.URL.Path)
		}
	}
}
