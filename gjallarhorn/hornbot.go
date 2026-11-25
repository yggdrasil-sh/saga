package main

// horn-bot (2263-2 Messwerkzeug): viele synthetische Geraete (sfuBot) in EINEM Prozess, die mit echten Saga-Token in
// einen echten Anruf ueber Saga gehen - Wege udp, tcp (ICE-TCP) und ws (Medien-Tunnel saga-tunnel.1 ueber die
// Saga-Adresse, mit Quittungen wie die Apps, wahlweise eigenes Fenster), optional mit echtem Frame-E2E saga-horn/v1,
// Pegel-Skripten (Sprechmuster) und Kennzahlen als JSON. Die Saga-Seite (Konten, Geraete, Anruf, Beitritt, /horn,
// versiegelte fkey/fkey-ack/state) fuehrt tools/perf/horn_last.py; dieser Prozess macht nur Medien.
//
// Steuerung: eine JSON-Zeile je Befehl auf stdin, Ereignisse als JSON-Zeilen auf stdout (Fehler/Log auf stderr):
//   {"op":"add","id":"d1","cand":{...Kandidat aus call.horn.candidates...},"path":"ws|tcp|udp","video":true,
//    "tiles":4,"device":"desktop","script":"still|haupt|einwurf|gleichzeitig|gemurmel","e2e":true,"ip":"198.18.0.7"}
//       ip: eigene Client-Adresse (X-Forwarded-For; nur hinter einem vertrauten Proxy wirksam), sonst alle eine Adresse
//       mic:false: kein Ton senden (Begleitgeraet = zweites Geraet eines Kontos, sein Token erlaubt kein "a")
//       cand.node: Knoten - fuer die Zeitcode-Latenz Bot -> Bot getrennt nach gleichem Knoten / ueber Bruecke
//       -> {"ev":"up","id","ms","csrc"} bzw. {"ev":"err","id","msg"}; danach {"ev":"first","id","kind","ms"}
//   {"op":"own","id","n","key":"<B64 32 Byte>","after_ms":1500}   eigener Schluessel (sofort bzw. nach after_ms)
//   {"op":"switch","id"}                                         wartenden Schluessel sofort (alle haben bestaetigt)
//   {"op":"key","id","from":"<geraet>","refs":["<hp>",...],"n","key","s"}  Schluessel eines anderen Geraets (fkey);
//       refs = seine Kennungen am Knoten (hp, bei Kaskade hpn je Knoten) - CSRC -> Kennung -> Geraet
//   {"op":"script","id","script":"haupt"}                        Sprechmuster wechseln
//   {"op":"genwait","tag","t0":<unix ms>,"want":{"<geraet>":n,...},"timeout_ms":10000}
//       -> {"ev":"genwait","tag","pairs","done","hidden","p50_ms","p95_ms","max_ms"}: bis alle Bots die neue Generation
//          der Sender aufmachen (Zeit bis zum fkey-Wechsel nach einem Abgang); pairs nur, wo der Empfaenger den Sender
//          sieht (seit t0 - 2 s ein Rahmen aufgegangen), die anderen als hidden
//   Nachforderung: {"ev":"need","id","from":"<geraet>","n"} - Sender liefert seit 2 s nur Unlesbares (fkey-ack ok:false)
//   {"op":"leave","id"} -> {"ev":"stats",...,"final":true}  ·  {"op":"stats"} -> je Bot "stats" + {"ev":"sum",...}
//   {"op":"quit"}
// Kennzahlen je Bot ("stats"): up_ms (Beine verbunden), first_audio_ms/first_video_ms (erster aufgegangener Ton/Bild ab
// "add"), rx_pkts/rx_lost/loss_pct, freezes je Platzart (s Sprecher, t Kachel; raw = nach Ankunft, jb = mit festem
// Jitter-Puffer-Ziel), e2e (ok, nokey, auth, replay, format, short, nomap = Absender noch unbekannt, incomplete = Rahmen
// mit Luecke; tx_sealed, tx_nokey), aswitch_rx (Absenderwechsel auf den eigenen Ton-Plaetzen), tunnel (App-Seite) und mit
// "stats" legs (Knotensicht je Bein: Weg, Tunnel-Fenster/RTT, fwd_p95_us, aswitch falls der Knoten ihn meldet).
// "sum" zusaetzlich speak_switch_ms: Sprechbeginn eines Bots bis sein Ton bei den anderen Bots auf einem Platz ankommt,
// lat_same_ms / lat_bridge_ms (2438): Bild-Laufzeit Bot -> Bot aus dem Zeitcode (Sendezeit im synthetischen Bild, nach
// dem Oeffnen gelesen; nur mit Frame-E2E), getrennt nach gleichem Knoten bzw. anderem Knoten (Kaskade ueber Bruecke).
// Sprechmuster (Pegel -dBov je 100 ms, <= 55 zaehlt im Knoten als Sprache): still (127), haupt (3-8 s reden, 0,3-0,8 s
// Pause), einwurf (alle 6-14 s 0,8-2,5 s), gleichzeitig (4-10 s reden, 0,2-0,5 s Pause - "4 gleichzeitig" = 4 Bots damit),
// gemurmel (zufaellig um die Sprachschwelle). Ton = gueltiges Opus (Stille, 120 Byte je 40 ms), Bild = synthetisches VP8
// (Simulcast l/m[/h], zeitliche Schichten, nicht dekodierbar - Browser zeigen es nicht).
// Schalter: -resolve name=ip[,name=ip] (Saga-/Knotennamen), -insecure (selbst signierte Zertifikate), -all-ips (ICE
// nicht nur ueber Loopback), -fc (Fenster auch beim Senden der App), -jitter <ms>, -out <datei> (letzte Zusammenfassung).

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math/rand"
	"net"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/wstun"
)

// sfuBotAllIPs: ICE auch ueber andere Adressen als Loopback (horn-bot -all-ips, z. B. Knoten auf einer veth-Adresse).
var sfuBotAllIPs bool

type hbCand struct {
	Node   string            `json:"node"`
	URL    string            `json:"url"`
	Peer   string            `json:"peer"`
	Tokens map[string]string `json:"tokens"`
}

type hbCmd struct {
	Op      string         `json:"op"`
	ID      string         `json:"id"`
	Cand    hbCand         `json:"cand"`
	Path    string         `json:"path"`
	Video   bool           `json:"video"`
	Tiles   int            `json:"tiles"`
	Device  string         `json:"device"`
	Script  string         `json:"script"`
	E2E     bool           `json:"e2e"`
	N       int            `json:"n"`
	Key     string         `json:"key"`
	S       string         `json:"s"`
	From    string         `json:"from"`
	Refs    []string       `json:"refs"`
	AfterMs int            `json:"after_ms"`
	Tag     string         `json:"tag"`
	T0      int64          `json:"t0"`
	Want    map[string]int `json:"want"`
	Timeout int            `json:"timeout_ms"`
	IP      string         `json:"ip"`  // Adresse des Geraets als X-Forwarded-For (hinter dem vertrauten Proxy)
	Mic     *bool          `json:"mic"` // false: kein Ton senden (Begleitgeraet - zweites Geraet eines Kontos, Token ohne "a")
}

type hornRun struct {
	hc       *http.Client
	dialer   func(ctx context.Context, network, addr string) (net.Conn, error)
	tlsc     *tls.Config
	fc       bool
	outMu    sync.Mutex
	out      io.Writer
	mu       sync.Mutex
	bots     map[string]*hBot
	gone     []map[string]any // Kennzahlen gegangener Bots
	onsets   map[uint32]time.Time
	switchMs []float64 // Zeit vom Sprechbeginn bis zum Ton-Platz bei den anderen
	lastSum  map[string]any
	latMu    sync.Mutex
	lat      [2][]float64 // Bild-Zeitcode Bot -> Bot in ms: [0] gleicher Knoten, [1] ueber Bruecke (anderer Knoten)
}

// latMax: hoechstens so viele Zeitcode-Proben je Art (danach jede zweite verworfen - Speicher bleibt begrenzt).
const latMax = 200000

type hBot struct {
	run        *hornRun
	id         string
	node       string // Knoten (Kandidat node) - Zeitcode-Latenz gleicher Knoten / ueber Bruecke
	sb         *sfuBot
	path       string
	t0         time.Time
	upMs       int64
	firstA     int64
	firstV     int64
	script     string
	stopS      chan struct{}
	scriptStop chan struct{}
	ready      bool
	base       string // Adresse des Knotens (Kandidat url) fuer GET /api/sfu/leg/{id}
	hc         *http.Client
	hdr        http.Header // X-Forwarded-For je Bot (eigene Client-Adresse wie echte Geraete)
	tuns       []*wstun.Conn
	evc        *websocket.Conn
	mu         sync.Mutex
	refs       map[uint32]string // CSRC -> Kennung (Ereigniskanal + POST /leg)
	alias      map[string]string // Kennung am Knoten -> Geraet (aus "key": eine Kennung je Knoten, Kaskade)
	aswRx      int               // Wechsel der Absender auf den Ton-Plaetzen (Empfang)
	onSeen     map[uint32]time.Time
	bad        map[string][2]time.Time // Sender -> (seit wann nur Unlesbares, letzte Nachforderung)
	closed     bool
	err        string
}

func (r *hornRun) emit(m map[string]any) {
	b, _ := json.Marshal(m)
	r.outMu.Lock()
	_, _ = r.out.Write(append(b, '\n'))
	r.outMu.Unlock()
}

func runHornBot(args []string) error {
	fs := flag.NewFlagSet("horn-bot", flag.ContinueOnError)
	resolve := fs.String("resolve", "", "name=ip[,name=ip]: Namen der Saga-Adresse bzw. Knoten auf diese Adressen")
	insecure := fs.Bool("insecure", false, "selbst signierte Zertifikate annehmen (nur Pruefstand)")
	allIPs := fs.Bool("all-ips", false, "ICE nicht nur ueber Loopback")
	fc := fs.Bool("fc", false, "Weg ws: auch die App begrenzt ihre Sendungen nach Quittungen (Fenster)")
	jitter := fs.Int("jitter", int(JitterTarget/time.Millisecond), "Jitter-Puffer-Ziel der Einfrier-Messung (ms)")
	outFile := fs.String("out", "", "letzte Zusammenfassung (JSON) zusaetzlich in diese Datei")
	if err := fs.Parse(normFlags(args)); err != nil {
		return err
	}
	sfuBotAllIPs = *allIPs
	JitterTarget = time.Duration(*jitter) * time.Millisecond
	res := map[string]string{}
	for _, kv := range strings.Split(*resolve, ",") {
		if k, v, ok := strings.Cut(strings.TrimSpace(kv), "="); ok {
			res[strings.ToLower(k)] = v
		}
	}
	r := newHornRun(os.Stdout, res, *insecure, *fc)
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<20), 1<<22)
	for sc.Scan() {
		var c hbCmd
		if err := json.Unmarshal(sc.Bytes(), &c); err != nil {
			r.emit(map[string]any{"ev": "err", "msg": "json: " + err.Error()})
			continue
		}
		if c.Op == "quit" {
			break
		}
		r.handle(c)
	}
	r.mu.Lock()
	bots := make([]*hBot, 0, len(r.bots))
	for _, b := range r.bots {
		bots = append(bots, b)
	}
	r.mu.Unlock()
	for _, b := range bots {
		r.leave(b)
	}
	sum := r.summary()
	r.emit(sum)
	if *outFile != "" {
		bs, _ := json.MarshalIndent(sum, "", " ")
		if err := os.WriteFile(*outFile, bs, 0o644); err != nil {
			return err
		}
	}
	return nil
}

// newHornRun: Lauf mit eigenem HTTP-Client (Namen aufloesen, ohne Proxy) und Ereignis-Ausgabe.
func newHornRun(out io.Writer, res map[string]string, insecure, fc bool) *hornRun {
	nd := &net.Dialer{Timeout: 10 * time.Second}
	dial := func(ctx context.Context, network, addr string) (net.Conn, error) {
		if h, p, err := net.SplitHostPort(addr); err == nil {
			if ip, ok := res[strings.ToLower(h)]; ok {
				addr = net.JoinHostPort(ip, p)
			}
		}
		return nd.DialContext(ctx, network, addr)
	}
	tlsc := &tls.Config{InsecureSkipVerify: insecure, NextProtos: []string{"http/1.1"}} // #nosec G402 - nur mit -insecure
	r := &hornRun{dialer: dial, tlsc: tlsc, fc: fc, out: out, bots: map[string]*hBot{}, onsets: map[uint32]time.Time{}}
	r.hc = &http.Client{Timeout: 20 * time.Second, Transport: &http.Transport{Proxy: nil, TLSClientConfig: tlsc, DialContext: dial,
		MaxIdleConnsPerHost: 64}}
	return r
}

func (r *hornRun) bot(id string) *hBot {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.bots[id]
}

func (r *hornRun) handle(c hbCmd) {
	b := r.bot(c.ID)
	bad := func(msg string) { r.emit(map[string]any{"ev": "err", "id": c.ID, "op": c.Op, "msg": msg}) }
	switch c.Op {
	case "add":
		if b != nil {
			bad("schon da")
			return
		}
		b = &hBot{run: r, id: c.ID, node: c.Cand.Node, path: c.Path, t0: time.Now(), refs: map[uint32]string{}, alias: map[string]string{}, bad: map[string][2]time.Time{}, onSeen: map[uint32]time.Time{}, stopS: make(chan struct{})}
		r.mu.Lock()
		r.bots[c.ID] = b
		r.mu.Unlock()
		go b.start(c)
	case "own", "key":
		if b == nil || b.sb == nil {
			bad("unbekannt")
			return
		}
		k, err := base64.StdEncoding.DecodeString(c.Key)
		if err != nil || len(k) != 32 || c.N < 0 {
			bad("schluessel")
			return
		}
		if c.Op == "own" {
			if b.sb.e2eTx != nil {
				b.sb.e2eTx.set(c.N, k, time.Duration(c.AfterMs)*time.Millisecond)
			}
		} else if b.sb.e2eRx != nil {
			b.mu.Lock()
			for _, ref := range c.Refs {
				b.alias[ref] = c.From
			}
			b.mu.Unlock()
			b.sb.e2eRx.setKey(c.From, c.N, k, c.S)
		}
	case "switch":
		if b != nil && b.sb != nil && b.sb.e2eTx != nil {
			b.sb.e2eTx.switchNow()
		}
	case "script":
		if b == nil {
			bad("unbekannt")
			return
		}
		b.setScript(c.Script)
	case "genwait":
		go r.genWait(c)
	case "leave":
		if b == nil {
			bad("unbekannt")
			return
		}
		go r.leave(b)
	case "stats":
		r.mu.Lock()
		bots := make([]*hBot, 0, len(r.bots))
		for _, x := range r.bots {
			bots = append(bots, x)
		}
		r.mu.Unlock()
		var wg sync.WaitGroup
		for _, x := range bots {
			wg.Add(1)
			go func() {
				defer wg.Done()
				st := x.stats(false)
				st["legs"] = x.legDiag()
				r.emit(st)
			}()
		}
		wg.Wait()
		r.emit(r.summary())
	default:
		bad("unbekannter Befehl")
	}
}

// start: Beine aufbauen (Kandidat von Saga), Ereigniskanal, Sprechmuster.
func (b *hBot) start(c hbCmd) {
	r := b.run
	fail := func(err error) {
		b.mu.Lock()
		b.err = err.Error()
		b.mu.Unlock()
		r.emit(map[string]any{"ev": "err", "id": b.id, "msg": err.Error()})
	}
	path := c.Path
	if path == "" {
		path = "ws"
	}
	api, err := sfuBotAPI(path)
	if err != nil {
		fail(err)
		return
	}
	layers := []string{"l", "m"}
	if path == "udp" {
		layers = []string{"l", "m", "h"}
	}
	sb := &sfuBot{peer: c.ID, path: path, video: c.Video, layers: layers, toks: c.Cand.Tokens, noMic: c.Mic != nil && !*c.Mic}
	sb.level.Store(127)
	if c.E2E {
		sb.e2eTx, sb.e2eRx = newE2ESender(), newE2ERecv()
	}
	sb.refOf = b.refOf
	sb.onFirst = func(kind string) {
		ms := time.Since(b.t0).Milliseconds()
		b.mu.Lock()
		if kind == "audio" {
			b.firstA = ms
		} else {
			b.firstV = ms
		}
		b.mu.Unlock()
		r.emit(map[string]any{"ev": "first", "id": b.id, "kind": kind, "ms": ms})
	}
	sb.onASlot = b.aslot
	sb.onRx = b.rxNote
	sb.onLat = b.latNote
	base := strings.TrimRight(c.Cand.URL, "/")
	b.base = base
	hc, hdr := r.hc, http.Header{}
	if c.IP != "" {
		hdr.Set("X-Forwarded-For", c.IP)
		hc = &http.Client{Timeout: r.hc.Timeout, Transport: hdrRT{r.hc.Transport, c.IP}}
	}
	b.mu.Lock()
	b.hc, b.hdr = hc, hdr
	b.mu.Unlock()
	if path == "ws" {
		var cr struct{ Username, Credential string }
		if err := postJSON(hc, base+"/api/sfu/turn", map[string]string{"token": c.Cand.Tokens["audio"]}, &cr); err != nil {
			fail(fmt.Errorf("TURN-Zugang: %w", err))
			return
		}
		wsURL := wsOf(base) + "/api/sfu/ws"
		var terr error
		sb.bend = func(role, sdp string) string {
			port, tc, err := b.tunnel(wsURL, cr.Username, cr.Credential, role)
			if err != nil {
				terr = err
				return sdp
			}
			b.mu.Lock()
			b.tuns = append(b.tuns, tc)
			b.mu.Unlock()
			return bendTo(sdp, port)
		}
		defer func() {
			if terr != nil {
				fail(fmt.Errorf("Tunnel: %w", terr))
			}
		}()
	}
	b.mu.Lock()
	b.sb = sb
	b.mu.Unlock()
	tiles := c.Tiles
	if tiles <= 0 {
		tiles = 4
	}
	dev := c.Device
	if dev == "" {
		dev = "desktop"
	}
	if err := sb.join(hc, base, api, "", tiles, dev); err != nil {
		fail(fmt.Errorf("Beitritt: %w", err))
		return
	}
	want := 2
	if path == "udp" {
		want = 1
	}
	for i := 0; i < 300 && sb.connected() < want; i++ {
		time.Sleep(100 * time.Millisecond)
	}
	if sb.connected() < want {
		fail(fmt.Errorf("%d von %d Leitungen verbunden", sb.connected(), want))
		return
	}
	b.mu.Lock()
	b.ready = true
	b.upMs = time.Since(b.t0).Milliseconds()
	for ref, cs := range sb.peers {
		b.refs[cs] = ref
	}
	b.mu.Unlock()
	sb.start()
	r.emit(map[string]any{"ev": "up", "id": b.id, "ms": b.upMs, "csrc": sb.csrc, "legs": sb.legIDs})
	if len(sb.legIDs) > 0 {
		go b.events(base, sb.legIDs[0])
	}
	b.mu.Lock()
	sc := b.script
	b.mu.Unlock()
	if sc == "" {
		sc = c.Script
	}
	b.setScript(sc)
}

// tunnel: lokaler Lauscher (eine Verbindung) -> WebSocket saga-tunnel.1 mit auth und Quittungen (wie die Apps).
func (b *hBot) tunnel(wsURL, user, cred, role string) (int, *wstun.Conn, error) {
	r := b.run
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, nil, err
	}
	d := websocket.Dialer{Subprotocols: []string{wstun.Subprotocol}, TLSClientConfig: r.tlsc, NetDialContext: r.dialer,
		HandshakeTimeout: 10 * time.Second, Proxy: nil}
	ws, resp, err := d.Dial(wsURL, b.hdr)
	if err != nil {
		ln.Close()
		if resp != nil {
			return 0, nil, fmt.Errorf("%v (%d)", err, resp.StatusCode)
		}
		return 0, nil, err
	}
	rep, err := wstun.ClientAuth(ws, user, cred, role, 10*time.Second)
	if err != nil {
		ln.Close()
		ws.Close()
		return 0, nil, err
	}
	c := wstun.New(ws, nil, nil, wstun.Options{FC: r.fc, Ack: true, AckBytes: rep.AckBytes, AckDelay: time.Duration(rep.AckMs) * time.Millisecond})
	go func() {
		lc, err := ln.Accept()
		ln.Close()
		if err != nil {
			c.Close()
			return
		}
		go func() { _, _ = io.Copy(lc, c); lc.Close() }()
		_, _ = io.Copy(c, lc)
		c.Close()
	}()
	go func() { <-b.stopS; ln.Close(); c.Close() }()
	return ln.Addr().(*net.TCPAddr).Port, c, nil
}

// events: Ereigniskanal des ersten Beins (peers: Kennung -> CSRC, closed).
func (b *hBot) events(base, leg string) {
	r := b.run
	u := wsOf(base) + "/api/sfu/leg/" + url.PathEscape(leg) + "/events"
	d := websocket.Dialer{TLSClientConfig: r.tlsc, NetDialContext: r.dialer, HandshakeTimeout: 10 * time.Second, Proxy: nil}
	ws, _, err := d.Dial(u, b.hdr)
	if err != nil {
		r.emit(map[string]any{"ev": "warn", "id": b.id, "msg": "Ereigniskanal: " + err.Error()})
		return
	}
	b.mu.Lock()
	b.evc = ws
	b.mu.Unlock()
	defer ws.Close()
	for {
		var m struct {
			T     string            `json:"t"`
			Peers map[string]uint32 `json:"peers"`
			Why   string            `json:"why"`
		}
		if err := ws.ReadJSON(&m); err != nil {
			return
		}
		switch m.T {
		case "hello", "peers":
			b.mu.Lock()
			for ref, cs := range m.Peers {
				b.refs[cs] = ref
			}
			b.mu.Unlock()
		case "closed":
			r.emit(map[string]any{"ev": "down", "id": b.id, "why": m.Why})
		}
	}
}

// bendTo: passiver TCP-Kandidat im Antwort-SDP -> 127.0.0.1:<port> (wie HornTunnel.bend in den Apps; Weg ws).
func bendTo(sdp string, port int) string {
	lines := strings.Split(sdp, "\r\n")
	for i, l := range lines {
		f := strings.Fields(l)
		if strings.HasPrefix(l, "a=candidate:") && len(f) >= 8 && strings.EqualFold(f[2], "tcp") && f[7] == "host" && strings.Contains(l, "tcptype passive") {
			f[4], f[5] = "127.0.0.1", strconv.Itoa(port)
			lines[i] = strings.Join(f, " ")
		}
	}
	return strings.Join(lines, "\r\n")
}

// hdrRT setzt X-Forwarded-For je Bot (der vertraute Proxy haengt seine Gegenstelle an; Saga/Knoten nehmen die
// rechteste nicht vertraute Adresse - wie bei echten Geraeten hinter verschiedenen Anschluessen).
type hdrRT struct {
	rt http.RoundTripper
	ip string
}

func (h hdrRT) RoundTrip(r *http.Request) (*http.Response, error) {
	r = r.Clone(r.Context())
	r.Header.Set("X-Forwarded-For", h.ip)
	return h.rt.RoundTrip(r)
}

// wsOf: https -> wss, http -> ws (Pruefstand ohne TLS, wie hinter saga-media).
func wsOf(base string) string {
	if strings.HasPrefix(base, "http://") {
		return "ws://" + strings.TrimPrefix(base, "http://")
	}
	return "wss://" + strings.TrimPrefix(base, "https://")
}

func (b *hBot) isReady() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.ready
}

func (b *hBot) refOf(c uint32) string {
	b.mu.Lock()
	defer b.mu.Unlock()
	ref := b.refs[c]
	if d, ok := b.alias[ref]; ok {
		return d
	}
	return ref
}

// aslot: Ton-Platz zeigt einen neuen Absender - zaehlen und, wenn der Absender ein Bot dieses Prozesses ist, der gerade
// zu sprechen begann, die Wechselzeit messen.
func (b *hBot) aslot(c uint32) {
	r := b.run
	now := time.Now()
	b.mu.Lock()
	b.aswRx++
	b.mu.Unlock()
	r.mu.Lock()
	on, ok := r.onsets[c]
	r.mu.Unlock()
	if !ok || now.Sub(on) > 5*time.Second {
		return
	}
	b.mu.Lock()
	seen := b.onSeen[c].Equal(on)
	b.onSeen[c] = on
	early := !b.ready || on.Before(b.t0.Add(time.Duration(b.upMs)*time.Millisecond)) // Sprechbeginn vor dem eigenen Beitritt: Beitrittszeit, keine Wechselzeit
	b.mu.Unlock()
	if !seen && !early {
		r.mu.Lock()
		r.switchMs = append(r.switchMs, float64(now.Sub(on).Microseconds())/1000)
		r.mu.Unlock()
	}
}

// rxNote: Nachforderung wie Web/Apps (HORN-E2E §4): liefert ein Sender seit 2 s nur Unlesbares (kein Schluessel), meldet
// der Bot "need" (hoechstens alle 5 s je Sender); horn_last.py schickt dann fkey-ack ok:false, der Sender antwortet mit
// seinem neuesten Schluessel. Noetig z. B. wenn ein Sender lange nicht auf den eigenen Plaetzen war und inzwischen mehr
// als 4 Ratschen weiter ist.
func (b *hBot) rxNote(from string, ok bool) {
	now := time.Now()
	b.mu.Lock()
	x, had := b.bad[from]
	switch {
	case ok:
		if had {
			delete(b.bad, from)
		}
		b.mu.Unlock()
		return
	case !had || x[0].IsZero():
		b.bad[from] = [2]time.Time{now, x[1]}
		b.mu.Unlock()
		return
	case now.Sub(x[0]) < 2*time.Second || now.Sub(x[1]) < 5*time.Second:
		b.mu.Unlock()
		return
	}
	b.bad[from] = [2]time.Time{x[0], now}
	sb := b.sb
	b.mu.Unlock()
	n := -1
	if sb != nil && sb.e2eRx != nil {
		n = sb.e2eRx.newest(from)
	}
	b.run.emit(map[string]any{"ev": "need", "id": b.id, "from": from, "n": n})
}

// --- Sprechmuster (Pegel -dBov je 100 ms; <= 55 zaehlt im Knoten als Sprache, 127 = still) -------------------------

func (b *hBot) setScript(s string) {
	b.mu.Lock()
	if b.closed {
		b.mu.Unlock()
		return
	}
	if b.scriptStop != nil {
		close(b.scriptStop)
		b.scriptStop = nil
	}
	b.script = s
	sb := b.sb
	if sb == nil || !b.ready {
		b.mu.Unlock() // vor dem Beitritt: nur merken, start() setzt es
		return
	}
	stop := make(chan struct{})
	b.scriptStop = stop
	b.mu.Unlock()
	go b.speak(s, sb, stop)
}

func (b *hBot) speak(script string, sb *sfuBot, stop chan struct{}) {
	rng := rand.New(rand.NewSource(time.Now().UnixNano() ^ int64(len(b.id))<<20 ^ int64(sb.csrc)))
	tick := time.NewTicker(100 * time.Millisecond)
	defer tick.Stop()
	talking := false
	var until time.Time
	between := func(a, z float64) time.Duration {
		return time.Duration((a + rng.Float64()*(z-a)) * float64(time.Second))
	}
	lvl := func(a, z int) uint32 { return uint32(a + rng.Intn(z-a+1)) }
	for {
		now := time.Now()
		var lv uint32 = 127
		switch script {
		case "haupt", "einwurf", "gleichzeitig":
			if now.After(until) {
				talking = !talking
				switch {
				case script == "haupt" && talking:
					until = now.Add(between(3, 8))
				case script == "haupt":
					until = now.Add(between(0.3, 0.8))
				case script == "einwurf" && talking:
					until = now.Add(between(0.8, 2.5))
				case script == "einwurf":
					until = now.Add(between(6, 14))
				case talking:
					until = now.Add(between(4, 10))
				default:
					until = now.Add(between(0.2, 0.5))
				}
				if talking && sb.csrc != 0 {
					b.run.mu.Lock()
					b.run.onsets[sb.csrc] = now
					b.run.mu.Unlock()
				}
			}
			if talking {
				switch script {
				case "haupt":
					lv = lvl(18, 28)
				case "einwurf":
					lv = lvl(24, 32)
				default:
					lv = lvl(22, 30)
				}
			}
		case "gemurmel": // leises Durcheinander: um die Sprachschwelle, ohne lange Saetze
			if rng.Float64() < 0.4 {
				lv = lvl(48, 58)
			} else {
				lv = lvl(70, 90)
			}
		}
		sb.level.Store(lv)
		select {
		case <-stop:
			sb.level.Store(127)
			return
		case <-tick.C:
		}
	}
}

// --- Ende, Kennzahlen ---------------------------------------------------------------------------------------------

func (r *hornRun) leave(b *hBot) {
	b.mu.Lock()
	if b.closed {
		b.mu.Unlock()
		return
	}
	b.closed = true
	if b.scriptStop != nil {
		close(b.scriptStop)
		b.scriptStop = nil
	}
	evc := b.evc
	b.mu.Unlock()
	st := b.stats(true)
	if b.sb != nil {
		b.sb.close()
	}
	close(b.stopS)
	if evc != nil {
		evc.Close()
	}
	r.mu.Lock()
	delete(r.bots, b.id)
	r.gone = append(r.gone, st)
	r.mu.Unlock()
	r.emit(st)
}

func (b *hBot) stats(final bool) map[string]any {
	b.mu.Lock()
	sb := b.sb
	m := map[string]any{"ev": "stats", "id": b.id, "path": b.path, "secs": round(time.Since(b.t0).Seconds(), 1), "up_ms": b.upMs,
		"first_audio_ms": b.firstA, "first_video_ms": b.firstV, "aswitch_rx": b.aswRx, "err": b.err, "final": final}
	var tun []wstun.Snapshot
	for _, t := range b.tuns {
		tun = append(tun, t.Snap())
	}
	b.mu.Unlock()
	if sb == nil {
		return m
	}
	pk, lost := sb.rxPkts.Load(), sb.rxLost.Load()
	m["rx_pkts"], m["rx_lost"] = pk, lost
	if pk+lost > 0 {
		m["loss_pct"] = round(100*float64(lost)/float64(pk+lost), 3)
	}
	fz := map[string]any{}
	for cls, v := range sb.freezeByClass() {
		fz[string([]byte{cls})] = map[string]int{"raw": v[0], "jb": v[1], "tracks": v[2]}
	}
	_, _, frames := sb.freezes()
	m["freezes"], m["frames"], m["legs_up"], m["csrc"] = fz, frames, sb.connected(), sb.csrc
	sb.mu.Lock()
	e := map[string]uint64{}
	for k, v := range sb.e2eSt {
		e[k] = v
	}
	sb.mu.Unlock()
	if sb.e2eTx != nil {
		sb.e2eTx.mu.Lock()
		e["tx_sealed"], e["tx_nokey"] = sb.e2eTx.sealed, sb.e2eTx.dropped
		sb.e2eTx.mu.Unlock()
		m["e2e"] = e
		m["key_n"] = sb.e2eTx.gen()
	}
	if len(tun) > 0 {
		m["tunnel"] = tun
	}
	return m
}

// legDiag: Sicht des Knotens je Bein (GET /api/sfu/leg/{id}): Weg, gesendet, Tunnel-Fenster/RTT, Platzwechsel Ton
// (aswitch, 2263-3 - fehlt bei aelteren Knoten).
func (b *hBot) legDiag() []map[string]any {
	b.mu.Lock()
	sb, hc := b.sb, b.hc
	b.mu.Unlock()
	if sb == nil || hc == nil || !b.isReady() {
		return nil
	}
	var out []map[string]any
	for _, id := range sb.legIDs {
		var lay struct {
			Stats map[string]any `json:"stats"`
		}
		resp, err := hc.Get(b.base + "/api/sfu/leg/" + url.PathEscape(id))
		if err != nil {
			continue
		}
		_ = json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&lay)
		resp.Body.Close()
		m := map[string]any{"id": id}
		for _, k := range []string{"path", "role", "bytes_out", "frames_dropped", "stalls", "max_queue_ms", "fwd_p95_us", "tunnel", "aswitch"} {
			if v, ok := lay.Stats[k]; ok {
				m[k] = v
			}
		}
		out = append(out, m)
	}
	return out
}

func pctl(v []float64, q float64) float64 {
	if len(v) == 0 {
		return 0
	}
	s := append([]float64(nil), v...)
	sort.Float64s(s)
	i := int(q * float64(len(s)))
	if i >= len(s) {
		i = len(s) - 1
	}
	return round(s[i], 1)
}

// summary: Kennzahlen ueber alle Bots (laufende und gegangene).
func (r *hornRun) summary() map[string]any {
	r.mu.Lock()
	bots := make([]*hBot, 0, len(r.bots))
	for _, b := range r.bots {
		bots = append(bots, b)
	}
	gone := append([]map[string]any(nil), r.gone...)
	sw := append([]float64(nil), r.switchMs...)
	r.mu.Unlock()
	all := gone
	for _, b := range bots {
		all = append(all, b.stats(false))
	}
	var up, firstA, firstV, loss, fzMin, aswMin []float64
	errs := 0
	e2e := map[string]uint64{}
	var pk, lost uint64
	for _, s := range all {
		if s["err"] != "" {
			errs++
		}
		if v, _ := s["up_ms"].(int64); v > 0 {
			up = append(up, float64(v))
		}
		if v, _ := s["first_audio_ms"].(int64); v > 0 {
			firstA = append(firstA, float64(v))
		}
		if v, _ := s["first_video_ms"].(int64); v > 0 {
			firstV = append(firstV, float64(v))
		}
		if v, ok := s["loss_pct"].(float64); ok {
			loss = append(loss, v)
		}
		a, _ := s["rx_pkts"].(uint64)
		l, _ := s["rx_lost"].(uint64)
		pk, lost = pk+a, lost+l
		secs, _ := s["secs"].(float64)
		if secs >= 5 {
			n := 0
			if fz, ok := s["freezes"].(map[string]any); ok {
				for _, x := range fz {
					n += x.(map[string]int)["jb"]
				}
			}
			fzMin = append(fzMin, float64(n)/(secs/60))
			if v, ok := s["aswitch_rx"].(int); ok {
				aswMin = append(aswMin, float64(v)/(secs/60))
			}
		}
		if e, ok := s["e2e"].(map[string]uint64); ok {
			for k, v := range e {
				e2e[k] += v
			}
		}
	}
	m := map[string]any{"ev": "sum", "bots": len(all), "running": len(bots), "errors": errs,
		"up_ms":           map[string]float64{"p50": pctl(up, .5), "p95": pctl(up, .95)},
		"first_audio_ms":  map[string]float64{"p50": pctl(firstA, .5), "p95": pctl(firstA, .95), "n": float64(len(firstA))},
		"first_video_ms":  map[string]float64{"p50": pctl(firstV, .5), "p95": pctl(firstV, .95), "n": float64(len(firstV))},
		"loss_pct":        map[string]float64{"p50": pctl(loss, .5), "max": pctl(loss, 1)},
		"freezes_min":     map[string]float64{"p50": pctl(fzMin, .5), "max": pctl(fzMin, 1)},
		"aswitch_rx_min":  map[string]float64{"p50": pctl(aswMin, .5), "max": pctl(aswMin, 1)},
		"speak_switch_ms": map[string]float64{"p50": pctl(sw, .5), "p95": pctl(sw, .95), "n": float64(len(sw))},
		"rx_pkts":         pk, "rx_lost": lost}
	m["lat_same_ms"], m["lat_bridge_ms"] = r.latSum()
	if len(e2e) > 0 {
		m["e2e"] = e2e
	}
	r.mu.Lock()
	r.lastSum = m
	r.mu.Unlock()
	return m
}

// latNote: Zeitcode eines Bildes von from (Bot dieses Prozesses) - nach gleichem Knoten bzw. ueber Bruecke getrennt.
func (b *hBot) latNote(from string, ms float64) {
	r := b.run
	r.mu.Lock()
	snd := r.bots[from]
	r.mu.Unlock()
	if snd == nil || snd.node == "" || b.node == "" || ms < 0 || ms > 30000 {
		return
	}
	k := 0
	if snd.node != b.node {
		k = 1
	}
	r.latMu.Lock()
	if len(r.lat[k]) >= latMax {
		h := r.lat[k][:0]
		for i := 0; i < len(r.lat[k]); i += 2 {
			h = append(h, r.lat[k][i])
		}
		r.lat[k] = h
	}
	r.lat[k] = append(r.lat[k], ms)
	r.latMu.Unlock()
}

func (r *hornRun) latSum() (same, bridge map[string]float64) {
	r.latMu.Lock()
	defer r.latMu.Unlock()
	f := func(v []float64) map[string]float64 {
		return map[string]float64{"p50": pctl(v, .5), "p95": pctl(v, .95), "n": float64(len(v))}
	}
	return f(r.lat[0]), f(r.lat[1])
}

// genWait: Zeit vom Abgang (t0) bis jeder laufende Bot von jedem Sender in want eine Generation >= n aufmacht. Nur
// Paare, bei denen der Empfaenger den Sender sieht (seit t0 - 2 s ein Rahmen aufgegangen; Kachel-/Ton-Plaetze zeigen
// nicht jeden) - die anderen zaehlen als hidden.
func (r *hornRun) genWait(c hbCmd) {
	to := time.Duration(c.Timeout) * time.Millisecond
	if to <= 0 {
		to = 10 * time.Second
	}
	t0 := time.UnixMilli(c.T0)
	end := time.Now().Add(to)
	for {
		var lat []float64
		pairs, done, hidden := 0, 0, 0
		r.mu.Lock()
		bots := make([]*hBot, 0, len(r.bots))
		for _, b := range r.bots {
			bots = append(bots, b)
		}
		r.mu.Unlock()
		for _, b := range bots {
			if b.sb == nil || b.sb.e2eRx == nil {
				continue
			}
			for from, n := range c.Want {
				if from == b.id {
					continue
				}
				if at, ok := b.sb.e2eRx.firstAt(from, n); ok {
					pairs++
					done++
					lat = append(lat, float64(at.Sub(t0).Microseconds())/1000)
				} else if b.sb.e2eRx.seenSince(from, t0.Add(-2*time.Second)) {
					pairs++
				} else {
					hidden++
				}
			}
		}
		if done == pairs || time.Now().After(end) {
			r.emit(map[string]any{"ev": "genwait", "tag": c.Tag, "pairs": pairs, "done": done, "hidden": hidden, "p50_ms": pctl(lat, .5),
				"p95_ms": pctl(lat, .95), "max_ms": pctl(lat, 1)})
			return
		}
		time.Sleep(200 * time.Millisecond)
	}
}

// newest: neueste gehaltene Generation des Senders (-1 ohne).
func (r *e2eRecv) newest(from string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	if p := r.peers[from]; p != nil {
		return p.newest
	}
	return -1
}

// seenSince: ist seit t ein Rahmen des Senders from aufgegangen (der Empfaenger sieht ihn auf einem Platz)?
func (r *e2eRecv) seenSince(from string, t time.Time) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	p := r.peers[from]
	return p != nil && !p.last.Before(t)
}

// firstAt: erster aufgegangener Rahmen des Senders from mit Generation >= n.
func (r *e2eRecv) firstAt(from string, n int) (time.Time, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	p := r.peers[from]
	if p == nil {
		return time.Time{}, false
	}
	var best time.Time
	for g, t := range p.firstN {
		if g >= n && (best.IsZero() || t.Before(best)) {
			best = t
		}
	}
	return best, !best.IsZero()
}
