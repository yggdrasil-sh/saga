package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"net/url"
	"strings"
	"testing"
	"time"
)

// F1 (Pruefung 2.5): das fremde Kaskaden-Token geht in eine Kopfzeile der Waehlanfrage. Mit CR/LF liesse ein
// boesartiges Heim zusaetzliche Kopfzeilen beim angewaehlten Knoten einschleusen - abgelehnt, bevor gewaehlt wird.
func TestKaskadeTokenKopfzeile(t *testing.T) {
	hits := make(chan http.Header, 4)
	ts := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits <- r.Header.Clone()
		w.WriteHeader(403)
	}))
	defer ts.Close()
	s := testNodeServer(t, false)
	if s.nodeKey == nil {
		_, k, _ := ed25519.GenerateKey(rand.Reader)
		s.nodeKey = k
	}
	ip := strings.TrimPrefix(ts.URL, "https://")
	host, _, _ := net.SplitHostPort(ip)
	for _, tok := range []string{"c1.aaaa.bbbb\r\nX-Eingeschleust: ja", "c1.aaaa.bbbb\nX-Eingeschleust: ja", "c1.aa aa.bbbb",
		"c1.aaaa.bb\tbb", "c1.aaaa.bbbb.cccc", "c1.aaaa.", "c1..bbbb", "c2.aaaa.bbbb", "c1.aa+a.bb/b", "c1.aaaa.bbbb=",
		"c1." + strings.Repeat("a", ctokMaxLen) + ".b"} {
		_, err := s.dialBridge(context.Background(), cascadeCmd{Bridge: "bridge-abcdef12", URL: ts.URL, Peer: "x",
			Token: tok, Room: "room-abcdef12", IPs: []string{host}})
		if err == nil || !strings.Contains(err.Error(), "token") {
			t.Errorf("%q: %v", tok, err)
		}
		if err := s.cascade(cascadeCmd{Bridge: "bridge-abcdef12", URL: ts.URL, Token: tok, Room: "room-abcdef12"}); err == nil {
			t.Errorf("cascade %q angenommen", tok)
		}
	}
	select {
	case h := <-hits:
		t.Fatalf("trotz ungueltigem Token gewaehlt (X-Eingeschleust=%q)", h.Get("X-Eingeschleust"))
	case <-time.After(200 * time.Millisecond):
	}
	// gueltige Form: wird gewaehlt (Ziel lehnt ab), keine fremde Kopfzeile
	_, err := s.dialBridge(context.Background(), cascadeCmd{Bridge: "bridge-abcdef12", URL: ts.URL, Peer: "x",
		Token: "c1.aaaa-_09.bbbb", Room: "room-abcdef12", IPs: []string{host}})
	if err == nil {
		t.Fatal("403 als Erfolg")
	}
	h := <-hits
	if h.Get("X-Bifrost-Token") != "c1.aaaa-_09.bbbb" || h.Get("X-Eingeschleust") != "" {
		t.Fatalf("kopfzeilen: %v", h)
	}
	if !peerTokenOK(true, "raum-aaaa") || peerTokenOK(true, "raum\r\nx: y") {
		t.Fatal("entwicklungsmodus: raumkennung")
	}
}

// F2 (Pruefung 2.5): der Knoten waehlt genau die von Saga gepruefte Adresse (kein erneutes DNS, Rebinding), der Name
// gilt nur fuer SNI/Host. Ohne mitgegebene Adressen loest er selbst auf und nimmt nur oeffentliche.
func TestKaskadeWaehlziel(t *testing.T) {
	hosts := make(chan string, 4)
	ts := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hosts <- r.Host
		w.WriteHeader(403)
	}))
	defer ts.Close()
	s := testNodeServer(t, false)
	if s.nodeKey == nil {
		_, k, _ := ed25519.GenerateKey(rand.Reader)
		s.nodeKey = k
	}
	_, port, _ := net.SplitHostPort(strings.TrimPrefix(ts.URL, "https://"))
	// Name loest nicht (bzw. anders) auf - gewaehlt wird die mitgegebene Adresse, Host bleibt der Name
	name := "https://knoten.invalid:" + port
	if _, err := s.dialBridge(context.Background(), cascadeCmd{Bridge: "bridge-abcdef12", URL: name, Token: "c1.aa.bb",
		Room: "room-abcdef12", IPs: []string{"::ffff:127.0.0.1"}}); err == nil {
		t.Fatal("403 als Erfolg")
	}
	select {
	case h := <-hosts:
		if h != "knoten.invalid:"+port {
			t.Fatalf("host %q", h)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("mitgegebene adresse nicht gewaehlt")
	}
	// ohne Adressen: interne Ziele (auch gemappt) abgelehnt, ohne zu waehlen
	for _, u := range []string{ts.URL, "https://[::ffff:127.0.0.1]:" + port, "https://[::1]:" + port, "https://10.0.0.1:" + port} {
		pu, _ := url.Parse(u)
		if _, err := s.bridgeTargets(context.Background(), cascadeCmd{}, pu); err == nil {
			t.Errorf("%s: internes ziel ohne pruefung", u)
		}
	}
	// eigener Knoten derselben Saga (auch im LAN): Name aufloesen, keine Sperre
	own, _ := url.Parse(ts.URL)
	if tg, err := s.bridgeTargets(context.Background(), cascadeCmd{Own: true}, own); err != nil || len(tg) != 1 || tg[0] != own.Host {
		t.Errorf("eigener knoten: %v %v", tg, err)
	}
	for _, bad := range [][]string{{"x"}, {"fe80::1%eth0"}, {"0.0.0.0"}, {"224.0.0.1"}} {
		pu, _ := url.Parse(name)
		if _, err := s.bridgeTargets(context.Background(), cascadeCmd{IPs: bad}, pu); err == nil {
			t.Errorf("ips %v angenommen", bad)
		}
	}
	select {
	case h := <-hosts:
		t.Fatalf("unerwartet gewaehlt: %s", h)
	default:
	}
	for a, want := range map[string]bool{"8.8.8.8": true, "2a00:1450::1": true, "::ffff:8.8.8.8": true, "127.0.0.1": false,
		"::ffff:127.0.0.1": false, "::ffff:10.1.2.3": false, "10.1.2.3": false, "172.16.0.1": false, "192.168.0.1": false,
		"100.64.0.1": false, "169.254.169.254": false, "fd00::1": false, "fe80::1": false, "::1": false, "64:ff9b::a01:203": false,
		"64:ff9b::808:808": true, "2002:a00:1::1": false, "0.1.2.3": false} {
		if got := publicAddr(netip.MustParseAddr(a)); got != want {
			t.Errorf("publicAddr(%s)=%v", a, got)
		}
	}
}
