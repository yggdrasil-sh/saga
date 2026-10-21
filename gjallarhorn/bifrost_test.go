package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

func startBotsAs(t *testing.T, base, room, prefix string, paths []string, tiles int, layers ...string) sfuBots {
	t.Helper()
	hc := httpClient()
	if err := postJSON(hc, base+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	var bs sfuBots
	for i, p := range paths {
		api, err := sfuBotAPI(p)
		if err != nil {
			t.Fatal(err)
		}
		b := &sfuBot{peer: fmt.Sprintf("%s%d", prefix, i), path: p, video: true, layers: layers}
		b.level.Store(127)
		if err := b.join(hc, base, api, room, tiles, "desktop"); err != nil {
			bs.close()
			t.Fatal(err)
		}
		bs = append(bs, b)
	}
	t.Cleanup(bs.close)
	for i := 0; i < 100; i++ {
		c, want := 0, 0
		for _, b := range bs {
			c += b.connected()
			want += len(b.pcs)
		}
		if c == want {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	for _, b := range bs {
		if b.connected() != len(b.pcs) {
			t.Fatalf("%s (%s) nicht verbunden", b.peer, b.path)
		}
		b.start()
	}
	return bs
}

// TestKaskade: zwei Knoten, je zwei Bots in "ihrem" Raum, Bruecke A -> B. Jeder hoert und sieht alle drei anderen
// (Ton-Top-3 von sich aus, Bild auf Nachfrage), niemand bekommt sich selbst, keine Luecken in den Folgenummern.
// Dann Bruecke zu: die Teilnehmenden des anderen Knotens verschwinden aus dem Raum.
func TestKaskade(t *testing.T) {
	a := testNodeServer(t, true)
	b := testNodeServer(t, true)
	ba, bb := "https://"+a.mux.Addr().String(), "https://"+b.mux.Addr().String()
	ra := startBotsAs(t, ba, "raum-aaaa", "anna", []string{"tcp", "udp"}, 4, "l", "m")
	rb := startBotsAs(t, bb, "raum-bbbb", "bert", []string{"tcp", "tcp"}, 4, "l", "m")
	ra[0].level.Store(20)
	rb[1].level.Store(30)
	if err := postJSON(httpClient(), ba+"/api/dev/cascade", map[string]string{"bridge": "brueckentest1", "url": bb, "room": "raum-aaaa",
		"peerroom": "raum-bbbb"}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	all := append(append(sfuBots{}, ra...), rb...)
	var err error
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) {
		err = nil
		ida, idb := roomCSRCs(t, ba, "raum-aaaa"), roomCSRCs(t, bb, "raum-bbbb")
		if len(ida) != 4 || len(idb) != 4 {
			err = fmt.Errorf("teilnehmende a=%v b=%v", ida, idb)
		}
		for _, x := range all {
			if err != nil {
				break
			}
			ids := ida
			if strings.HasPrefix(x.peer, "bert") {
				ids = idb
			}
			au, vi := x.seen()
			for _, o := range all {
				if o == x {
					continue
				}
				c := ids[o.peer]
				if au[c] < 20 {
					err = fmt.Errorf("%s hoert %s nicht (%d)", x.peer, o.peer, au[c])
					break
				}
				if vi[c] < 20 {
					err = fmt.Errorf("%s sieht %s nicht (%d)", x.peer, o.peer, vi[c])
					break
				}
			}
			if au[ids[x.peer]] != 0 || vi[ids[x.peer]] != 0 {
				err = fmt.Errorf("%s bekommt sich selbst", x.peer)
			}
		}
		if err == nil {
			break
		}
		time.Sleep(250 * time.Millisecond)
	}
	if err != nil {
		t.Fatal(err)
	}
	var lost uint64
	for _, x := range all {
		lost += x.rxLost.Load()
	}
	if lost > 10 {
		t.Fatalf("luecken in folgenummern: %d", lost)
	}
	st := a.bridgeStats()
	if len(st) != 1 {
		t.Fatalf("bruecken %v", st)
	}
	// Bruecke zu: die Gaeste verschwinden
	a.uncascade("brueckentest1")
	deadline = time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if len(roomCSRCs(t, ba, "raum-aaaa")) == 2 && len(roomCSRCs(t, bb, "raum-bbbb")) == 2 {
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("nach dem Trennen: a=%v b=%v", roomCSRCs(t, ba, "raum-aaaa"), roomCSRCs(t, bb, "raum-bbbb"))
}

// TestKaskadenToken: Kaskaden-Token pruefen (Signatur, Rolle, Ablauf, Lebensdauer, Form).
func TestKaskadenToken(t *testing.T) {
	pub, sk, _ := ed25519.GenerateKey(rand.Reader)
	ppub, _, _ := ed25519.GenerateKey(rand.Reader)
	v := &sagaVerifier{}
	v.SetKey(pub)
	mk := func(b ctokBody, key ed25519.PrivateKey) string {
		raw, _ := json.Marshal(b)
		p := base64.RawURLEncoding.EncodeToString(raw)
		return ctokPrefix + p + "." + base64.RawURLEncoding.EncodeToString(ed25519.Sign(key, []byte(ctokDomain+p)))
	}
	ok := ctokBody{V: 1, Bridge: "brueckeeins1", Node: "horn-1.example.org", Room: "raumraumraum", Role: "accept", PeerNode: "horn-2.example.org",
		PeerPub: base64.StdEncoding.EncodeToString(ppub), Exp: time.Now().Add(60 * time.Second).Unix(), JTI: "jtijtijtijti"}
	if _, err := v.verifyCascade(mk(ok, sk)); err != nil {
		t.Fatal(err)
	}
	_, other, _ := ed25519.GenerateKey(rand.Reader)
	bad := []ctokBody{ok, ok, ok, ok, ok}
	bad[1].Role = "x"
	bad[2].Exp = time.Now().Add(-time.Second).Unix()
	bad[3].Exp = time.Now().Add(10 * time.Minute).Unix()
	bad[4].PeerPub = "kurz"
	for i, b := range bad {
		key := sk
		if i == 0 {
			key = other
		}
		if _, err := v.verifyCascade(mk(b, key)); err == nil {
			t.Fatalf("fall %d angenommen", i)
		}
	}
}

// TestKontingentTor: ueber dem Kontingent wird Bild bis zum naechsten Schluesselbild verworfen, Ton nie.
func TestKontingentTor(t *testing.T) {
	s := testNodeServer(t, true)
	q, err := s.node.Quota("kontingent1", 1, 100, true)
	if err != nil || q == nil {
		t.Fatal(err)
	}
	if _, err := s.node.Quota("kontingent1", 1, 100, true); err != nil {
		t.Fatal("ohne Teilnehmende darf einer dazu")
	}
	src := &fwd.Source{Kind: fwd.Video, Layer: "l"}
	src.Gate = func(n int) bool { return false }
	pkt := []byte{0x80, 96, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0x10, 0}
	src.Inject(pkt)
	if src.Gated.Load() != 1 {
		t.Fatalf("nicht verworfen: %d", src.Gated.Load())
	}
	n := s.node.QuotaOff("kontingent1", time.Second)
	if n != 0 {
		t.Fatalf("entfernt %d", n)
	}
	if _, err := s.node.Quota("kontingent1", 5, 100, true); err == nil {
		t.Fatal("notaus wirkt nicht")
	}
}

// TestKaskadeEcht: ohne Entwicklungsmodus - beide Knoten pruefen ein Saga-Token, die Unterschriften gelten nur fuer die
// eigene TLS-Verbindung (EKM); falscher Knotenschluessel, falsches Token und Wiederholung werden abgewiesen.
func TestKaskadeEcht(t *testing.T) {
	pub, sk, _ := ed25519.GenerateKey(rand.Reader)
	a := testNodeServer(t, false)
	b := testNodeServer(t, false)
	a.verifier.SetKey(pub)
	b.verifier.SetKey(pub)
	b.cfg.Name = "horn-b.example.org"
	a.cfg.Name = "horn-a.example.org"
	pa := base64.StdEncoding.EncodeToString(a.nodeKey.Public().(ed25519.PublicKey))
	pb := base64.StdEncoding.EncodeToString(b.nodeKey.Public().(ed25519.PublicKey))
	mk := func(c ctokBody) string {
		c.V, c.Exp, c.JTI = 1, time.Now().Add(60*time.Second).Unix(), randHex(8)
		raw, _ := json.Marshal(c)
		p := base64.RawURLEncoding.EncodeToString(raw)
		return ctokPrefix + p + "." + base64.RawURLEncoding.EncodeToString(ed25519.Sign(sk, []byte(ctokDomain+p)))
	}
	cmd := func(bridge, peerPub string) cascadeCmd {
		return cascadeCmd{Bridge: bridge, URL: "https://" + b.mux.Addr().String(), Peer: b.cfg.Name, PeerPub: peerPub, IPs: []string{"127.0.0.1"},
			Token: mk(ctokBody{Bridge: bridge, Node: b.cfg.Name, Room: "raum-bbbb", Role: "accept", PeerNode: a.cfg.Name, PeerPub: pa}),
			Mine:  mk(ctokBody{Bridge: bridge, Node: a.cfg.Name, Room: "raum-aaaa", Role: "dial", PeerNode: b.cfg.Name, PeerPub: peerPub})}
	}
	state := func(s *server, id string) string { return s.bridgeStates()[id].St }
	wait := func(f func() bool) bool {
		for i := 0; i < 80; i++ {
			if f() {
				return true
			}
			time.Sleep(100 * time.Millisecond)
		}
		return false
	}
	if err := a.cascade(cmd("bruecke-echt1", pb)); err != nil {
		t.Fatal(err)
	}
	if !wait(func() bool { return state(a, "bruecke-echt1") == "up" && state(b, "bruecke-echt1") == "up" }) {
		t.Fatalf("nicht verbunden: %v / %v", a.bridgeStates(), b.bridgeStates())
	}
	// falscher Schluessel der Gegenseite: der waehlende Knoten lehnt die Antwort ab
	_, wrong, _ := ed25519.GenerateKey(rand.Reader)
	wp := base64.StdEncoding.EncodeToString(wrong.Public().(ed25519.PublicKey))
	if err := a.cascade(cmd("bruecke-echt2", wp)); err != nil {
		t.Fatal(err)
	}
	time.Sleep(1500 * time.Millisecond)
	if st := a.bridgeStates()["bruecke-echt2"]; st.St == "up" || !(strings.Contains(st.Why, "unterschrift") || strings.Contains(st.Why, "schon benutzt")) {
		t.Fatalf("falscher schluessel: %+v", st)
	}
	a.uncascade("bruecke-echt2")
	// eigenes Token einer anderen Saga: abgelehnt
	c3 := cmd("bruecke-echt3", pb)
	_, sk2, _ := ed25519.GenerateKey(rand.Reader)
	sk = sk2
	c3.Mine = mk(ctokBody{Bridge: "bruecke-echt3", Node: a.cfg.Name, Room: "raum-aaaa", Role: "dial", PeerNode: b.cfg.Name, PeerPub: pb})
	if err := a.cascade(c3); err == nil {
		t.Fatal("fremdes eigenes Token angenommen")
	}
	// Token fuer die Gegenseite von einer anderen Saga (sk2): B lehnt ab, A meldet den Grund
	b.verifier.SetKey(pub)
	a.verifier.SetKey(sk2.Public().(ed25519.PublicKey))
	if err := a.cascade(cmd("bruecke-echt4", pb)); err != nil {
		t.Fatal(err)
	}
	time.Sleep(1500 * time.Millisecond)
	if st := a.bridgeStates()["bruecke-echt4"]; st.St == "up" || !strings.Contains(st.Why, "401") {
		t.Fatalf("fremdes token: %+v", st)
	}
	a.uncascade("bruecke-echt4")
	if state(a, "bruecke-echt1") != "up" {
		t.Fatal("erste bruecke weg")
	}
}
