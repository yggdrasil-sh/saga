package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
)

// Kopplung "Erweitern" (Konzept v2 §2.3, Paket P6). Ein weiterer Knoten bekommt im Deploy-Skript
//
//	HORN_SAGA=https://saga.example.org  HORN_PAIR=<einmaliger Kopplungs-Code>  HORN_NAME=horn-2.example.org
//
// Erster Start: Knotenschluessel erzeugen (HORN_DATA/node.key), eigene Adressen ueber GET /api/horn/whoami getrennt
// per IPv4 und IPv6 ermitteln, UDP-Selbsttest, dann POST /api/horn/pair {token, name, node_pub, sig, v4, v6, port, udp,
// version, cores, link_mbit}. sig = Ed25519(node_sk, "horn-pair/1\n" + token + "\n" + name) beweist den Schluessel.
// Saga prueft den Code (einmalig, 30 min, nur als SHA-256 gespeichert), prueft die Erreichbarkeit zurueck (443/TCP ueber
// v4 und v6 mit GET /api/horn/echo, UDP-Binding) und antwortet {node_id, saga_pub, ctl_url, name}. Das legt der Knoten
// in HORN_DATA/pair.json ab; danach ist der Code wertlos. Bei jedem weiteren Start verbindet er sich direkt.
// saga_pub bleibt angeheftet: Ein welcome mit anderem Schluessel lehnt der Knoten ab.

type pairState struct {
	NodeID  string `json:"node_id"`
	SagaPub string `json:"saga_pub"`
	CtlURL  string `json:"ctl_url"`
	Name    string `json:"name"`
	Saga    string `json:"saga"`
	At      string `json:"at"`
}

func pairFile(dir string) string { return filepath.Join(dir, "pair.json") }

func loadPair(dir string) *pairState {
	b, err := os.ReadFile(pairFile(dir))
	if err != nil {
		return nil
	}
	var p pairState
	if json.Unmarshal(b, &p) != nil || p.NodeID == "" || p.CtlURL == "" {
		return nil
	}
	return &p
}

func savePair(dir string, p *pairState) error {
	b, _ := json.MarshalIndent(p, "", " ")
	tmp := pairFile(dir) + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, pairFile(dir))
}

// sagaBase prueft HORN_SAGA: https, http nur zu localhost (Tests) bzw. mit HORN_INSECURE_SAGA=1.
func sagaBase(s string) (string, error) {
	s = strings.TrimRight(strings.TrimSpace(s), "/")
	u, err := url.Parse(s)
	if err != nil || u.Host == "" || (u.Scheme != "https" && u.Scheme != "http") || u.Path != "" {
		return "", errors.New("HORN_SAGA: https://saga.example.org erwartet")
	}
	if u.Scheme == "http" && !loopbackHost(u.Hostname()) && os.Getenv("HORN_INSECURE_SAGA") != "1" {
		return "", errors.New("HORN_SAGA: nur https (http nur fuer localhost)")
	}
	return s, nil
}

func loopbackHost(h string) bool {
	if h == "localhost" {
		return true
	}
	ip := net.ParseIP(h)
	return ip != nil && ip.IsLoopback()
}

// errPairFinal: Saga lehnt endgueltig ab (Code ungueltig, abgelaufen, falscher Name) - kein Neuversuch.
type errPairFinal struct{ msg string }

func (e errPairFinal) Error() string { return e.msg }

// startExternal: gekoppelter (oder zu koppelnder) Knoten ohne saga-media. -> true, wenn eine Steuerung laeuft bzw. folgt.
func (s *server) startExternal(base string) bool {
	if p := loadPair(s.cfg.Data); p != nil && p.Saga == base {
		pub, err := base64.StdEncoding.DecodeString(p.SagaPub)
		if err != nil || len(pub) != ed25519.PublicKeySize {
			log.Printf("kopplung: %s unlesbar - Knoten im Admin-Bereich entfernen und neu koppeln", pairFile(s.cfg.Data))
			return false
		}
		s.verifier.SetKey(pub)
		s.startCtl(p.CtlURL, "", pub)
		log.Printf("kopplung: gekoppelt mit %s als %s (knoten %s)", base, p.Name, p.NodeID)
		return true
	}
	tok := os.Getenv("HORN_PAIR")
	_ = os.Unsetenv("HORN_PAIR")
	if tok == "" {
		log.Printf("kopplung: nicht gekoppelt - HORN_PAIR (Kopplungs-Code aus dem Admin-Bereich, Erweitern) fehlt")
		return false
	}
	go s.pairLoop(base, tok)
	return true
}

func (s *server) pairLoop(base, tok string) {
	time.Sleep(300 * time.Millisecond) // Weiche laeuft, UDP-Selbsttest fertig
	back := 5 * time.Second
	for !s.closing.Load() {
		p, err := s.pair(base, tok)
		if err == nil {
			if err := savePair(s.cfg.Data, p); err != nil {
				log.Printf("kopplung: %s: %v", pairFile(s.cfg.Data), err)
			}
			pub, _ := base64.StdEncoding.DecodeString(p.SagaPub)
			s.verifier.SetKey(pub)
			log.Printf("kopplung: gekoppelt als %s (knoten %s) - der Kopplungs-Code ist jetzt wertlos", p.Name, p.NodeID)
			s.startCtl(p.CtlURL, "", pub)
			return
		}
		var fe errPairFinal
		if errors.As(err, &fe) {
			log.Printf("kopplung: abgelehnt: %v - im Admin-Bereich (Anrufe, Erweitern) einen neuen Code erzeugen", err)
			s.pairErr.Store(&fe.msg)
			return
		}
		log.Printf("kopplung: %v - neuer Versuch in %v", err, back)
		time.Sleep(back)
		if back *= 2; back > 5*time.Minute {
			back = 5 * time.Minute
		}
	}
}

// whoami fragt Saga nach der eigenen Adresse ueber eine Adressfamilie (tcp4/tcp6).
func whoami(base, network string) string {
	d := &net.Dialer{Timeout: 5 * time.Second}
	tr := &http.Transport{DialContext: func(ctx context.Context, _, addr string) (net.Conn, error) { return d.DialContext(ctx, network, addr) },
		TLSHandshakeTimeout: 5 * time.Second, Proxy: nil}
	cl := &http.Client{Transport: tr, Timeout: 8 * time.Second}
	defer tr.CloseIdleConnections()
	req, _ := http.NewRequest(http.MethodGet, base+"/api/horn/whoami", nil)
	req.Header.Set("X-Saga", "1")
	r, err := cl.Do(req)
	if err != nil {
		return ""
	}
	defer r.Body.Close()
	var out struct {
		IP string `json:"ip"`
	}
	if r.StatusCode != 200 || json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&out) != nil {
		return ""
	}
	ip := net.ParseIP(out.IP)
	if ip == nil || (network == "tcp4") != (ip.To4() != nil) {
		return ""
	}
	return ip.String()
}

func (s *server) publicPort() int {
	if p, err := strconv.Atoi(os.Getenv("HORN_PUBLIC_PORT")); err == nil && p > 0 && p < 65536 {
		return p
	}
	if u, err := url.Parse(s.publicURL()); err == nil && u.Port() != "" {
		p, _ := strconv.Atoi(u.Port())
		return p
	}
	return 443
}

func (s *server) pair(base, tok string) (*pairState, error) {
	v4, v6, dropped := s.publicAddrs(base)
	if v4 == "" && v6 == "" {
		if len(dropped) > 0 {
			return nil, fmt.Errorf("Saga sieht nur interne Adressen %v (Knoten erreicht Saga ueber ein VPN?) - "+
				"oeffentliche Adresse in der .env als HORN_PUBLIC_IP=... eintragen und neu starten", dropped)
		}
		return nil, errors.New("saga nicht erreichbar (whoami)")
	}
	if len(dropped) > 0 {
		log.Printf("kopplung: interne Adresse %v verworfen (VPN?)", dropped)
	}
	link, _ := strconv.Atoi(os.Getenv("HORN_LINK_MBIT"))
	pub := s.nodeKey.Public().(ed25519.PublicKey)
	body := map[string]any{"token": tok, "name": s.cfg.Name, "node_pub": base64.StdEncoding.EncodeToString(pub),
		"sig": base64.StdEncoding.EncodeToString(ed25519.Sign(s.nodeKey, []byte("horn-pair/1\n"+tok+"\n"+s.cfg.Name))),
		"v4":  v4, "v6": v6, "port": s.publicPort(), "udp": s.udp != nil, "udp_check": s.UDPCheck().State,
		"version": version, "cores": runtime.GOMAXPROCS(0), "link_mbit": link}
	b, _ := json.Marshal(body)
	req, _ := http.NewRequest(http.MethodPost, base+"/api/horn/pair", bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Saga", "1")
	cl := &http.Client{Timeout: 60 * time.Second, Transport: &http.Transport{Proxy: nil, TLSHandshakeTimeout: 10 * time.Second}}
	r, err := cl.Do(req)
	if err != nil {
		return nil, err
	}
	defer r.Body.Close()
	var out struct {
		NodeID  string `json:"node_id"`
		SagaPub string `json:"saga_pub"`
		CtlURL  string `json:"ctl_url"`
		Name    string `json:"name"`
		Error   string `json:"error"`
		Code    string `json:"code"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 64<<10)).Decode(&out)
	switch {
	case r.StatusCode == 200:
	case r.StatusCode == 403 || r.StatusCode == 410 || r.StatusCode == 409:
		return nil, errPairFinal{fmt.Sprintf("%s (%s)", out.Error, out.Code)}
	default:
		return nil, fmt.Errorf("saga: http %d %s %s", r.StatusCode, out.Code, out.Error)
	}
	pb, err := base64.StdEncoding.DecodeString(out.SagaPub)
	if err != nil || len(pb) != ed25519.PublicKeySize || out.NodeID == "" {
		return nil, errors.New("antwort unvollstaendig")
	}
	cu, err := url.Parse(out.CtlURL)
	bu, _ := url.Parse(base)
	if err != nil || (cu.Scheme != "wss" && cu.Scheme != "ws") || cu.Host != bu.Host || (cu.Scheme == "ws" && bu.Scheme != "http") {
		return nil, errors.New("ctl_url passt nicht zu HORN_SAGA")
	}
	return &pairState{NodeID: out.NodeID, SagaPub: out.SagaPub, CtlURL: out.CtlURL, Name: out.Name, Saga: base,
		At: time.Now().UTC().Format(time.RFC3339)}, nil
}

// apiEcho: Rueckpruefung von Saga (Kopplung, "Port pruefen"): beweist, dass unter dieser Adresse DIESER Knoten
// lauscht. sig = Ed25519(node_sk, "horn-echo/1\n" + n + "\n" + name). Hoechstens 20 je Sekunde.
func (s *server) apiEcho(w http.ResponseWriter, r *http.Request) {
	n := r.URL.Query().Get("n")
	if len(n) < 16 || len(n) > 64 || !validRef(n) {
		writeJSON(w, 400, map[string]string{"error": "n"})
		return
	}
	if !s.echoRate.allow() {
		writeJSON(w, 429, map[string]string{"error": "rate"})
		return
	}
	writeJSON(w, 200, map[string]any{"name": s.cfg.Name, "version": version,
		"sig": base64.StdEncoding.EncodeToString(ed25519.Sign(s.nodeKey, []byte("horn-echo/1\n"+n+"\n"+s.cfg.Name)))})
}

// rateGate: einfache Grenze je Sekunde (ohne Allokation).
type rateGate struct {
	sec atomic.Int64
	n   atomic.Int64
	max int64
}

func (g *rateGate) allow() bool {
	now := time.Now().Unix()
	if g.sec.Swap(now) != now {
		g.n.Store(0)
	}
	return g.n.Add(1) <= g.max
}
