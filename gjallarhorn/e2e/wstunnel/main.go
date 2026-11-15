// wstunnel: Stellvertreter des App-Tunnels (Saga 2.5 A3, Protokoll saga-tunnel.1 v1) fuer Pruefstand und Messungen mit
// Chromium als libwebrtc. Nur fuer Tests.
//
// libwebrtc spricht ICE-TCP mit einem lokalen Port (die Testseite biegt den passiven TCP-Kandidaten des Knotens auf
// 127.0.0.1:<listen>); je lokaler Verbindung nimmt wstunnel einen WebSocket zur Saga-Adresse
// (wss://saga.example.org/api/sfu/ws, Unterprotokoll saga-tunnel.1), meldet sich in der ersten Textnachricht mit den
// TURN-Zugangsdaten an und reicht die Bytes durch - wie die Apps: ohne eigenes Fenster, mit Quittungen
// (ack_bytes/ack_ms aus der Antwort ok).
//
//	wstunnel -listen 127.0.0.1:8526 -url wss://saga.example.org:8521/api/sfu/ws [-dial 127.0.0.1:8525] [-ctl 127.0.0.1:8527]
//	         [-pre 2] [-max 1]
//
// Zugangsdaten: POST http://<ctl>/auth mit "user:credential" (die Testseite holt sie per POST /api/sfu/turn) oder -auth.
// GET http://<ctl>/stats: Zaehler je Tunnel. -pre: so viele WebSockets vorab offen (angemeldet) halten (Vertrag 7).
package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"flag"
	"io"
	"log"
	"net"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/wstun"
)

var (
	listen = flag.String("listen", "127.0.0.1:8526", "lokaler Port fuer libwebrtc")
	wsURL  = flag.String("url", "wss://saga.example.org:8521/api/sfu/ws", "Tunnel-Endpunkt")
	dial   = flag.String("dial", "", "TCP-Ziel statt Host der URL (Engpass davor)")
	ctl    = flag.String("ctl", "127.0.0.1:8527", "Steuerung (auth, stats)")
	authF  = flag.String("auth", "", "user:credential")
	lrcv   = flag.Int("lrcvbuf", 65536, "SO_RCVBUF/SO_SNDBUF der lokalen Verbindung (0 = Standard)")
	pre    = flag.Int("pre", 0, "so viele WebSockets vorab offen halten (spart den WS-Aufbau)")
	tlog   = flag.Bool("tlog", false, "Zeitpunkte je Verbindung protokollieren")
	role   = flag.String("role", "", "audio | video (nur Diagnose am Knoten)")
)

var (
	authMu  sync.Mutex
	auth    string
	seq     atomic.Uint64
	connMu  sync.Mutex
	conns   = map[uint64]*entry{}
	lastErr atomic.Pointer[string]
)

type entry struct {
	id   uint64
	at   time.Time
	c    *wstun.Conn
	dial time.Duration
}

func getAuth() string { authMu.Lock(); defer authMu.Unlock(); return auth }

func dialWS() (*wstun.Conn, time.Duration, error) {
	user, cred, _ := strings.Cut(getAuth(), ":")
	d := websocket.Dialer{
		TLSClientConfig:  &tls.Config{InsecureSkipVerify: true, NextProtos: []string{"http/1.1"}}, // nur Pruefstand
		HandshakeTimeout: 10 * time.Second, ReadBufferSize: 16 << 10, WriteBufferSize: 16 << 10,
		Subprotocols: []string{wstun.Subprotocol},
	}
	if *dial != "" {
		d.NetDialContext = func(ctx context.Context, network, _ string) (net.Conn, error) {
			var nd net.Dialer
			c, err := nd.DialContext(ctx, network, *dial)
			if tc, ok := c.(*net.TCPConn); ok {
				_ = tc.SetNoDelay(true)
			}
			return c, err
		}
	}
	t0 := time.Now()
	ws, resp, err := d.Dial(*wsURL, nil)
	if err != nil {
		if resp != nil {
			b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
			e := resp.Status + " " + strings.TrimSpace(string(b))
			lastErr.Store(&e)
			log.Printf("ws: %v %s", err, e)
		}
		return nil, 0, err
	}
	r, err := wstun.ClientAuth(ws, user, cred, *role, 10*time.Second)
	if err != nil {
		e := err.Error()
		lastErr.Store(&e)
		_ = ws.Close()
		return nil, 0, err
	}
	dt := time.Since(t0)
	opt := wstun.Options{Ack: true, AckBytes: r.AckBytes, AckDelay: time.Duration(r.AckMs) * time.Millisecond}
	return wstun.New(ws, ws.LocalAddr(), ws.RemoteAddr(), opt), dt, nil
}

// Vorrat vorab geoeffneter, angemeldeter WebSockets: ein neuer lokaler Strom wartet dann nicht auf TCP+TLS+Upgrade.
var pool = make(chan *wstun.Conn, 16)

func fill() {
	for {
		if len(pool) >= *pre || getAuth() == "" {
			time.Sleep(50 * time.Millisecond)
			continue
		}
		c, _, err := dialWS()
		if err != nil {
			time.Sleep(time.Second)
			continue
		}
		pool <- c
	}
}

func getWS() (*wstun.Conn, time.Duration, error) {
	for *pre > 0 {
		select {
		case c := <-pool:
			select {
			case <-c.Done(): // inzwischen zu (Frist fuer das erste Datenbyte, Proxy)
				continue
			default:
			}
			return c, 0, nil
		default:
		}
		break
	}
	return dialWS()
}

func track(c *wstun.Conn, dt time.Duration) uint64 {
	id := seq.Add(1)
	connMu.Lock()
	conns[id] = &entry{id: id, at: time.Now(), c: c, dial: dt}
	connMu.Unlock()
	go func() {
		<-c.Done()
		time.Sleep(30 * time.Second) // Zaehler noch kurz lesbar
		connMu.Lock()
		delete(conns, id)
		connMu.Unlock()
	}()
	return id
}

func serveTCP() {
	lc := net.ListenConfig{Control: func(_, _ string, rc syscall.RawConn) error {
		if *lrcv > 0 { // vor listen: gilt fuer angenommene Verbindungen
			_ = rc.Control(func(fd uintptr) { _ = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_RCVBUF, *lrcv) })
		}
		return nil
	}}
	ln, err := lc.Listen(context.Background(), "tcp", *listen)
	if err != nil {
		log.Fatal(err)
	}
	log.Printf("wstunnel: lauscht auf %s -> %s", *listen, *wsURL)
	for {
		lcn, err := ln.Accept()
		if err != nil {
			log.Fatal(err)
		}
		if tc, ok := lcn.(*net.TCPConn); ok && *lrcv > 0 {
			_ = tc.SetReadBuffer(*lrcv)
			_ = tc.SetWriteBuffer(*lrcv)
			_ = tc.SetNoDelay(true)
		}
		go pipe(lcn)
	}
}

func pipe(lcn net.Conn) {
	defer lcn.Close()
	ta := time.Now()
	c, dt, err := getWS()
	if err != nil {
		log.Printf("tunnel: %v", err)
		return
	}
	id := track(c, dt)
	log.Printf("tunnel %d: offen (%d ms)", id, dt.Milliseconds())
	go func() {
		b := make([]byte, 16<<10)
		first := true
		for {
			n, err := c.Read(b)
			if n > 0 {
				if first && *tlog {
					log.Printf("tunnel %d: erstes Byte vom Knoten nach %d ms", id, time.Since(ta).Milliseconds())
				}
				first = false
				if _, werr := lcn.Write(b[:n]); werr != nil {
					break
				}
			}
			if err != nil {
				break
			}
		}
		_ = lcn.Close()
	}()
	buf := make([]byte, 16<<10)
	for {
		n, err := lcn.Read(buf)
		if n > 0 {
			if _, werr := c.Write(buf[:n]); werr != nil {
				break
			}
		}
		if err != nil {
			break
		}
	}
	_ = c.Close()
}

func main() {
	flag.Parse()
	auth = *authF
	mux := http.NewServeMux()
	cors := func(w http.ResponseWriter) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		w.Header().Set("Access-Control-Allow-Private-Network", "true")
	}
	mux.HandleFunc("/auth", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		if r.Method == http.MethodOptions {
			return
		}
		b, _ := io.ReadAll(io.LimitReader(r.Body, 1024))
		authMu.Lock()
		auth = strings.TrimSpace(string(b))
		authMu.Unlock()
		_, _ = io.WriteString(w, "ok")
	})
	mux.HandleFunc("/stats", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		connMu.Lock()
		out := []map[string]any{}
		for _, e := range conns {
			out = append(out, map[string]any{"id": e.id, "dial_ms": e.dial.Milliseconds(), "age_s": int(time.Since(e.at).Seconds()), "st": e.c.Snap()})
		}
		connMu.Unlock()
		le := ""
		if p := lastErr.Load(); p != nil {
			le = *p
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"tunnels": out, "pool": len(pool), "last_err": le})
	})
	go func() { log.Fatal(http.ListenAndServe(*ctl, mux)) }()
	if *pre > 0 {
		go fill()
	}
	serveTCP()
}
