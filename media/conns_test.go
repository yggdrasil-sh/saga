package main

import (
	"bufio"
	"net"
	"net/http"
	"testing"
	"time"
)

// startLimited: Server mit Verbindungsgrenzen (insgesamt max, je Adresse maxIP; 127.0.0.1 nicht vertraut).
func startLimited(t *testing.T, max, maxIP int, idle time.Duration) (string, func()) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	c := &config{maxConns: max, maxConnsIP: maxIP, idleUnauth: idle, trusted: parseTrust("10.9.9.9")}
	lim := newLimitListener(ln, c)
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Cookie") == "s=gueltig" { // steht fuer: Saga hat die Anmeldung bestaetigt (authz bzw. X-Saga-Media-Auth)
			markAuth(r)
		}
		_, _ = w.Write([]byte("ok"))
	}),
		ConnContext: connContext, ConnState: lim.connState}
	go func() { _ = srv.Serve(lim) }()
	return ln.Addr().String(), func() { _ = srv.Close() }
}

// idleConn: Verbindung mit einer fertigen Anfrage, danach Keep-Alive-Leerlauf.
func idleConn(t *testing.T, addr, extra string) net.Conn {
	so, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = so.Write([]byte("GET / HTTP/1.1\r\nHost: x\r\n" + extra + "\r\n"))
	_ = so.SetReadDeadline(time.Now().Add(2 * time.Second))
	if _, err := http.ReadResponse(bufio.NewReader(so), nil); err != nil {
		t.Fatalf("Antwort: %v", err)
	}
	return so
}

func closed(so net.Conn, wait time.Duration) bool {
	_ = so.SetReadDeadline(time.Now().Add(wait))
	b := make([]byte, 1)
	_, err := so.Read(b)
	if ne, ok := err.(net.Error); ok && ne.Timeout() {
		return false
	}
	return err != nil
}

func get(addr string) error {
	cl := &http.Client{Timeout: 2 * time.Second, Transport: &http.Transport{DisableKeepAlives: true}}
	r, err := cl.Get("http://" + addr + "/")
	if err == nil {
		r.Body.Close()
	}
	return err
}

func TestConnLimitsIdleEviction(t *testing.T) {
	addr, stop := startLimited(t, 6, 3, 10*time.Second)
	defer stop()
	var idle []net.Conn
	for i := 0; i < 6; i++ { // Grenze je Adresse 3: die aelteren ruhenden werden geschlossen, nichts haengt
		idle = append(idle, idleConn(t, addr, ""))
		time.Sleep(20 * time.Millisecond)
	}
	if !closed(idle[0], 500*time.Millisecond) || !closed(idle[2], 500*time.Millisecond) {
		t.Errorf("aelteste ruhende Verbindungen der Adresse nicht geschlossen")
	}
	if closed(idle[5], 200*time.Millisecond) {
		t.Errorf("neueste Verbindung geschlossen")
	}
	t0 := time.Now()
	if err := get(addr); err != nil || time.Since(t0) > time.Second {
		t.Errorf("neuer Abruf trotz ruhender Verbindungen: %v nach %v", err, time.Since(t0))
	}
	for _, so := range idle {
		so.Close()
	}
}

func TestConnLimitsPerIPActive(t *testing.T) {
	addr, stop := startLimited(t, 16, 2, 10*time.Second)
	defer stop()
	var raw []net.Conn // offen ohne Anfrage (nicht ruhend): Grenze je Adresse greift, dritte wird sofort geschlossen
	for i := 0; i < 2; i++ {
		so, err := net.Dial("tcp", addr)
		if err != nil {
			t.Fatal(err)
		}
		raw = append(raw, so)
	}
	time.Sleep(100 * time.Millisecond)
	so, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	if !closed(so, time.Second) {
		t.Errorf("dritte Verbindung derselben Adresse nicht geschlossen")
	}
	so.Close()
	for _, s := range raw {
		s.Close()
	}
	time.Sleep(100 * time.Millisecond)
	if err := get(addr); err != nil {
		t.Errorf("nach dem Schliessen wieder Platz: %v", err)
	}
}

func TestConnIdleUnauthShorter(t *testing.T) {
	addr, stop := startLimited(t, 16, 8, 300*time.Millisecond)
	defer stop()
	anon := idleConn(t, addr, "")
	fake := idleConn(t, addr, "Cookie: x=1\r\nAuthorization: Bearer x\r\nX-Saga-Media-Token: x\r\n")
	authd := idleConn(t, addr, "Cookie: s=gueltig\r\n")
	if !closed(anon, 1500*time.Millisecond) {
		t.Errorf("Leerlauf ohne Anmeldedaten nicht beendet")
	}
	if !closed(fake, 500*time.Millisecond) {
		t.Errorf("Leerlauf mit ungeprueften Anmeldekoepfen verlaengert (S1b)")
	}
	fake.Close()
	if closed(authd, 200*time.Millisecond) {
		t.Errorf("Leerlauf mit Anmeldedaten zu frueh beendet")
	}
	anon.Close()
	authd.Close()
}

func TestIPBucket(t *testing.T) {
	for in, want := range map[string]string{"203.0.113.7": "203.0.113.7", "::ffff:203.0.113.7": "203.0.113.7",
		"2001:db8:1:2:3:4:5:6": "2001:db8:1:2::/64", "2001:db8:1:2:ffff::1": "2001:db8:1:2::/64", "2001:db8:1:3::1": "2001:db8:1:3::/64"} {
		if got := ipBucket(hostOf(in)); got != want {
			t.Errorf("ipBucket(%s) = %s, erwartet %s", in, got, want)
		}
	}
}
