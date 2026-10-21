package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

func signTok(sk ed25519.PrivateKey, b map[string]any) string {
	raw, _ := json.Marshal(b)
	p := base64.RawURLEncoding.EncodeToString(raw)
	return tokenPrefix + p + "." + base64.RawURLEncoding.EncodeToString(ed25519.Sign(sk, []byte(tokenDomain+p)))
}

func tokBody(over map[string]any) map[string]any {
	b := map[string]any{"v": 1, "node": "horn-0.example.org", "call_ref": "CallRef0123456789", "peer_ref": "PeerRef0123456789",
		"leg_ref": "LegRef0123456789a", "role": "video", "pub": []string{"a", "v"}, "max_kbit": 4000, "udp_ok": false,
		"dev_class": "mobile", "exp": time.Now().Add(60 * time.Second).Unix(), "jti": "jti-0123456789ab"}
	for k, v := range over {
		b[k] = v
	}
	return b
}

func TestSagaVerifier(t *testing.T) {
	pub, sk, _ := ed25519.GenerateKey(rand.Reader)
	_, other, _ := ed25519.GenerateKey(rand.Reader)
	v := &sagaVerifier{}
	if _, err := v.Verify(signTok(sk, tokBody(nil))); !errors.Is(err, sfu.ErrNoVerifier) {
		t.Fatalf("ohne schluessel: %v", err)
	}
	v.SetKey(pub)
	g, err := v.Verify(signTok(sk, tokBody(nil)))
	if err != nil || g.Room != "CallRef0123456789" || g.Peer != "PeerRef0123456789" || g.Leg != "LegRef0123456789a" ||
		g.Role != sfu.RoleVideo || g.Pub != sfu.PubAudio|sfu.PubVideo || g.Device != sfu.Mobile || g.MaxKbit != 4000 || g.ID != "jti-0123456789ab" {
		t.Fatalf("gueltig: %+v %v", g, err)
	}
	bad := map[string]string{
		"fremder schluessel": signTok(other, tokBody(nil)),
		"zu lange gueltig":   signTok(sk, tokBody(map[string]any{"exp": time.Now().Add(time.Hour).Unix()})),
		"ref zu kurz":        signTok(sk, tokBody(map[string]any{"call_ref": "x"})),
		"ref mit zeichen":    signTok(sk, tokBody(map[string]any{"leg_ref": "a/b/c/d/e/f"})),
		"rolle":              signTok(sk, tokBody(map[string]any{"role": "admin"})),
		"geraeteklasse":      signTok(sk, tokBody(map[string]any{"dev_class": "server"})),
		"version":            signTok(sk, tokBody(map[string]any{"v": 2})),
		"ohne knoten":        signTok(sk, tokBody(map[string]any{"node": ""})),
		"ohne praefix":       signTok(sk, tokBody(nil))[3:],
		"leer":               "",
	}
	for name, tok := range bad {
		if _, err := v.Verify(tok); err == nil {
			t.Errorf("%s: angenommen", name)
		}
	}
	// geaenderte Nutzlast mit alter Signatur
	tok := signTok(sk, tokBody(nil))
	raw, _ := json.Marshal(tokBody(map[string]any{"role": "both"}))
	forged := tokenPrefix + base64.RawURLEncoding.EncodeToString(raw) + tok[len(tok)-87:]
	if _, err := v.Verify(forged); err == nil {
		t.Error("gefaelschte nutzlast angenommen")
	}
	// Ablauf, Knoten: Node.Admit
	n := sfu.NewNode(sfu.Config{Name: "horn-0.example.org", Verifier: v})
	defer n.Close()
	if _, err := n.CreateRoom("CallRef0123456789", nil); err != nil {
		t.Fatal(err)
	}
	if _, _, err := n.Admit(signTok(sk, tokBody(map[string]any{"exp": time.Now().Add(-time.Second).Unix()})), sfu.PathTCP); !errors.Is(err, sfu.ErrExpired) {
		t.Errorf("abgelaufen: %v", err)
	}
	if _, _, err := n.Admit(signTok(sk, tokBody(map[string]any{"node": "horn-9.example.org"})), sfu.PathTCP); !errors.Is(err, sfu.ErrWrongNode) {
		t.Errorf("anderer knoten: %v", err)
	}
	if _, _, err := n.Admit(signTok(sk, tokBody(nil)), sfu.PathUDP); !errors.Is(err, sfu.ErrNoUDP) {
		t.Errorf("udp ohne udp_ok: %v", err)
	}
	if _, _, err := n.Admit(signTok(sk, tokBody(nil)), sfu.PathTCP); err != nil {
		t.Errorf("gueltig: %v", err)
	}
}

func TestUsedTokens(t *testing.T) {
	var u usedTokens
	exp := time.Now().Add(time.Minute)
	if !u.use("a", "leg", exp) || u.use("a", "leg", exp) {
		t.Fatal("zweite benutzung angenommen")
	}
	if !u.use("a", "turn", exp) {
		t.Fatal("anderer zweck abgelehnt")
	}
	if !u.use("", "leg", exp) || !u.use("", "leg", exp) {
		t.Fatal("entwicklungs-token ohne kennung")
	}
}

func BenchmarkSagaVerifier(b *testing.B) {
	pub, sk, _ := ed25519.GenerateKey(rand.Reader)
	v := &sagaVerifier{}
	v.SetKey(pub)
	tok := signTok(sk, tokBody(nil))
	for i := 0; i < b.N; i++ {
		if _, err := v.Verify(tok); err != nil {
			b.Fatal(err)
		}
	}
}
