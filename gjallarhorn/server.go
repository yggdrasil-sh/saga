package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/netip"
	"os"
	"os/signal"
	"path/filepath"
	"runtime/pprof"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/logging"
	"github.com/pion/turn/v5"
	"github.com/pion/webrtc/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
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
	Mode      string // "probe" (Spiegel) oder "node" (Weiterleiter)
	Dev       bool   // HORN_DEV=1: Entwicklungs-Token und Testseite (nur Tests!)
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
		Mode:   env("HORN_MODE", "probe"),
		Dev:    env("HORN_DEV", "0") == "1",
	}
	if c.Mode != "probe" && c.Mode != "node" {
		return nil, errors.New("HORN_MODE: probe oder node")
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

	// Betriebsart node
	node                    *sfu.Node
	sfuUDP, sfuTCP, sfuTURN *webrtc.API
	gcc                     gccHook
	iceMux                  *netx.ICEMux
	bySFU                   map[*sfu.Leg]*leg
	devSecret               string
	verifier                *sagaVerifier          // P3: Medien-Token von Saga (Ed25519)
	used                    usedTokens             // jedes Token einmal je Zweck
	ctl                     *ctlClient             // Steuerkanal zu Saga (HORN_SAGA_CTL)
	origin                  atomic.Pointer[string] // Herkunft der Saga-Seiten (CORS, /probe)
	graves                  map[string]time.Time   // geschlossene Raeume (gesperrt bis)
	kicked                  map[string]time.Time   // entfernte Teilnehmende "raum|peer" (gesperrt bis)
	closing                 atomic.Bool
	capture                 *capture // nur Tests: Mitschnitt der eingehenden Rahmen (HORN_CAPTURE)

	pubIPs   []string                      // oeffentliche Adressen (Kandidaten)
	udpCheck atomic.Pointer[netx.UDPCheck] // Ergebnis des UDP-Selbsttests

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
	return runNode(args, bench, "")
}

// runNode startet den Knoten; mode "" = aus HORN_MODE.
func runNode(args []string, bench bool, mode string) error {
	cfg, err := loadConfig(bench)
	if err != nil {
		return err
	}
	if mode != "" {
		cfg.Mode = mode
	}
	if pf := os.Getenv("HORN_CPUPROFILE"); pf != "" { // Entwicklung: CPU-Profil
		f, err := os.Create(pf)
		if err == nil {
			_ = pprof.StartCPUProfile(f)
			defer func() { pprof.StopCPUProfile(); f.Close() }()
		}
	}
	if pf := os.Getenv("HORN_MEMPROFILE"); pf != "" { // Entwicklung: Allokationsprofil beim Beenden
		defer func() {
			if f, err := os.Create(pf); err == nil {
				_ = pprof.Lookup("allocs").WriteTo(f, 0)
				f.Close()
			}
		}()
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
	s := &server{cfg: cfg, legs: map[string]*leg{}, ufrags: map[string]*leg{}, perIP: map[string]int{}, start: time.Now(), turnSecret: randHex(32),
		bySFU: map[*sfu.Leg]*leg{}, devSecret: randHex(32), verifier: &sagaVerifier{}, graves: map[string]time.Time{}, kicked: map[string]time.Time{}}
	if cfg.Mode == "node" {
		nc := sfu.Config{Name: cfg.Name, MaxLegs: cfg.MaxLegs, OnLegClosed: s.onSFULegClosed}
		if cfg.Dev {
			nc.Verifier = sfu.VerifierFunc(s.verifyDev)
			log.Printf("WARNUNG: HORN_DEV=1 - Entwicklungs-Token ohne Saga, nur fuer Tests")
		} else {
			// P3: Ed25519-Token von Saga; Schluessel aus der Steuerverbindung bzw. HORN_SAGA_PUB
			if k := strings.TrimSpace(os.Getenv("HORN_SAGA_PUB")); k != "" {
				b, err := base64.StdEncoding.DecodeString(k)
				if err != nil || len(b) != ed25519.PublicKeySize {
					return nil, errors.New("HORN_SAGA_PUB: 32 Byte Base64 erwartet")
				}
				s.verifier.SetKey(b)
			}
			nc.Verifier = s.verifier
		}
		s.node = sfu.NewNode(nc)
		if o := os.Getenv("HORN_SAGA_ORIGIN"); o != "" {
			s.setOrigin(o)
		}
		if d := os.Getenv("HORN_CAPTURE"); d != "" {
			s.capture = newCapture(d)
			log.Printf("WARNUNG: HORN_CAPTURE - Mitschnitt eingehender Rahmen nach %s, nur fuer Tests", d)
		}
	}
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
	// eigener ICE-TCP-Mux (ohne Allokation je Paket): Standard im Knoten,
	// die Probe misst weiter mit pions Mux (HORN_ICEMUX=own|pion)
	if im := env("HORN_ICEMUX", map[bool]string{true: "own", false: "pion"}[cfg.Mode == "node"]); im == "own" {
		s.iceMux = netx.NewICEMux(s.mux.Addr())
		s.mux.ICEMux = s.iceMux
	}
	s.mux.NoCoalesce = env("HORN_COALESCE", "1") == "0"
	// Nachlaeufer gegen lange TCP-Staus: Standard im Knoten, die Probe misst
	// wie bisher ohne (HORN_TAILPROBE_MS, 0 = aus)
	tp := "0"
	if cfg.Mode == "node" {
		tp = "10"
	}
	if ms, err := strconv.Atoi(env("HORN_TAILPROBE_MS", tp)); err == nil && ms > 0 {
		s.mux.TailProbe = time.Duration(ms) * time.Millisecond
	} else {
		s.mux.NoTailProbe = true
	}
	if env("HORN_TAILPROBE", "auto") == "always" { // Tests mit nachgebildetem Verlust
		s.mux.TailMode = netx.TailAlways
	}
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
	go s.udpSelfTest()
	if s.node != nil && !cfg.Dev {
		go s.roomReaper()
		if u := os.Getenv("HORN_SAGA_CTL"); u != "" {
			key := os.Getenv("HORN_CTL_KEY")
			_ = os.Unsetenv("HORN_CTL_KEY")
			c, err := newCtl(s, u, key)
			if err != nil {
				return nil, err
			}
			s.ctl = c
			go c.run()
		} else if !s.verifier.HasKey() {
			log.Printf("WARNUNG: weder HORN_SAGA_CTL noch HORN_SAGA_PUB - der Knoten nimmt keine Beine an")
		}
	}
	udpInfo := "aus"
	if s.udp != nil {
		udpInfo = s.udp.LocalAddr().String()
	}
	log.Printf("gjallarhorn %s %s: name=%s tcp=%s udp=%s tls=%s proxy=%d", version, cfg.Mode, cfg.Name, s.mux.Addr(), udpInfo, certs.Mode, cfg.Proxy)
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
	s.closing.Store(true)
	s.mu.Lock()
	ls := make([]*leg, 0, len(s.legs))
	for _, l := range s.legs {
		ls = append(ls, l)
	}
	s.mu.Unlock()
	for _, l := range ls {
		s.closeLeg(l)
	}
	if s.node != nil {
		s.node.Close()
	}
	if s.iceMux != nil {
		_ = s.iceMux.Close()
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
	s.pubIPs = pub
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
		if s.node != nil {
			if s.sfuUDP, err = newSFUAPI(se, true, &s.gcc); err != nil {
				return err
			}
		}
	}
	// TCP-Weg: ICE-TCP passiv ueber die Weiche, ohne NACK/RTX (H5)
	{
		se := webrtc.SettingEngine{}
		if err := base(&se); err != nil {
			return err
		}
		s.iceFilter(&se)
		se.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeTCP4, webrtc.NetworkTypeTCP6})
		if s.iceMux != nil {
			se.SetICETCPMux(s.iceMux)
		} else {
			se.SetICETCPMux(ice.NewTCPMuxDefault(ice.TCPMuxParams{Listener: s.mux.ICE, Logger: lf.NewLogger("tcpmux"), ReadBufferSize: 64}))
		}
		api, err := newAPI(se, false)
		if err != nil {
			return err
		}
		s.apiTCP = api
		if s.node != nil {
			if s.sfuTCP, err = newSFUAPI(se, false, nil); err != nil {
				return err
			}
		}
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
		if s.node != nil {
			if s.sfuTURN, err = newSFUAPI(se, false, nil); err != nil {
				return err
			}
		}
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

// udpSelfTest prueft, ob 443/UDP antwortet: Binding-Anfragen von einem
// eigenen Socket an die oeffentlichen Adressen (sonst an die gebundene
// Adresse, Ergebnis "lokal"). Die Weiche beantwortet einfache Bindings
// selbst. Das ist eine Vorpruefung; die Gegenprobe von aussen macht Saga
// beim Koppeln (P3) ueber denselben Beantworter.
func (s *server) udpSelfTest() *netx.UDPCheck {
	res := &netx.UDPCheck{State: "aus", At: time.Now().UTC().Format(time.RFC3339)}
	if s.udp == nil {
		s.udpCheck.Store(res)
		return res
	}
	port := s.udp.LocalAddr().(*net.UDPAddr).Port
	var targets []netip.AddrPort
	via := "oeffentlich"
	for _, ip := range s.pubIPs {
		if a, err := netip.ParseAddr(ip); err == nil {
			targets = append(targets, netip.AddrPortFrom(a.Unmap(), uint16(port)))
		}
	}
	if len(targets) == 0 {
		via = "lokal"
		la := s.udp.LocalAddr().(*net.UDPAddr).AddrPort()
		a := la.Addr().Unmap()
		if a.IsUnspecified() {
			a = netip.MustParseAddr("127.0.0.1")
		}
		targets = append(targets, netip.AddrPortFrom(a, la.Port()))
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	from, rtt, err := netx.ProbeUDP(ctx, targets)
	for _, t := range targets {
		if via == "lokal" {
			res.Tested = append(res.Tested, "lokal")
		} else if t.Addr().Is4() {
			res.Tested = append(res.Tested, "v4")
		} else {
			res.Tested = append(res.Tested, "v6")
		}
	}
	if err != nil {
		res.State = "fehlt"
	} else {
		res.State, res.Via, res.RTTms = "ok", via, float64(rtt.Microseconds())/1000
		_ = from
	}
	s.udpCheck.Store(res)
	log.Printf("udp-selbsttest: %s (%s)", res.State, via)
	return res
}

// UDPCheck liefert das letzte Ergebnis des Selbsttests.
func (s *server) UDPCheck() netx.UDPCheck {
	if c := s.udpCheck.Load(); c != nil {
		return *c
	}
	return netx.UDPCheck{State: "unbekannt"}
}

// reaper raeumt alte und gescheiterte Beine ab.
func (s *server) reaper() {
	for range time.Tick(5 * time.Second) {
		now := time.Now()
		var old []*leg
		s.mu.Lock()
		for _, l := range s.legs {
			st := l.state()
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
