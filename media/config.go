package main

import (
	"errors"
	"fmt"
	"net"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Konfiguration aus der Umgebung (dieselben Namen wie in Saga, dazu MEDIA_*).
type config struct {
	listen       string        // HOST:PORT nach aussen (bisheriger Saga-Port)
	upstream     string        // 127.0.0.1:MEDIA_INTERNAL_PORT (uvicorn)
	internalPort int           // an Python als SAGA_INTERNAL_PORT
	blobDir      string        // STATE_DIR/chat - nur Dateien direkt darin werden ausgeliefert
	trusted      trustSet      // TRUSTED_PROXIES (wie uvicorn forwarded_allow_ips)
	ttl          time.Duration // Berechtigung hoechstens so lange merken (MEDIA_AUTHZ_TTL, max. 60 s)
	maxConns     int           // gleichzeitige Verbindungen insgesamt
	maxStreams   int           // gleichzeitige Datei-Stroeme insgesamt
	maxStreamsIP int           // gleichzeitige Datei-Stroeme je Client-Adresse
	maxConnsIP   int           // offene Verbindungen je Client-Adresse (2.4, vertraute Proxys ausgenommen)
	idleUnauth   time.Duration // Leerlauf einer Verbindung ohne Anmeldedaten (Keep-Alive)
	stall        time.Duration // kein Fortschritt beim Lesen/Schreiben so lange -> Verbindung zu (Slowloris)
	accessLog    bool          // Zugriffszeilen wie uvicorn (LOG_LEVEL info/debug)
	key          string        // gemeinsames Geheimnis fuer /internal/media/authz

	// 2.5 "Ein Name, ein Port" (einport.go): zweiter Eingang mit TLS und Weiche
	tlsOn      bool   // SAGA_TLS=1 (oder TLS_LISTEN gesetzt)
	tlsListen  string // TLS_LISTEN, Standard :443
	tlsProxy   bool   // TLS_PROXY_PROTOCOL=1: vor jeder Verbindung ein PROXY-v2-Kopf (Pflicht)
	tlsCert    string // SAGA_TLS_CERT/SAGA_TLS_KEY: eigene Zertifikatsdateien (sonst vom eingebauten Knoten)
	tlsKey     string
	baseHost   string        // Name aus BASE_URL (Zertifikat, Knotenname)
	baseOrigin string        // https://name[:port] aus BASE_URL (Adresse des eingebauten Knotens)
	hornData   string        // HORN_DATA bzw. STATE_DIR/horn (Zertifikat des Knotens)
	tlsReload  time.Duration // SAGA_TLS_RELOAD_S: so oft Zertifikatsdateien pruefen (Standard 30 s)
}

func envStr(name, def string) string {
	if v := strings.TrimSpace(os.Getenv(name)); v != "" {
		return v
	}
	return def
}

func envInt(name string, def, lo, hi int) (int, error) {
	v := strings.TrimSpace(os.Getenv(name))
	if v == "" {
		return def, nil
	}
	n, err := strconv.Atoi(v)
	if err != nil || n < lo || n > hi {
		return 0, fmt.Errorf("%s=%q: erwartet eine Zahl von %d bis %d", name, v, lo, hi)
	}
	return n, nil
}

func loadConfig() (*config, error) {
	c := &config{}
	port, err := envInt("PORT", 8080, 1, 65535)
	if err != nil {
		return nil, err
	}
	c.internalPort, err = envInt("MEDIA_INTERNAL_PORT", 8079, 1, 65535)
	if err != nil {
		return nil, err
	}
	if c.internalPort == port {
		return nil, fmt.Errorf("MEDIA_INTERNAL_PORT (%d) darf nicht gleich PORT sein", port)
	}
	if admin := strings.TrimSpace(os.Getenv("ADMIN_PORT")); admin == strconv.Itoa(c.internalPort) {
		return nil, fmt.Errorf("MEDIA_INTERNAL_PORT (%d) darf nicht gleich ADMIN_PORT sein", c.internalPort)
	}
	c.listen = net.JoinHostPort(envStr("HOST", "0.0.0.0"), strconv.Itoa(port))
	c.upstream = net.JoinHostPort("127.0.0.1", strconv.Itoa(c.internalPort))
	dir, err := filepath.Abs(filepath.Join(envStr("STATE_DIR", "/data"), "chat"))
	if err != nil {
		return nil, err
	}
	c.blobDir = dir
	c.trusted = parseTrust(envStr("TRUSTED_PROXIES", "127.0.0.1"))
	ttl, err := envInt("MEDIA_AUTHZ_TTL", 60, 0, 60)
	if err != nil {
		return nil, err
	}
	c.ttl = time.Duration(ttl) * time.Second
	if c.maxConns, err = envInt("MEDIA_MAX_CONNS", 4096, 16, 1<<20); err != nil {
		return nil, err
	}
	if c.maxStreams, err = envInt("MEDIA_MAX_STREAMS", 1024, 1, 1<<20); err != nil {
		return nil, err
	}
	if c.maxStreamsIP, err = envInt("MEDIA_MAX_STREAMS_IP", 128, 1, 1<<20); err != nil {
		return nil, err
	}
	if c.maxConnsIP, err = envInt("MEDIA_MAX_CONNS_IP", 256, 2, 1<<20); err != nil {
		return nil, err
	}
	if c.maxConnsIP > c.maxConns {
		c.maxConnsIP = c.maxConns
	}
	idle, err := envInt("MEDIA_IDLE_UNAUTH", 10, 1, 75)
	if err != nil {
		return nil, err
	}
	c.idleUnauth = time.Duration(idle) * time.Second
	stall, err := envInt("MEDIA_STALL_TIMEOUT", 60, 5, 3600)
	if err != nil {
		return nil, err
	}
	c.stall = time.Duration(stall) * time.Second
	lvl := strings.ToLower(envStr("LOG_LEVEL", "info"))
	c.accessLog = lvl == "info" || lvl == "debug" || lvl == "trace"
	if err := c.loadTLS(port); err != nil {
		return nil, err
	}
	return c, nil
}

// loadTLS: Betriebsart "Ein Port" (2.5). SAGA_TLS=1 oder TLS_LISTEN schaltet sie ein; BASE_URL muss dann https sein
// (der Name dort ist der Name des Zertifikats und des eingebauten Knotens).
func (c *config) loadTLS(port int) error {
	on := strings.TrimSpace(os.Getenv("SAGA_TLS"))
	c.tlsListen = strings.TrimSpace(os.Getenv("TLS_LISTEN"))
	switch strings.ToLower(on) {
	case "", "0", "off", "false", "no":
		c.tlsOn = c.tlsListen != "" && on == ""
	case "1", "on", "true", "yes":
		c.tlsOn = true
	default:
		return fmt.Errorf("SAGA_TLS=%q: erwartet 0 oder 1", on)
	}
	c.hornData = envStr("HORN_DATA", filepath.Join(envStr("STATE_DIR", "/data"), "horn"))
	if !c.tlsOn {
		return nil
	}
	if c.tlsListen == "" {
		c.tlsListen = ":443"
	}
	h, p, err := net.SplitHostPort(c.tlsListen)
	if err != nil {
		return fmt.Errorf("TLS_LISTEN=%q: erwartet [HOST]:PORT", c.tlsListen)
	}
	if n, err := strconv.Atoi(p); err != nil || n < 1 || n > 65535 || n == port {
		return fmt.Errorf("TLS_LISTEN=%q: Port 1 bis 65535 und ungleich PORT", c.tlsListen)
	}
	c.tlsListen = net.JoinHostPort(h, p)
	switch v := strings.ToLower(strings.TrimSpace(os.Getenv("TLS_PROXY_PROTOCOL"))); v {
	case "", "0", "off", "false":
	case "1", "on", "true":
		c.tlsProxy = true
	default:
		return fmt.Errorf("TLS_PROXY_PROTOCOL=%q: erwartet 0 oder 1", v)
	}
	rl, err := envInt("SAGA_TLS_RELOAD_S", 30, 1, 3600)
	if err != nil {
		return err
	}
	c.tlsReload = time.Duration(rl) * time.Second
	c.tlsCert, c.tlsKey = strings.TrimSpace(os.Getenv("SAGA_TLS_CERT")), strings.TrimSpace(os.Getenv("SAGA_TLS_KEY"))
	if (c.tlsCert == "") != (c.tlsKey == "") {
		return errors.New("SAGA_TLS_CERT und SAGA_TLS_KEY nur zusammen setzen")
	}
	host, origin, err := httpsOrigin(os.Getenv("BASE_URL"))
	if err != nil {
		return fmt.Errorf("SAGA_TLS=1 braucht BASE_URL=https://<Name>: %v", err)
	}
	c.baseHost, c.baseOrigin = host, origin
	return nil
}

// httpsOrigin: BASE_URL -> Name (klein) und Ursprung https://name[:port] (ohne Pfad, Standardport 443 weggelassen).
func httpsOrigin(raw string) (string, string, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil {
		return "", "", errors.New("keine https-Adresse")
	}
	host := strings.ToLower(u.Hostname())
	if net.ParseIP(host) != nil || host == "localhost" || !reHornName.MatchString(host) || !strings.Contains(host, ".") {
		return "", "", errors.New("braucht einen DNS-Namen (keine IP, nicht localhost)")
	}
	origin := "https://" + host
	if p := u.Port(); p != "" && p != "443" {
		if n, err := strconv.Atoi(p); err != nil || n < 1 || n > 65535 {
			return "", "", errors.New("ungueltiger Port")
		}
		origin += ":" + p
	}
	return host, origin, nil
}

// trustSet: vertrauenswuerdige Reverse Proxys - gleiche Regeln wie uvicorn (forwarded_allow_ips):
// einzelne Adressen, Netze (CIDR) oder "*".
type trustSet struct {
	all  bool
	nets []netip.Prefix
}

func parseTrust(s string) trustSet {
	var t trustSet
	for _, item := range strings.Split(s, ",") {
		item = strings.TrimSpace(item)
		switch {
		case item == "":
		case item == "*":
			t.all = true
		case strings.Contains(item, "/"):
			if p, err := netip.ParsePrefix(item); err == nil {
				t.nets = append(t.nets, p.Masked())
			}
		default:
			if a, err := netip.ParseAddr(item); err == nil {
				t.nets = append(t.nets, netip.PrefixFrom(a.Unmap(), a.Unmap().BitLen()))
			}
		}
	}
	return t
}

func (t trustSet) has(a netip.Addr) bool {
	if t.all {
		return true
	}
	if !a.IsValid() {
		return false
	}
	a = a.Unmap()
	for _, p := range t.nets {
		if p.Contains(a) {
			return true
		}
	}
	return false
}

// hostOf: Adresse aus "ip:port", "[v6]:port" oder "ip" (X-Forwarded-For-Eintrag).
func hostOf(s string) netip.Addr {
	s = strings.TrimSpace(s)
	if ap, err := netip.ParseAddrPort(s); err == nil {
		return ap.Addr().Unmap()
	}
	if a, err := netip.ParseAddr(strings.Trim(s, "[]")); err == nil {
		return a.Unmap()
	}
	return netip.Addr{}
}
