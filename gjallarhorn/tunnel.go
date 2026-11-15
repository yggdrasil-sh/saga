package main

// Medien-Tunnel ueber WebSocket (Saga 2.5, A3 "ICE-TCP im WebSocket-Tunnel", Vertrag vertrag-a3.md S1-S6,
// docs/APP-API.md "Medien-Tunnel /api/sfu/ws"): Apps hinter einem TLS-beendenden Proxy (NPM mit "Websockets Support")
// reichen den ICE-TCP-Strom einer Leitung als WebSocket an die normale Saga-Adresse. saga-media gibt ihn roh an das
// interne HTTP des eingebauten Knotens (node.sock), der Knoten speist ihn in seine ICE-TCP-Weiche (netx.Mux.Inject).
//
// GET /api/sfu/ws, Unterprotokoll saga-tunnel.1 (saga-frames.1 ist fuer den Browser-Weg B2 reserviert, bis dahin 400):
//   - ohne Upgrade (Proxy reicht WebSocket nicht durch) -> 426 mit Hinweis, vor jeder anderen Pruefung
//   - Grenze je echter Client-Adresse 4 x HORN_MAX_PER_IP (Ton + Bild + Vorrat je Geraet), gesamt 2 x HORN_MAX_LEGS + 16
//   - Zugang in der ersten Textnachricht (TURN-Zugangsdaten aus POST /api/sfu/turn wie fuer TURN-TLS) - nie in URL,
//     Query oder Kopf (NPM protokolliert $request)
//   - Fenster nur auf dieser Seite (Downlink), die App quittiert (internal/wstun)

import (
	"crypto/hmac"
	"crypto/sha1"
	"encoding/base64"
	"encoding/json"
	"net"
	"net/http"
	"net/netip"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/wstun"
)

// Fristen des Tunnels (Tests aendern sie).
var (
	tunnelAuthWait  = 10 * time.Second // erste Textnachricht
	tunnelFirstWait = 30 * time.Second // erstes Datenbyte nach ok (vorab geoeffneter WebSocket, Vertrag 7)
	tunnelPing      = 25 * time.Second
)

type tunnels struct {
	mu    sync.Mutex
	perIP map[string]int
	n     int // alle Tunnel-WS (auch noch ohne auth)
	open  map[uint64]*tunnelInfo
	seq   atomic.Uint64
	vport atomic.Uint32

	total, authFail, noUpgrade, limited atomic.Uint64
	lastOpen, lastNoUpgrade             atomic.Int64 // Unix-Sekunden
}

type tunnelInfo struct {
	at   time.Time
	role string
	c    *wstun.Conn
}

func newTunnels() *tunnels {
	return &tunnels{perIP: map[string]int{}, open: map[uint64]*tunnelInfo{}}
}

// tunnelUserOK: TURN-Zugangsdaten (pion turn.GenerateLongTermCredentials: user = "<Ablauf>", cred = base64(HMAC-SHA1)).
func tunnelUserOK(secret, user, cred string) bool {
	exp, err := strconv.ParseInt(strings.SplitN(user, ":", 2)[0], 10, 64)
	if err != nil || time.Now().Unix() > exp {
		return false
	}
	m := hmac.New(sha1.New, []byte(secret))
	m.Write([]byte(user))
	want := base64.StdEncoding.EncodeToString(m.Sum(nil))
	return hmac.Equal([]byte(want), []byte(cred))
}

func hasSubprotocol(r *http.Request, p string) bool {
	for _, v := range websocket.Subprotocols(r) {
		if v == p {
			return true
		}
	}
	return false
}

// tunnelNoUpgradeText: Hinweis fuer Menschen (Admin-Pruefung, Logs der Apps); Apps werten "error" aus.
const tunnelNoUpgradeText = "Der Reverse Proxy reicht WebSocket nicht durch. Bei Nginx Proxy Manager \"Websockets Support\" einschalten."

func (s *server) apiSFUWS(w http.ResponseWriter, r *http.Request) {
	t := s.tun
	if !websocket.IsWebSocketUpgrade(r) { // Vertrag 2: vor jeder anderen Pruefung
		t.noUpgrade.Add(1)
		t.lastNoUpgrade.Store(time.Now().Unix())
		w.Header().Set("Upgrade", "websocket")
		writeJSON(w, http.StatusUpgradeRequired, map[string]string{"error": "websocket_required", "text": tunnelNoUpgradeText})
		return
	}
	if !hasSubprotocol(r, wstun.Subprotocol) { // saga-frames.1 (B2) kommt spaeter
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "subprotocol", "supported": []string{wstun.Subprotocol}})
		return
	}
	ip := ipKey(r)
	t.mu.Lock()
	if t.perIP[ip] >= 4*s.cfg.maxPerIP() || t.n >= 2*s.cfg.MaxLegs+16 {
		t.mu.Unlock()
		t.limited.Add(1)
		writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": "limit"})
		return
	}
	t.perIP[ip]++
	t.n++
	t.mu.Unlock()
	release := func() {
		t.mu.Lock()
		t.n--
		if t.perIP[ip]--; t.perIP[ip] <= 0 {
			delete(t.perIP, ip)
		}
		t.mu.Unlock()
	}
	up := websocket.Upgrader{ReadBufferSize: 16 << 10, WriteBufferSize: 16 << 10, Subprotocols: []string{wstun.Subprotocol},
		HandshakeTimeout: 10 * time.Second, EnableCompression: false,
		// Jede Herkunft: Der Tunnel nutzt keine Cookies oder andere mitgeschickte Berechtigungen - der Zugang haengt allein
		// an den TURN-Zugangsdaten der ersten Nachricht (eine fremde Seite erreicht ohne sie nichts, mit ihnen nicht mehr
		// als jedes Programm). Apps schicken keinen Origin; die Pruefung im Admin-Bereich kommt von dessen Herkunft
		// (ADMIN_PORT, oft ein anderer Name als die Saga-Seiten).
		CheckOrigin: func(*http.Request) bool { return true }}
	ws, err := up.Upgrade(w, r, nil)
	if err != nil {
		release()
		return
	}
	if tc, ok := ws.UnderlyingConn().(*net.TCPConn); ok { // eigener Port (gekoppelter Knoten, eigener Name)
		_ = tc.SetNoDelay(true)
	}
	ws.SetReadLimit(wstun.MaxPayload + 64)
	_ = ws.SetReadDeadline(time.Now().Add(tunnelAuthWait))
	mt, b, err := ws.ReadMessage()
	_ = ws.SetReadDeadline(time.Time{})
	if err != nil {
		code, cc := "bad", wstun.CloseBad
		if ne, ok := err.(net.Error); ok && ne.Timeout() {
			code, cc = "timeout", wstun.CloseTimeout
		}
		_ = wstun.WriteErr(ws, code, cc)
		_ = ws.Close()
		release()
		return
	}
	a, code, cc := wstun.ParseAuth(mt, b)
	if code == "" && !tunnelUserOK(s.turnSecret, a.User, a.Cred) {
		code, cc = "auth", wstun.CloseAuth
	}
	if code == "" && (s.draining.Load() || s.closing.Load()) {
		code, cc = "unavailable", wstun.CloseUnavailable
	}
	if code != "" {
		if code == "auth" {
			t.authFail.Add(1)
		}
		_ = wstun.WriteErr(ws, code, cc)
		_ = ws.Close()
		release()
		return
	}
	ok, _ := json.Marshal(wstun.Reply{T: "ok", V: wstun.Version, AckBytes: wstun.DefAckBytes, AckMs: int(wstun.DefAckDelay / time.Millisecond)})
	_ = ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
	if err := ws.WriteMessage(websocket.TextMessage, ok); err != nil {
		_ = ws.Close()
		release()
		return
	}
	// virtuelle Gegenadresse: echte Client-Adresse, eigener Port je Tunnel (nur im Speicher)
	addr, err := netip.ParseAddr(ip)
	if err != nil {
		addr = netip.IPv6Unspecified()
	}
	remote := net.TCPAddrFromAddrPort(netip.AddrPortFrom(addr.Unmap(), uint16(1024+t.vport.Add(1)%60000)))
	c := wstun.New(ws, s.mux.Addr(), remote, wstun.Options{FC: true, MinWindow: wstun.DefMinWindow, Ping: tunnelPing})
	id := t.seq.Add(1)
	role := a.Role
	if role != "audio" && role != "video" {
		role = ""
	}
	t.mu.Lock()
	t.open[id] = &tunnelInfo{at: time.Now(), role: role, c: c}
	t.mu.Unlock()
	t.total.Add(1)
	t.lastOpen.Store(time.Now().Unix())
	go func() {
		<-c.Done()
		release()
		t.mu.Lock()
		delete(t.open, id)
		t.mu.Unlock()
	}()
	// erst mit dem ersten Datenbyte an die Weiche (deren Frist fuer das erste Byte ist 5 s, der WebSocket darf vorab
	// offen sein, bis libwebrtc sich lokal verbindet)
	tm := time.NewTimer(tunnelFirstWait)
	defer tm.Stop()
	select {
	case <-c.First():
		s.mux.Inject(c, nil, remote)
	case <-tm.C:
		c.Fail("timeout", wstun.CloseTimeout)
	case <-c.Done():
	}
}

// tunnelStats: Zaehler fuer Admin -> Anrufe (Lastmeldung an Saga) und Diagnose. Keine Adressen.
type tunnelStats struct {
	Open          int    `json:"open"`
	Total         uint64 `json:"total"`
	AuthFail      uint64 `json:"auth_fail,omitempty"`
	Limited       uint64 `json:"limited,omitempty"`
	NoUpgrade     uint64 `json:"no_upgrade,omitempty"`
	LastOpen      int64  `json:"last_open,omitempty"`
	LastNoUpgrade int64  `json:"last_no_upgrade,omitempty"`
	WinKiB        int64  `json:"win_kib,omitempty"`    // groesstes Fenster der offenen Tunnel
	RTTms         int64  `json:"rtt_ms,omitempty"`     // Median der kleinsten RTT (Quittungen) der offenen Tunnel
	RTTMaxms      int64  `json:"rtt_max_ms,omitempty"` // groesste davon
	BlockedMs     int64  `json:"blocked_ms,omitempty"` // Summe Wartezeit vor dem Fenster (offene Tunnel)
}

func (t *tunnels) stats() tunnelStats {
	st := tunnelStats{Total: t.total.Load(), AuthFail: t.authFail.Load(), Limited: t.limited.Load(), NoUpgrade: t.noUpgrade.Load(),
		LastOpen: t.lastOpen.Load(), LastNoUpgrade: t.lastNoUpgrade.Load()}
	t.mu.Lock()
	cs := make([]*wstun.Conn, 0, len(t.open))
	for _, ti := range t.open {
		cs = append(cs, ti.c)
	}
	t.mu.Unlock()
	st.Open = len(cs)
	var rtts []int64
	for _, c := range cs {
		sn := c.Snap()
		if w := sn.Window >> 10; w > st.WinKiB {
			st.WinKiB = w
		}
		if sn.RTTus > 0 {
			rtts = append(rtts, sn.RTTus)
		}
		st.BlockedMs += sn.BlockedMs
	}
	if len(rtts) > 0 {
		sort.Slice(rtts, func(i, j int) bool { return rtts[i] < rtts[j] })
		st.RTTms, st.RTTMaxms = rtts[len(rtts)/2]/1000, rtts[len(rtts)-1]/1000
	}
	return st
}

// apiTunnelStats (nur HORN_DEV): offene Tunnel mit Zaehlern.
func (s *server) apiTunnelStats(w http.ResponseWriter, r *http.Request) {
	t := s.tun
	t.mu.Lock()
	out := []map[string]any{}
	for id, ti := range t.open {
		out = append(out, map[string]any{"id": id, "role": ti.role, "age_s": int(time.Since(ti.at).Seconds()), "st": ti.c.Snap()})
	}
	t.mu.Unlock()
	writeJSON(w, 200, map[string]any{"tunnels": out, "sum": t.stats()})
}
