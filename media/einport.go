package main

import (
	"bytes"
	"context"
	"crypto/subtle"
	"crypto/tls"
	"encoding/binary"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// Betriebsart "Ein Name, ein Port" (Saga 2.5, Konzept saga-2.5-einport-konzept.md, Variante A/B "Weiche in saga-media").
//
// saga-media lauscht zusaetzlich auf TLS_LISTEN (im Container :443). Von jeder Verbindung liest es nur den Anfang
// (optional zuerst den PROXY-v2-Kopf, TLS_PROXY_PROTOCOL=1) und entscheidet:
//   - TLS mit ALPN h2/http/1.1/http/1.0: TLS hier beenden, Saga wie auf PORT bedienen (nur http/1.1, Stufe 1 ohne h2),
//     echte Client-Adresse als X-Forwarded-For, X-Forwarded-Proto https.
//   - TLS ohne ALPN bzw. stun.turn (TURN-TLS der Browser und Apps), acme-tls/1 (TLS-ALPN-01) und rohes ICE-TCP
//     (RFC 4571, erstes Byte 0x00/0x01): den Socket selbst samt gelesener Bytes und echter Client-Adresse an den
//     eingebauten Knoten uebergeben (SCM_RIGHTS ueber ein Unix-Socket-Paar). Der Knoten bedient ihn mit seiner Weiche
//     (netx.Mux); BBR, TCP_NOTSENT_LOWAT, Buendeln und Nachlaeufer wirken wie auf eigenen Sockets. HTTP-Clients ohne ALPN
//     reicht der Knoten nach dem Handshake an saga-media zurueck (Unix-Socket back.sock, Kopf mit Schluessel).
//   - anderes erstes Byte (Klartext-HTTP ...): schliessen.
// Ohne bereiten Knoten (Absturz, Update, SAGA_HORN=off) bedient saga-media TLS ohne ALPN selbst - HTTP geht, TURN nicht.
// HTTPS geht erst, wenn ein gueltiges Zertifikat fuer den Namen aus BASE_URL vorliegt (einport_cert.go, HSTS).
// Geraete-HTTP des Knotens (/api/sfu/*, /probe) leitet saga-media an dessen internes HTTP weiter (node.sock).
// PORT (8080) bleibt unveraendert: Healthcheck, alte L7-Proxys, Rueckweg.

const (
	einKeyHeader = "X-Saga-Einport-Key" // Schluessel zwischen saga-media und Knoten (= HORN_CTL_KEY)
	einForHeader = "X-Saga-Einport-For" // echte Client-Adresse (nur zusammen mit dem Schluessel)
	maxHello     = 64 << 10             // ClientHello hoechstens so lang (mehrere Datensaetze)
)

// Fristen und Grenzen der Weiche (Tests aendern sie).
var (
	edgeFirstByte  = 5 * time.Second  // PROXY-Kopf und erstes Byte bzw. ganzer ClientHello
	edgeHandshake  = 10 * time.Second // TLS-Handshake fuer Saga
	edgeMaxPending = int64(512)       // Verbindungen in der Zuordnung (noch ohne Ziel)
	edgeSendWait   = 2 * time.Second  // Uebergabe an den Knoten
)

type edgeStats struct {
	Accepted, HTTPS, Handed, ICE, ACME, Rejected, ProxyErr, HelloErr, TLSErr, Busy, NoCert, Local atomic.Uint64
}

type edge struct {
	m         *media
	c         *config
	ln        net.Listener
	httpLn    *chanListener
	cert      *edgeCert
	tlsCfg    *tls.Config
	hand      atomic.Pointer[handoff]
	key       string
	dir       string
	nodeSock  string
	backSock  string
	nodeProxy *httputil.ReverseProxy
	pending   atomic.Int64
	st        edgeStats
	proxySeen atomic.Int64 // Unix-Sekunden des letzten gueltigen PROXY-Kopfs
	proxyBad  atomic.Int64 // ... des letzten abgewiesenen (fehlt bzw. ungueltig)
	onState   func()       // Zustand geaendert (Zertifikat, Knoten) -> an Saga melden
	stop      chan struct{}
	stopOnce  sync.Once
}

// newEdge oeffnet den TLS-Eingang und die internen Unix-Sockets (privater Ordner 0700).
func newEdge(m *media, key string) (*edge, error) {
	c := m.c
	dir, err := os.MkdirTemp("", "saga-einport-")
	if err != nil {
		return nil, err
	}
	_ = os.Chmod(dir, 0o700)
	lc := net.ListenConfig{KeepAlive: 30 * time.Second}
	ln, err := lc.Listen(context.Background(), "tcp", c.tlsListen)
	if err != nil {
		_ = os.RemoveAll(dir)
		return nil, err
	}
	e := &edge{m: m, c: c, ln: ln, key: key, dir: dir, nodeSock: filepath.Join(dir, "node.sock"), backSock: filepath.Join(dir, "back.sock"),
		httpLn: newChanListener(ln.Addr()), cert: newEdgeCert(c), stop: make(chan struct{})}
	e.tlsCfg = &tls.Config{MinVersion: tls.VersionTLS12, NextProtos: []string{"http/1.1"}, GetCertificate: e.cert.get}
	e.cert.onChange = func(*certState) { e.changed() }
	e.cert.reload()
	e.nodeProxy = e.newNodeProxy()
	return e, nil
}

func (e *edge) changed() {
	if f := e.onState; f != nil {
		f()
	}
}

// start: Weiche, Saga-HTTP ueber TLS, HTTP-Rueckfall vom Knoten.
func (e *edge) start() error {
	_ = os.Remove(e.backSock)
	bl, err := net.Listen("unix", e.backSock)
	if err != nil {
		return err
	}
	_ = os.Chmod(e.backSock, 0o600)
	back := &http.Server{Handler: http.HandlerFunc(e.serveBack), ReadHeaderTimeout: 15 * time.Second, IdleTimeout: 75 * time.Second,
		MaxHeaderBytes: 64 << 10, ErrorLog: log.New(io.Discard, "", 0)}
	go func() { _ = back.Serve(bl) }()

	srv := &http.Server{Handler: e.m, ReadHeaderTimeout: 15 * time.Second, IdleTimeout: 75 * time.Second, MaxHeaderBytes: 64 << 10,
		ErrorLog: log.New(io.Discard, "", 0), ConnContext: connContext}
	cc := *e.c
	cc.trusted = trustSet{} // echte Client-Adressen: keine Ausnahme fuer "vertraute Proxys"
	lim := newLimitListener(e.httpLn, &cc)
	srv.ConnState = lim.connState
	go func() { _ = srv.Serve(lim) }()
	go e.cert.watch(e.stop)
	go e.serve()
	px := ""
	if e.c.tlsProxy {
		px = ", PROXY-v2-Kopf Pflicht"
	}
	logf("TLS-Eingang auf %s fuer %s (ein Port: HTTPS, TURN-TLS, ICE-TCP%s)", e.c.tlsListen, e.c.baseHost, px)
	return nil
}

func (e *edge) close() {
	e.stopOnce.Do(func() {
		close(e.stop)
		_ = e.ln.Close()
		_ = e.httpLn.Close()
		_ = os.RemoveAll(e.dir)
	})
}

func (e *edge) serve() {
	for {
		c, err := e.ln.Accept()
		if err != nil {
			if errors.Is(err, net.ErrClosed) {
				return
			}
			time.Sleep(50 * time.Millisecond)
			continue
		}
		e.st.Accepted.Add(1)
		if e.pending.Add(1) > edgeMaxPending {
			e.pending.Add(-1)
			e.st.Busy.Add(1)
			_ = c.Close()
			continue
		}
		go e.handle(c)
	}
}

// peekBuf: gelesene, noch nicht verbrauchte Bytes (gehen bei der Uebergabe mit bzw. werden vor TLS wieder gelesen).
type peekBuf struct {
	c net.Conn
	b []byte
}

func (p *peekBuf) need(n int) error {
	for len(p.b) < n {
		if cap(p.b) < n {
			nb := make([]byte, len(p.b), n+4096)
			copy(nb, p.b)
			p.b = nb
		}
		k, err := p.c.Read(p.b[len(p.b):cap(p.b)])
		p.b = p.b[:len(p.b)+k]
		if err != nil && len(p.b) < n {
			return err
		}
	}
	return nil
}

func (e *edge) handle(raw net.Conn) {
	pending := true
	done := func() {
		if pending {
			pending = false
			e.pending.Add(-1)
		}
	}
	defer done()
	var remote net.Addr = raw.RemoteAddr()
	_ = raw.SetReadDeadline(time.Now().Add(edgeFirstByte))
	p := &peekBuf{c: raw, b: make([]byte, 0, 4096)}
	if e.c.tlsProxy {
		a, err := e.readProxy(p)
		if err != nil {
			e.st.ProxyErr.Add(1)
			e.proxyBad.Store(time.Now().Unix())
			_ = raw.Close()
			return
		}
		e.proxySeen.Store(time.Now().Unix())
		if a != nil {
			remote = a
		}
	}
	if err := p.need(1); err != nil {
		_ = raw.Close()
		return
	}
	switch p.b[0] {
	case 0x00, 0x01: // ICE-TCP (RFC 4571: 2 Byte Laenge, STUN darunter)
		e.st.ICE.Add(1)
		if e.handover(raw, p.b, remote) != nil {
			_ = raw.Close()
		}
	case 0x16:
		alpn, err := e.readHello(p)
		if err != nil {
			e.st.HelloErr.Add(1)
			_ = raw.Close()
			return
		}
		switch {
		case len(alpn) == 1 && alpn[0] == "acme-tls/1": // TLS-ALPN-01: der Knoten beantwortet die Pruefung
			e.st.ACME.Add(1)
			if e.handover(raw, p.b, remote) != nil {
				_ = raw.Close()
			}
		case hasHTTP(alpn) || !e.cert.ok():
			e.serveLocal(raw, p.b, remote, done)
		default: // ohne ALPN bzw. stun.turn: TURN-TLS (oder HTTP-Client ohne ALPN -> Rueckfall im Knoten)
			if e.handover(raw, p.b, remote) != nil {
				e.serveLocal(raw, p.b, remote, done) // Knoten nicht da: HTTP geht weiter, TURN nicht
			}
		}
	default:
		e.st.Rejected.Add(1)
		_ = raw.Close()
	}
}

// readProxy: PROXY-v2-Kopf (Pflicht) -> Client-Adresse (nil bei LOCAL); verbraucht genau den Kopf.
func (e *edge) readProxy(p *peekBuf) (*net.TCPAddr, error) {
	need := 0
	for need == 0 {
		if err := p.need(len(p.b) + 1); err != nil {
			return nil, err
		}
		n, err := proxyNeed(p.b)
		if err != nil {
			return nil, err
		}
		need = n
	}
	if err := p.need(need); err != nil {
		return nil, err
	}
	a, err := parseProxyV2(p.b[:need])
	if err != nil {
		return nil, err
	}
	p.b = append(p.b[:0], p.b[need:]...)
	return a, nil
}

// readHello liest den ganzen ClientHello (ein oder mehrere TLS-Datensaetze), ohne ihn zu verbrauchen, und liefert die
// ALPN-Liste (Parser der Standardbibliothek).
func (e *edge) readHello(p *peekBuf) ([]string, error) {
	off, got, want := 0, 0, -1
	var hs []byte
	for want < 0 || got < want {
		if err := p.need(off + 5); err != nil {
			return nil, err
		}
		if p.b[off] != 0x16 {
			return nil, errBadHello
		}
		rl := int(binary.BigEndian.Uint16(p.b[off+3 : off+5]))
		if rl == 0 || rl > 16384 || off+5+rl > maxHello {
			return nil, errBadHello
		}
		if err := p.need(off + 5 + rl); err != nil {
			return nil, err
		}
		hs = append(hs, p.b[off+5:off+5+rl]...)
		off += 5 + rl
		got = len(hs)
		if want < 0 && got >= 4 {
			if hs[0] != 1 { // client_hello
				return nil, errBadHello
			}
			want = 4 + (int(hs[1])<<16 | int(hs[2])<<8 | int(hs[3]))
			if want > maxHello {
				return nil, errBadHello
			}
		}
	}
	alpn, ok := peekHello(p.b[:off])
	if !ok {
		return nil, errBadHello
	}
	return alpn, nil
}

var errBadHello = errors.New("kein gueltiger ClientHello")
var errPeeked = errors.New("gelesen")

type helloConn struct {
	net.Conn
	r io.Reader
}

func (c *helloConn) Read(b []byte) (int, error)         { return c.r.Read(b) }
func (c *helloConn) Write(b []byte) (int, error)        { return len(b), nil }
func (c *helloConn) Close() error                       { return nil }
func (c *helloConn) SetDeadline(time.Time) error        { return nil }
func (c *helloConn) SetReadDeadline(time.Time) error    { return nil }
func (c *helloConn) SetWriteDeadline(time.Time) error   { return nil }
func (c *helloConn) LocalAddr() net.Addr                { return &net.TCPAddr{} }
func (c *helloConn) RemoteAddr() net.Addr               { return &net.TCPAddr{} }

// peekHello: Server-Handshake gegen eine Kopie der ClientHello-Datensaetze; GetConfigForClient liefert ALPN und bricht ab.
func peekHello(rec []byte) (alpn []string, ok bool) {
	cfg := &tls.Config{GetConfigForClient: func(h *tls.ClientHelloInfo) (*tls.Config, error) {
		alpn, ok = append([]string(nil), h.SupportedProtos...), true
		return nil, errPeeked
	}}
	_ = tls.Server(&helloConn{r: bytes.NewReader(rec)}, cfg).Handshake()
	return alpn, ok
}

func hasHTTP(p []string) bool {
	for _, x := range p {
		if x == "h2" || x == "http/1.1" || x == "http/1.0" {
			return true
		}
	}
	return false
}

// prefixConn: zuerst die schon gelesenen Bytes, danach direkt die Verbindung (kein Lesepuffer im Weg).
type prefixConn struct {
	net.Conn
	pre []byte
}

func (c *prefixConn) Read(b []byte) (int, error) {
	if len(c.pre) > 0 {
		n := copy(b, c.pre)
		c.pre = c.pre[n:]
		return n, nil
	}
	return c.Conn.Read(b)
}

// edgeConn: TLS-Verbindung von Saga mit der echten Client-Adresse (aus dem PROXY-Kopf bzw. der Gegenstelle).
type edgeConn struct {
	net.Conn
	remote net.Addr
	ip     string
}

func (c *edgeConn) RemoteAddr() net.Addr { return c.remote }

// serveLocal: TLS hier beenden und an den HTTP-Server von Saga geben (nur mit gueltigem Zertifikat).
func (e *edge) serveLocal(raw net.Conn, pre []byte, remote net.Addr, done func()) {
	if !e.cert.ok() {
		e.st.NoCert.Add(1)
		_ = raw.Close()
		return
	}
	tc := tls.Server(&prefixConn{Conn: raw, pre: pre}, e.tlsCfg)
	_ = raw.SetDeadline(time.Now().Add(edgeHandshake))
	if err := tc.Handshake(); err != nil {
		e.st.TLSErr.Add(1)
		_ = raw.Close()
		return
	}
	_ = raw.SetDeadline(time.Time{})
	done()
	e.st.HTTPS.Add(1)
	ip := hostOf(remote.String())
	e.httpLn.push(&edgeConn{Conn: tc, remote: remote, ip: ip.String()})
}

// --- Uebergabe an den Knoten ----------------------------------------------------------------------------------------

// handoff: Unix-Socket-Paar (SOCK_SEQPACKET) zum laufenden Knoten. Je Uebergabe ein Paket:
// [1][Laenge der Adresse][Adresse "ip:port"][schon gelesene Bytes] + SCM_RIGHTS mit dem Socket.
type handoff struct {
	uc *net.UnixConn
	mu sync.Mutex
}

var errNoNode = errors.New("eingebauter Knoten nicht bereit")

func (e *edge) handover(raw net.Conn, pre []byte, remote net.Addr) error {
	h := e.hand.Load()
	if h == nil {
		return errNoNode
	}
	sc, ok := raw.(syscall.Conn)
	if !ok {
		return errNoNode
	}
	rc, err := sc.SyscallConn()
	if err != nil {
		return err
	}
	addr := remote.String()
	if len(addr) > 255 {
		return errNoNode
	}
	msg := make([]byte, 0, 2+len(addr)+len(pre))
	msg = append(msg, 1, byte(len(addr)))
	msg = append(append(msg, addr...), pre...)
	var werr error
	h.mu.Lock()
	_ = h.uc.SetWriteDeadline(time.Now().Add(edgeSendWait))
	cerr := rc.Control(func(fd uintptr) {
		_, _, werr = h.uc.WriteMsgUnix(msg, syscall.UnixRights(int(fd)), nil)
	})
	h.mu.Unlock()
	if cerr != nil || werr != nil {
		if werr != nil && !isTimeout(werr) {
			e.detach(h)
		}
		return errNoNode
	}
	e.st.Handed.Add(1)
	_ = raw.Close() // nur unsere Kopie: der Knoten haelt den Socket
	return nil
}

// attach: neues Socket-Paar zum gerade gestarteten Knoten. Bereit erst nach seinem Paket "ready"; Ende (EOF) = weg.
func (e *edge) attach(uc *net.UnixConn) {
	h := &handoff{uc: uc}
	go func() {
		buf := make([]byte, 64)
		for {
			n, err := uc.Read(buf)
			if err != nil || n == 0 {
				e.detach(h)
				return
			}
			if string(buf[:n]) == "ready" {
				if e.hand.Swap(h) != h {
					logf("TLS-Eingang: eingebauter Knoten bereit (Uebergabe an)")
					e.changed()
				}
			}
		}
	}()
}

func (e *edge) detach(h *handoff) {
	if e.hand.CompareAndSwap(h, nil) {
		logf("TLS-Eingang: eingebauter Knoten weg - TURN/ICE-TCP zu, HTTPS bedient saga-media allein")
		e.changed()
	}
	_ = h.uc.Close()
}

// nodeFiles: fuer den Start des Knotens - Socket-Paar (fd 3) und Lauscher fuer sein internes HTTP (fd 4).
// Der Aufrufer schliesst die zurueckgegebenen Dateien nach cmd.Start; attach laeuft schon.
func (e *edge) nodeFiles() ([]*os.File, error) {
	fds, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_SEQPACKET|syscall.SOCK_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	pf, cf := os.NewFile(uintptr(fds[0]), "einport"), os.NewFile(uintptr(fds[1]), "einport-knoten")
	pc, err := net.FileConn(pf)
	_ = pf.Close()
	if err != nil {
		_ = cf.Close()
		return nil, err
	}
	uc, ok := pc.(*net.UnixConn)
	if !ok {
		_ = pc.Close()
		_ = cf.Close()
		return nil, errors.New("socketpair: kein UnixConn")
	}
	_ = os.Remove(e.nodeSock)
	ln, err := net.ListenUnix("unix", &net.UnixAddr{Name: e.nodeSock, Net: "unix"})
	if err != nil {
		_ = uc.Close()
		_ = cf.Close()
		return nil, err
	}
	_ = os.Chmod(e.nodeSock, 0o600)
	ln.SetUnlinkOnClose(false)
	lf, err := ln.File()
	_ = ln.Close()
	if err != nil {
		_ = uc.Close()
		_ = cf.Close()
		return nil, err
	}
	e.attach(uc)
	return []*os.File{cf, lf}, nil
}

// nodeEnv: Umgebung des eingebauten Knotens in dieser Betriebsart.
func (e *edge) nodeEnv() []string {
	_, port, _ := net.SplitHostPort(e.c.tlsListen)
	env := []string{"HORN_EINPORT_FD=3", "HORN_EINPORT_HTTP_FD=4", "HORN_EINPORT_BACK=" + e.backSock,
		"HORN_NAME=" + e.c.baseHost, "HORN_PUBLIC_URL=" + e.c.baseOrigin, "HORN_LISTEN=:" + port, "HORN_PROXY_PROTOCOL=0"}
	if os.Getenv("HORN_ICE_TCP") == "" {
		// hinter einer TLS-Durchreiche (PROXY-Kopf) kommt rohes ICE-TCP nie an: kein Kandidat, Geraete nehmen gleich TLS
		env = append(env, "HORN_ICE_TCP="+map[bool]string{true: "0", false: "1"}[e.c.tlsProxy])
	}
	if e.c.tlsCert != "" {
		env = append(env, "HORN_TLS_CERT="+e.c.tlsCert, "HORN_TLS_KEY="+e.c.tlsKey)
	}
	return env
}

// einportInfo: Zustand fuer Admin -> Anrufe (POST /internal/horn/state, Feld einport). Keine Adressen, keine Pfade.
type einportInfo struct {
	Name      string `json:"name"`
	Port      int    `json:"port"`
	Proxy     bool   `json:"proxy"`
	ProxySeen int64  `json:"proxy_seen,omitempty"` // letzter gueltiger PROXY-Kopf (Unix-Sekunden, auf die Minute)
	ProxyBad  int64  `json:"proxy_bad,omitempty"`  // letzte Verbindung ohne bzw. mit ungueltigem Kopf
	Cert      string `json:"cert"`                 // ok | fehlt | name | abgelaufen | fehler
	CertSrc   string `json:"cert_src,omitempty"`   // own | dns01 | alpn | file
	NotAfter  int64  `json:"not_after,omitempty"`
	Node      bool   `json:"node"` // Uebergabe an den Knoten bereit
	H2        bool   `json:"h2"`
}

func minute(t int64) int64 { return t - t%60 }

func (e *edge) info() *einportInfo {
	_, ps, _ := net.SplitHostPort(e.c.tlsListen)
	port, _ := strconv.Atoi(ps)
	cs := e.cert.state()
	in := &einportInfo{Name: e.c.baseHost, Port: port, Proxy: e.c.tlsProxy, Cert: cs.state, CertSrc: cs.src, Node: e.hand.Load() != nil}
	if !cs.notAfter.IsZero() {
		in.NotAfter = cs.notAfter.Unix()
	}
	if t := e.proxySeen.Load(); t > 0 {
		in.ProxySeen = minute(t)
	}
	if t := e.proxyBad.Load(); t > 0 {
		in.ProxyBad = minute(t)
	}
	return in
}

// --- Geraete-HTTP des Knotens und HTTP-Rueckfall ---------------------------------------------------------------------

// nodePath: Pfade, die der Knoten unter dem Saga-Ursprung bedient (Saga selbst hat keine davon).
func nodePath(p string) bool { return strings.HasPrefix(p, "/api/sfu/") || p == "/probe" }

func (e *edge) newNodeProxy() *httputil.ReverseProxy {
	tr := &http.Transport{
		Proxy: nil,
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			var d net.Dialer
			return d.DialContext(ctx, "unix", e.nodeSock)
		},
		MaxIdleConns: 64, MaxIdleConnsPerHost: 64, IdleConnTimeout: 30 * time.Second, DisableCompression: true,
	}
	return &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.Out.URL.Scheme = "http"
			pr.Out.URL.Host = "knoten"
			pr.Out.Host = pr.In.Host
			h := pr.Out.Header
			h.Del(keyHeader)
			h.Set(einKeyHeader, e.key)
			h.Set(einForHeader, clientIP(pr.In, e.c.trusted))
		},
		Transport: tr,
		ErrorLog:  log.New(io.Discard, "", 0),
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "knoten nicht bereit"})
		},
	}
}

func keyEq(a, b string) bool {
	return len(a) == len(b) && subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}

// serveBack: HTTP-Rueckfall vom Knoten (HTTPS-Client ohne ALPN, TLS hat der Knoten beendet). Nur mit dem Schluessel;
// die Client-Adresse kommt dann aus einForHeader.
func (e *edge) serveBack(w http.ResponseWriter, r *http.Request) {
	ip := hostOf(r.Header.Get(einForHeader))
	if e.key == "" || !keyEq(r.Header.Get(einKeyHeader), e.key) || !ip.IsValid() {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	e.st.Local.Add(1)
	r = r.WithContext(context.WithValue(r.Context(), edgeKey{}, ip.String()))
	e.m.ServeHTTP(w, r)
}

// edgeKey: Anfrage kam ueber den TLS-Eingang (Wert = echte Client-Adresse).
type edgeKey struct{}

func edgeIP(r *http.Request) (string, bool) {
	v, ok := r.Context().Value(edgeKey{}).(string)
	return v, ok && v != ""
}

// chanListener: net.Listener fuer Verbindungen aus der Weiche (TLS schon beendet).
type chanListener struct {
	ch     chan net.Conn
	addr   net.Addr
	closed chan struct{}
	once   sync.Once
}

func newChanListener(a net.Addr) *chanListener {
	return &chanListener{ch: make(chan net.Conn, 64), addr: a, closed: make(chan struct{})}
}

func (l *chanListener) Accept() (net.Conn, error) {
	select {
	case c := <-l.ch:
		return c, nil
	case <-l.closed:
		return nil, net.ErrClosed
	}
}

func (l *chanListener) Close() error {
	l.once.Do(func() { close(l.closed) })
	return nil
}

func (l *chanListener) Addr() net.Addr { return l.addr }

func (l *chanListener) push(c net.Conn) {
	select {
	case l.ch <- c:
	case <-l.closed:
		_ = c.Close()
	case <-time.After(5 * time.Second):
		_ = c.Close()
	}
}
