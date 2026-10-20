package main

import (
	"fmt"
	"net"
	"net/netip"
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
	stall        time.Duration // kein Fortschritt beim Lesen/Schreiben so lange -> Verbindung zu (Slowloris)
	accessLog    bool          // Zugriffszeilen wie uvicorn (LOG_LEVEL info/debug)
	key          string        // gemeinsames Geheimnis fuer /internal/media/authz
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
	stall, err := envInt("MEDIA_STALL_TIMEOUT", 60, 5, 3600)
	if err != nil {
		return nil, err
	}
	c.stall = time.Duration(stall) * time.Second
	lvl := strings.ToLower(envStr("LOG_LEVEL", "info"))
	c.accessLog = lvl == "info" || lvl == "debug" || lvl == "trace"
	return c, nil
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
