package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"runtime/pprof"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/logging"
	"github.com/pion/turn/v5"
	"github.com/pion/webrtc/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
)

// config sind die Einstellungen aus der Umgebung.
type config struct {
	Name      string
	Listen    string
	UDP       bool
	UDPListen string
	Data      string
	Proxy     netx.ProxyMode
	PublicIPs []string
	ICEIPs    []string // nur diese lokalen Adressen als Kandidaten (leer = alle)
	RelayIP   net.IP
	MaxLegs   int
	Bench     bool
	Certs     netx.CertOptions
}

func env(k, def string) string {
	if v := strings.TrimSpace(os.Getenv(k)); v != "" {
		return v
	}
	return def
}

func splitList(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func loadConfig(bench bool) (*config, error) {
	c := &config{
		Name:   env("HORN_NAME", "localhost"),
		Listen: env("HORN_LISTEN", ":443"),
		UDP:    env("HORN_UDP", "1") != "0",
		Data:   env("HORN_DATA", "/data"),
		Bench:  bench,
	}
	c.UDPListen = env("HORN_UDP_LISTEN", c.Listen)
	pm, err := netx.ParseProxyMode(env("HORN_PROXY_PROTOCOL", "0"))
	if err != nil {
		return nil, err
	}
	c.Proxy = pm
	c.PublicIPs = splitList(os.Getenv("HORN_PUBLIC_IP"))
	c.ICEIPs = splitList(os.Getenv("HORN_ICE_IPS"))
	c.RelayIP = net.ParseIP(env("HORN_RELAY_IP", "127.0.0.1"))
	if c.RelayIP == nil {
		return nil, errors.New("HORN_RELAY_IP ungueltig")
	}
	c.MaxLegs, _ = strconv.Atoi(env("HORN_MAX_LEGS", "48"))
	certFile := env("HORN_TLS_CERT", filepath.Join(c.Data, "tls", "fullchain.pem"))
	keyFile := env("HORN_TLS_KEY", filepath.Join(c.Data, "tls", "privkey.pem"))
	c.Certs = netx.CertOptions{
		Name: c.Name, CertFile: certFile, KeyFile: keyFile,
		Self:      env("HORN_TLS_SELF", "0") == "1",
		ACMEDir:   filepath.Join(c.Data, "tls", "acme"),
		ACMEURL:   os.Getenv("HORN_ACME_URL"),
		ACMEEmail: os.Getenv("HORN_ACME_EMAIL"),
	}
	if bench {
		c.Certs.Self = true
		c.Certs.CertFile = ""
	}
	return c, nil
}

// server ist der Probe-Knoten.
type server struct {
	cfg   *config
	certs *netx.Certs
	mux   *netx.Mux
	udp   *netx.WebRTCPacketConn
	turn  *turn.Server
	room  *fwd.Room

	apiUDP, apiTCP, apiTURN *webrtc.API
	turnSecret              string

	mu     sync.Mutex
	legs   map[string]*leg
	ufrags map[string]*leg // lokaler ICE-ufrag -> Bein (fuer das Leeren nach Schueben)
	perIP  map[string]int  // nur Zaehler im Speicher, nie gespeichert
	start  time.Time
}

func randHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func runProbe(args []string, bench bool) error {
	cfg, err := loadConfig(bench)
	if err != nil {
		return err
	}
	if pf := os.Getenv("HORN_CPUPROFILE"); pf != "" { // Entwicklung: CPU-Profil
		f, err := os.Create(pf)
		if err == nil {
			_ = pprof.StartCPUProfile(f)
			defer func() { pprof.StopCPUProfile(); f.Close() }()
		}
	}
	s, err := newServer(cfg)
	if err != nil {
		return err
	}
	if bench {
		// Port an den Elternprozess melden
		fmt.Printf("LISTEN %d\n", s.mux.Addr().Port)
	}
	sig := make(chan os.Signal, 2)
	signal.Notify(sig, syscall.SIGHUP, syscall.SIGINT, syscall.SIGTERM)
	for x := range sig {
		if x == syscall.SIGHUP {
			if changed, err := s.certs.ReloadIfChanged(); err != nil {
				log.Printf("zertifikat: %v", err)
			} else if changed {
				log.Printf("zertifikat: neu geladen (SIGHUP)")
			}
			continue
		}
		log.Printf("beende (%v)", x)
		s.shutdown()
		return nil
	}
	return nil
}

func newServer(cfg *config) (*server, error) {
	s := &server{cfg: cfg, legs: map[string]*leg{}, ufrags: map[string]*leg{}, perIP: map[string]int{}, start: time.Now(), turnSecret: randHex(32)}
	if !cfg.Bench {
		if err := os.MkdirAll(filepath.Join(cfg.Data, "results"), 0o750); err != nil {
			return nil, fmt.Errorf("datenordner: %w", err)
		}
	}
	certs, err := netx.NewCerts(cfg.Certs)
	if err != nil {
		return nil, err
	}
	s.certs = certs
	go certs.Watch(30*time.Second, nil)
	s.mux, err = netx.Listen(cfg.Listen, certs.TLSConfig(), cfg.Proxy)
	if err != nil {
		return nil, fmt.Errorf("tcp %s: %w", cfg.Listen, err)
	}
	if cfg.UDP {
		ua := cfg.UDPListen
		if strings.HasSuffix(ua, ":0") || ua == cfg.Listen {
			ua = net.JoinHostPort(hostOf(cfg.Listen), strconv.Itoa(s.mux.Addr().Port))
		}
		uaddr, err := net.ResolveUDPAddr("udp", ua)
		if err == nil {
			var uc *net.UDPConn
			uc, err = net.ListenUDP("udp", uaddr)
			if err == nil {
				_ = uc.SetReadBuffer(4 << 20)
				_ = uc.SetWriteBuffer(4 << 20)
				s.udp = &netx.WebRTCPacketConn{UDPConn: uc}
			}
		}
		if err != nil {
			log.Printf("udp %s nicht verfuegbar (%v), nur TCP", ua, err)
		}
	}
	s.mux.OnICE = s.onICEConn
	s.mux.NoCoalesce = env("HORN_COALESCE", "1") == "0"
	s.mux.Start()
	s.room = fwd.NewRoom(250 * time.Millisecond)
	if err := s.buildAPIs(); err != nil {
		return nil, err
	}
	if err := s.startTURN(); err != nil {
		return nil, err
	}
	srv := &http.Server{Handler: s.routes(), ReadHeaderTimeout: 10 * time.Second, ErrorLog: log.New(discard{}, "", 0)}
	go func() { _ = srv.Serve(s.mux.HTTP) }()
	go s.reaper()
	udpInfo := "aus"
	if s.udp != nil {
		udpInfo = s.udp.LocalAddr().String()
	}
	log.Printf("gjallarhorn %s probe: name=%s tcp=%s udp=%s tls=%s proxy=%d", version, cfg.Name, s.mux.Addr(), udpInfo, certs.Mode, cfg.Proxy)
	return s, nil
}

type discard struct{}

func (discard) Write(p []byte) (int, error) { return len(p), nil }

func hostOf(addr string) string {
	h, _, err := net.SplitHostPort(addr)
	if err != nil {
		return ""
	}
	return h
}

func (s *server) shutdown() {
	s.mu.Lock()
	ls := make([]*leg, 0, len(s.legs))
	for _, l := range s.legs {
		ls = append(ls, l)
	}
	s.mu.Unlock()
	for _, l := range ls {
		s.closeLeg(l)
	}
	if s.turn != nil {
		_ = s.turn.Close()
	}
	_ = s.mux.Close()
	if s.udp != nil {
		_ = s.udp.Close()
	}
}

// candidateIPs ermittelt die oeffentlichen Adressen fuer Host-Kandidaten:
// HORN_PUBLIC_IP, sonst die Adressen von HORN_NAME, falls keine davon lokal ist
// (Docker, Portfreigabe). Leer = lokale Adressen unveraendert.
func (s *server) candidateIPs() []string {
	if len(s.cfg.PublicIPs) > 0 {
		return s.cfg.PublicIPs
	}
	if s.cfg.Name == "" || s.cfg.Name == "localhost" || s.cfg.Bench {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	ips, err := net.DefaultResolver.LookupIPAddr(ctx, s.cfg.Name)
	if err != nil || len(ips) == 0 {
		return nil
	}
	local := map[string]bool{}
	if addrs, err := net.InterfaceAddrs(); err == nil {
		for _, a := range addrs {
			if n, ok := a.(*net.IPNet); ok {
				local[n.IP.String()] = true
			}
		}
	}
	var out []string
	v4, v6 := false, false
	for _, ip := range ips {
		if local[ip.IP.String()] || ip.IP.IsLoopback() {
			return nil
		}
		// je Familie eine Adresse (pion bildet je Familie ab)
		if ip.IP.To4() != nil && !v4 {
			v4 = true
			out = append(out, ip.IP.String())
		} else if ip.IP.To4() == nil && !v6 {
			v6 = true
			out = append(out, ip.IP.String())
		}
	}
	return out
}

func (s *server) iceFilter(se *webrtc.SettingEngine) {
	if len(s.cfg.ICEIPs) == 0 {
		return
	}
	allow := map[string]bool{}
	loop := false
	for _, ip := range s.cfg.ICEIPs {
		allow[ip] = true
		if p := net.ParseIP(ip); p != nil && p.IsLoopback() {
			loop = true
		}
	}
	se.SetIncludeLoopbackCandidate(loop)
	se.SetIPFilter(func(ip net.IP) bool { return allow[ip.String()] })
}

func (s *server) buildAPIs() error {
	pub := s.candidateIPs()
	if len(pub) > 0 {
		log.Printf("kandidaten: oeffentliche adressen %v", pub)
	}
	lf := logging.NewDefaultLoggerFactory()
	lf.DefaultLogLevel = logging.LogLevelError
	if os.Getenv("HORN_DEBUG") == "1" {
		lf.DefaultLogLevel = logging.LogLevelWarn
	}
	base := func(se *webrtc.SettingEngine) error {
		se.LoggerFactory = lf
		se.SetLite(true)
		se.SetICEMulticastDNSMode(ice.MulticastDNSModeDisabled)
		se.SetICETimeouts(5*time.Second, 15*time.Second, 2*time.Second)
		se.SetReceiveMTU(fwd.MTU)
		if len(pub) > 0 {
			return se.SetICEAddressRewriteRules(webrtc.ICEAddressRewriteRule{External: pub, AsCandidateType: webrtc.ICECandidateTypeHost})
		}
		return nil
	}
	// UDP-Weg: ein Socket, NACK/RTX erlaubt
	if s.udp != nil {
		se := webrtc.SettingEngine{}
		if err := base(&se); err != nil {
			return err
		}
		s.iceFilter(&se)
		se.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4, webrtc.NetworkTypeUDP6})
		se.SetICEUDPMux(ice.NewUDPMuxDefault(ice.UDPMuxParams{UDPConn: s.udp, Logger: lf.NewLogger("udpmux")}))
		api, err := newAPI(se, true)
		if err != nil {
			return err
		}
		s.apiUDP = api
	}
	// TCP-Weg: ICE-TCP passiv ueber die Weiche, ohne NACK/RTX (H5)
	{
		se := webrtc.SettingEngine{}
		if err := base(&se); err != nil {
			return err
		}
		s.iceFilter(&se)
		se.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeTCP4, webrtc.NetworkTypeTCP6})
		se.SetICETCPMux(ice.NewTCPMuxDefault(ice.TCPMuxParams{Listener: s.mux.ICE, Logger: lf.NewLogger("tcpmux"), ReadBufferSize: 64}))
		api, err := newAPI(se, false)
		if err != nil {
			return err
		}
		s.apiTCP = api
	}
	// TLS-Weg: Geraet -> TURN-TLS -> Relay auf RelayIP -> Knoten (nur zu sich selbst)
	{
		se := webrtc.SettingEngine{}
		se.LoggerFactory = lf
		se.SetLite(true)
		se.SetICEMulticastDNSMode(ice.MulticastDNSModeDisabled)
		se.SetICETimeouts(5*time.Second, 15*time.Second, 2*time.Second)
		se.SetReceiveMTU(fwd.MTU)
		se.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
		relay := s.cfg.RelayIP
		se.SetIncludeLoopbackCandidate(relay.IsLoopback())
		se.SetIPFilter(func(ip net.IP) bool { return ip.Equal(relay) })
		api, err := newAPI(se, false)
		if err != nil {
			return err
		}
		s.apiTURN = api
	}
	return nil
}

func (s *server) startTURN() error {
	lf := logging.NewDefaultLoggerFactory()
	lf.DefaultLogLevel = logging.LogLevelError
	relay := s.cfg.RelayIP
	ts, err := turn.NewServer(turn.ServerConfig{
		Realm:         s.cfg.Name,
		LoggerFactory: lf,
		AuthHandler:   turn.NewLongTermAuthHandler(s.turnSecret, lf.NewLogger("turn")),
		ListenerConfigs: []turn.ListenerConfig{{
			Listener:              s.mux.TURN,
			RelayAddressGenerator: &turn.RelayAddressGeneratorStatic{RelayAddress: relay, Address: relay.String()},
			// Kein offenes Relay: nur der Knoten selbst ist als Gegenstelle erlaubt (§3.4)
			PermissionHandler: func(_ net.Addr, peer net.IP) bool { return peer.Equal(relay) },
		}},
	})
	if err != nil {
		return fmt.Errorf("turn: %w", err)
	}
	s.turn = ts
	return nil
}

// reaper raeumt alte und gescheiterte Beine ab.
func (s *server) reaper() {
	for range time.Tick(5 * time.Second) {
		now := time.Now()
		var old []*leg
		s.mu.Lock()
		for _, l := range s.legs {
			st := l.pc.ConnectionState()
			if now.Sub(l.created) > 15*time.Minute ||
				(st == webrtc.PeerConnectionStateFailed || st == webrtc.PeerConnectionStateClosed) && now.Sub(l.created) > 30*time.Second ||
				st == webrtc.PeerConnectionStateNew && now.Sub(l.created) > 60*time.Second {
				old = append(old, l)
			}
		}
		s.mu.Unlock()
		for _, l := range old {
			s.closeLeg(l)
		}
	}
}

func (s *server) onICEConn(ufrag string, c *netx.CoalesceConn) {
	s.mu.Lock()
	l := s.ufrags[ufrag]
	s.mu.Unlock()
	if l != nil {
		l.fl.SetFlusher(c)
	}
}
