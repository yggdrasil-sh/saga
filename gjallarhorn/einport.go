package main

import (
	"context"
	"crypto/subtle"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/netip"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// Betriebsart "Ein Name, ein Port" (Saga 2.5, eingebauter Knoten unter saga-media mit SAGA_TLS=1, media/einport.go):
// Der Knoten lauscht auf keinem eigenen TCP-Port. saga-media nimmt alle Verbindungen auf dem gemeinsamen Port (443) an
// und uebergibt TURN-TLS, TLS-ALPN-01 und rohes ICE-TCP samt Socket (SCM_RIGHTS ueber das Socket-Paar fd 3, Umgebung
// HORN_EINPORT_FD), den schon gelesenen Bytes und der echten Client-Adresse. Die Weiche (netx.Mux.Inject) bedient sie
// wie selbst angenommene - Tune, Buendeln und Nachlaeufer wirken auf dem echten Socket, die Grenze je Adresse
// (HORN_MAX_PER_IP) zaehlt echte Client-Adressen statt der des Proxys.
//   - Internes HTTP (fd 4, HORN_EINPORT_HTTP_FD): saga-media leitet /api/sfu/* und /probe dorthin weiter; die
//     Client-Adresse steht in X-Saga-Einport-For, gilt nur mit X-Saga-Einport-Key (= HORN_CTL_KEY).
//   - HTTP-Client ohne ALPN (TLS hat der Knoten beendet, kein STUN): Geraete-Pfade bedient der Knoten, alles andere geht
//     an saga-media zurueck (Unix-Socket HORN_EINPORT_BACK, gleicher Schluessel).
// Nachricht je Uebergabe: [1][Laenge der Adresse][Adresse "ip:port"][Bytes] + SCM_RIGHTS (genau ein Socket).
// Nach dem Aufbau schickt der Knoten "ready"; vorher nimmt saga-media keine Uebergaben vor.

const (
	einKeyHeader = "X-Saga-Einport-Key"
	einForHeader = "X-Saga-Einport-For"
)

type einport struct {
	hand *net.UnixConn
	http net.Listener
	back string
	key  string
}

// einportFromEnv -> nil ohne HORN_EINPORT_FD (normaler Betrieb).
func einportFromEnv(key string) (*einport, error) {
	fdS := os.Getenv("HORN_EINPORT_FD")
	if fdS == "" {
		return nil, nil
	}
	if key == "" {
		return nil, errors.New("HORN_EINPORT_FD ohne HORN_CTL_KEY")
	}
	fd, err := strconv.Atoi(fdS)
	if err != nil || fd < 3 {
		return nil, errors.New("HORN_EINPORT_FD ungueltig")
	}
	f := os.NewFile(uintptr(fd), "einport")
	c, err := net.FileConn(f)
	_ = f.Close()
	if err != nil {
		return nil, fmt.Errorf("einport: %w", err)
	}
	uc, ok := c.(*net.UnixConn)
	if !ok {
		c.Close()
		return nil, errors.New("einport: kein Unix-Socket")
	}
	e := &einport{hand: uc, back: os.Getenv("HORN_EINPORT_BACK"), key: key}
	if hs := os.Getenv("HORN_EINPORT_HTTP_FD"); hs != "" {
		hfd, err := strconv.Atoi(hs)
		if err != nil || hfd < 3 {
			return nil, errors.New("HORN_EINPORT_HTTP_FD ungueltig")
		}
		lf := os.NewFile(uintptr(hfd), "einport-http")
		ln, err := net.FileListener(lf)
		_ = lf.Close()
		if err != nil {
			return nil, fmt.Errorf("einport http: %w", err)
		}
		e.http = ln
	}
	for _, k := range []string{"HORN_EINPORT_FD", "HORN_EINPORT_HTTP_FD"} {
		_ = os.Unsetenv(k) // Kindprozesse (Selbst-Update gibt es hier nicht) sollen die fds nicht erben
	}
	return e, nil
}

// innerFromEnv: 2.5 A3 - internes HTTP ohne "Ein Port" (saga-media legt node.sock immer an, wenn es den Knoten
// startet; fd aus HORN_INNER_HTTP_FD). nil ohne die Variable.
func innerFromEnv() (net.Listener, error) {
	v := os.Getenv("HORN_INNER_HTTP_FD")
	if v == "" {
		return nil, nil
	}
	_ = os.Unsetenv("HORN_INNER_HTTP_FD")
	fd, err := strconv.Atoi(v)
	if err != nil || fd < 3 {
		return nil, errors.New("HORN_INNER_HTTP_FD ungueltig")
	}
	if os.Getenv("HORN_CTL_KEY") == "" {
		return nil, errors.New("HORN_INNER_HTTP_FD ohne HORN_CTL_KEY")
	}
	f := os.NewFile(uintptr(fd), "inner-http")
	ln, err := net.FileListener(f)
	_ = f.Close()
	if err != nil {
		return nil, fmt.Errorf("internes http: %w", err)
	}
	return ln, nil
}

// receive nimmt Uebergaben an, bis saga-media das Socket-Paar schliesst (dann beendet sich der Knoten nicht von selbst:
// saga-media beendet ihn per Signal).
func (s *server) einportReceive() {
	e := s.ein
	buf := make([]byte, 70<<10)
	oob := make([]byte, syscall.CmsgSpace(4*4))
	for {
		n, oobn, _, _, err := e.hand.ReadMsgUnix(buf, oob)
		if err != nil || (n == 0 && oobn == 0) {
			if !s.closing.Load() {
				log.Printf("einport: uebergabe beendet (%v)", err)
			}
			return
		}
		fds := parseRights(oob[:oobn])
		if len(fds) != 1 || n < 2 || buf[0] != 1 || 2+int(buf[1]) > n {
			for _, fd := range fds {
				_ = syscall.Close(fd)
			}
			s.mux.Stats.Rejected.Add(1)
			continue
		}
		var remote net.Addr
		if ap, err := netip.ParseAddrPort(string(buf[2 : 2+int(buf[1])])); err == nil {
			remote = net.TCPAddrFromAddrPort(ap)
		}
		pre := append([]byte(nil), buf[2+int(buf[1]):n]...)
		f := os.NewFile(uintptr(fds[0]), "uebergabe")
		c, err := net.FileConn(f)
		_ = f.Close()
		if err != nil {
			s.mux.Stats.Rejected.Add(1)
			continue
		}
		s.mux.Inject(c, pre, remote)
	}
}

func parseRights(oob []byte) []int {
	msgs, err := syscall.ParseSocketControlMessage(oob)
	if err != nil {
		return nil
	}
	var out []int
	for i := range msgs {
		if fds, err := syscall.ParseUnixRights(&msgs[i]); err == nil {
			out = append(out, fds...)
		}
	}
	return out
}

// einportReady: saga-media darf ab jetzt uebergeben.
func (s *server) einportReady() {
	if _, err := s.ein.hand.Write([]byte("ready")); err != nil {
		log.Printf("einport: ready nicht gesendet: %v", err)
	}
}

// devicePath: Pfade des Knotens unter dem Saga-Ursprung (saga-media leitet genau diese weiter).
func devicePath(p string) bool { return strings.HasPrefix(p, "/api/sfu/") || p == "/probe" }

// devPagePath: Entwicklerseiten des Knotens (nur HORN_DEV=1, Pruefstand mit MEDIA_NODE_SOCK).
func devPagePath(p string) bool {
	return strings.HasPrefix(p, "/dev/") || strings.HasPrefix(p, "/api/dev/") || p == "/style.css"
}

// einportInner: internes HTTP (von saga-media) - nur mit Schluessel; Client-Adresse aus dem Kopf.
func (s *server) einportInner(h http.Handler) http.Handler { return s.innerHandler(h, s.ein.key) }

// innerHandler: internes HTTP von saga-media ("Ein Port" fd 4 bzw. 2.5 A3 ohne "Ein Port" HORN_INNER_HTTP_FD): nur
// Geraete-Pfade, nur mit Schluessel (HORN_CTL_KEY); die echte Client-Adresse steht in X-Saga-Einport-For.
func (s *server) innerHandler(h http.Handler, key string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		k := r.Header.Get(einKeyHeader)
		ip, err := netip.ParseAddr(strings.TrimSpace(r.Header.Get(einForHeader)))
		dev := s.cfg != nil && s.cfg.Dev && devPagePath(r.URL.Path) // nur Pruefstand (HORN_DEV=1): Testseite, Entwicklungs-Token
		if key == "" || len(k) != len(key) || subtle.ConstantTimeCompare([]byte(k), []byte(key)) != 1 || err != nil || !(devicePath(r.URL.Path) || dev) {
			writeJSON(w, http.StatusForbidden, map[string]string{"error": "forbidden"})
			return
		}
		r.Header.Del(einKeyHeader)
		r.Header.Del(einForHeader)
		r.RemoteAddr = netip.AddrPortFrom(ip.Unmap(), 0).String()
		h.ServeHTTP(w, r)
	})
}

// einportFront: HTTP nach TLS im Knoten (Client ohne ALPN) - Geraete-Pfade hier, alles andere an saga-media zurueck.
func (s *server) einportFront(h http.Handler) http.Handler {
	back := &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.Out.URL.Scheme, pr.Out.URL.Host = "http", "saga"
			pr.Out.Host = pr.In.Host
			pr.Out.Header.Set(einKeyHeader, s.ein.key)
			pr.Out.Header.Set(einForHeader, ipKey(pr.In))
		},
		Transport: &http.Transport{Proxy: nil, DisableCompression: true, MaxIdleConnsPerHost: 16, IdleConnTimeout: 30 * time.Second,
			DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				var d net.Dialer
				return d.DialContext(ctx, "unix", s.ein.back)
			}},
		ErrorLog: log.New(io.Discard, "", 0),
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			w.Header().Set("Retry-After", "5")
			http.Error(w, "Saga ist gerade nicht erreichbar.", http.StatusBadGateway)
		},
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		r.Header.Del(einKeyHeader)
		r.Header.Del(einForHeader)
		if devicePath(r.URL.Path) {
			h.ServeHTTP(w, r)
			return
		}
		if s.ein.back == "" {
			http.NotFound(w, r)
			return
		}
		back.ServeHTTP(w, r)
	})
}

// iceTCPMode: HORN_ICE_TCP 0|1|auto. auto: aus, wenn HORN_PUBLIC_URL einen anderen Port nennt als den, auf dem der
// Knoten lauscht (TLS-Durchreiche bzw. Portweiterleitung 443 -> 8443: rohes ICE-TCP kommt dort nie bzw. am falschen
// Port an, Geraete warteten bis zu 4 s, bevor sie TLS nehmen).
func iceTCPMode(v, publicURL string, listenPort int) (bool, error) {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "0", "off", "false":
		return false, nil
	case "1", "on", "true":
		return true, nil
	case "", "auto":
	default:
		return true, errors.New("HORN_ICE_TCP: 0, 1 oder auto")
	}
	u := strings.TrimSpace(publicURL)
	if u == "" {
		return true, nil
	}
	port := 443
	if i := strings.LastIndex(u, ":"); i > len("https:") && !strings.HasSuffix(u, "]") {
		p, err := strconv.Atoi(strings.TrimRight(u[i+1:], "/"))
		if err == nil {
			port = p
		}
	}
	return port == listenPort, nil
}
