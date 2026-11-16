// Package wstun: Medien-Tunnel ueber WebSocket (Saga 2.5, A3 "ICE-TCP im WebSocket-Tunnel", Unterprotokoll
// saga-tunnel.1).
//
// Die Apps (libwebrtc) sprechen ICE-TCP (RFC 4571) zu einem lokalen Port in der App; die App reicht den Byte-Strom 1:1
// in einen WebSocket ueber die normale Saga-Adresse (wss://saga.example.org/api/sfu/ws). Hinter einem TLS-beendenden
// Proxy (NPM) laeuft der WebSocket bis saga-media und von dort roh ueber einen Unix-Socket zum eingebauten Knoten, der
// ihn in seine ICE-TCP-Weiche einspeist (netx.Mux.Inject). Fuer den Knoten ist das ein normales TCP-Bein.
//
// Protokoll (docs/APP-API.md, "Medien-Tunnel /api/sfu/ws"):
//   - erste Nachricht (Text, App -> Knoten): {"t":"auth","v":1,"mode":"ice","user":…,"cred":…}
//   - Antwort (Text): {"t":"ok","v":1,"ack_bytes":…,"ack_ms":…} bzw. {"t":"err","v":1,"code":…} und Close
//   - danach Binaernachrichten, erstes Byte = Art (Zahlen big endian):
//     0x00 Daten     Byte-Strom, beliebig gestueckelt (1 Byte … MaxPayload)
//     0x01 Quittung  8 Byte: Summe der bisher auf diesem WebSocket empfangenen Nutzbytes von Daten-Nachrichten
//     andere Arten werden ignoriert (reserviert)
//
// Flusssteuerung: Hinter einem Proxy gehoert der Socket zum Geraet dem Proxy - mit dessen Sendepuffer (Autotuning bis
// tcp_wmem, oft MB), ohne TCP_NOTSENT_LOWAT. Der Stau laege dann im Proxy, der Knoten saehe ihn nie (gemessen: Bild-RTT
// 15 s bei 600 kbit/s Downlink). Deshalb quittiert die App, was angekommen ist, und der Knoten haelt hoechstens ein
// Fenster unquittierter Bytes unterwegs (2 x Lieferrate x kleinste RTT + 16 KiB, mindestens 64 KiB; wie cwnd +
// NOTSENT_LOWAT bei BBR). Darueber blockiert Write: der Stau liegt wieder im Bein, wo die Schaetzung "Alter der
// Warteschlange" und das Verwerfen nach 150 ms (H3) wirken. Die App sendet selbst ohne Fenster (ein Fenster in der App
// liess libwebrtc intern verwerfen, Uplink-Einfrierer) und braucht deshalb keine Quittungen vom Knoten.
package wstun

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"net"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// Arten der Binaernachrichten.
const (
	MsgData = 0x00
	MsgAck  = 0x01
)

// Subprotocol ist das WebSocket-Unterprotokoll (Version 1 im Feld v der Textnachrichten).
const (
	Subprotocol = "saga-tunnel.1"
	Version     = 1
	MaxPayload  = 64 << 10  // groesste Nutzlast je Daten-Nachricht
	MaxText     = 4 << 10   // groesste Textnachricht (auth)
	MaxRxBuf    = 256 << 10 // hoechstens so viele empfangene, noch nicht gelesene Bytes je Verbindung (dann liest readLoop
	// nicht weiter - Gegendruck ueber TCP bis zum Absender)
)

// Standardwerte (Vertrag A3).
const (
	DefMinWindow = 64 << 10 // 24 KiB drosselten bei gestautem Uplink (spaete Quittungen), 128 KiB staute den Downlink
	DefMaxWindow = 1 << 20
	DefAckBytes  = 8 << 10
	DefAckDelay  = 10 * time.Millisecond // Messung A3: 16 KiB/15 ms liess den Knoten bei 600 kbit/s auf Schicht m (nur Zeitschichten verworfen), 8 KiB/10 ms geht auf l; 5 ms im Spike = doppelt so viele Quittungen
)

// Close-Codes nach einem Fehler (Textnachricht err vorher).
const (
	CloseBad         = 4400
	CloseAuth        = 4401
	CloseTimeout     = 4408
	CloseUnavailable = 4503
)

// Auth ist die erste Textnachricht der App.
type Auth struct {
	T    string `json:"t"`
	V    int    `json:"v"`
	Mode string `json:"mode"`
	User string `json:"user"`
	Cred string `json:"cred"`
	Role string `json:"role,omitempty"`
}

// Reply ist die Antwort des Knotens (ok bzw. err).
type Reply struct {
	T        string `json:"t"`
	V        int    `json:"v"`
	Code     string `json:"code,omitempty"`
	Max      int    `json:"max,omitempty"`
	AckBytes int    `json:"ack_bytes,omitempty"`
	AckMs    int    `json:"ack_ms,omitempty"`
}

// Options einer Seite.
type Options struct {
	FC        bool          // eigene Sendungen nach Quittungen begrenzen (Knoten)
	Ack       bool          // empfangene Bytes quittieren (App)
	MinWindow int           // kleinstes Fenster (Byte), Standard DefMinWindow
	MaxWindow int           // groesstes Fenster, Standard DefMaxWindow
	AckBytes  int           // spaetestens nach so vielen empfangenen Bytes quittieren
	AckDelay  time.Duration // ... bzw. so lange nach dem ersten unquittierten Byte
	Ping      time.Duration // >0: WebSocket-Ping in diesem Abstand; ohne Lebenszeichen 3 x Ping -> Schluss
}

// Stats einer Verbindung (atomar lesbar).
type Stats struct {
	TxBytes, RxBytes, TxMsgs, RxMsgs, AcksTx, AcksRx atomic.Uint64
	Blocked                                          atomic.Int64 // Summe der Wartezeit vor dem Fenster (ns)
	BlockedSince                                     atomic.Int64 // wartet gerade seit (UnixNano, 0 = nicht)
	MinRTTus, BWBps, Window, Inflight, MaxInflight   atomic.Int64
}

// Snapshot: Zaehler fuer Diagnose (JSON).
type Snapshot struct {
	Window      int64  `json:"win"`
	Inflight    int64  `json:"inflight"`
	MaxInflight int64  `json:"max_inflight"`
	RTTus       int64  `json:"rtt_us"`
	BWBps       int64  `json:"bw_Bps"`
	Tx          uint64 `json:"tx"`
	Rx          uint64 `json:"rx"`
	AcksRx      uint64 `json:"acks_rx"`
	AcksTx      uint64 `json:"acks_tx"`
	BlockedMs   int64  `json:"blocked_ms"`
}

// Conn: net.Conn ueber einen WebSocket (Strom).
type Conn struct {
	ws            *websocket.Conn
	opt           Options
	local, remote net.Addr
	St            Stats

	wmu sync.Mutex // Schreiben auf den WebSocket

	// Empfang
	rch    chan []byte
	rq     atomic.Int64  // Bytes in rch
	rxFree chan struct{} // Leser hat etwas aus rch genommen
	rbuf   []byte
	first  chan struct{} // zu beim ersten Datenbyte
	fonce  sync.Once
	rdl    atomic.Int64 // Lesefrist (UnixNano, 0 = keine)
	rdlCh  chan struct{}
	closed chan struct{}
	once   sync.Once
	rerr   atomic.Pointer[error]
	alive  atomic.Int64 // letztes Lebenszeichen der Gegenseite (UnixNano)

	// Quittungen senden
	amu      sync.Mutex
	rcvd     uint64
	ackedOut uint64
	ackTimer *time.Timer

	// Flusssteuerung (eigene Sendungen, gemeinsam mit dem Rahmen-Bein: flow.go)
	flow *Flow
	wdl  atomic.Int64
}

// New: Verbindung ueber einen fertigen WebSocket (nach auth/ok); liest sofort.
func New(ws *websocket.Conn, local, remote net.Addr, opt Options) *Conn {
	if opt.AckBytes <= 0 {
		opt.AckBytes = DefAckBytes
	}
	if opt.AckDelay <= 0 {
		opt.AckDelay = DefAckDelay
	}
	c := &Conn{ws: ws, opt: opt, local: local, remote: remote, rch: make(chan []byte, 512), closed: make(chan struct{}),
		rdlCh: make(chan struct{}, 1), first: make(chan struct{}), rxFree: make(chan struct{}, 1)}
	c.flow = NewFlow(opt.MinWindow, opt.MaxWindow, &c.St)
	c.alive.Store(time.Now().UnixNano())
	ws.SetReadLimit(MaxPayload + 64)
	ws.SetPongHandler(func(string) error { c.alive.Store(time.Now().UnixNano()); return nil })
	go c.readLoop()
	if opt.Ping > 0 {
		go c.pingLoop()
	}
	return c
}

var errClosed = errors.New("wstun: geschlossen")

func (c *Conn) fail(err error) {
	if err == nil {
		err = errClosed
	}
	c.rerr.CompareAndSwap(nil, &err)
	c.once.Do(func() {
		close(c.closed)
		_ = c.ws.Close()
		c.flow.Wake()
	})
}

func (c *Conn) readLoop() {
	defer close(c.rch)
	for {
		mt, b, err := c.ws.ReadMessage()
		if err != nil {
			c.fail(err)
			return
		}
		c.alive.Store(time.Now().UnixNano())
		if mt != websocket.BinaryMessage || len(b) == 0 {
			continue // Text nach auth: reserviert
		}
		switch b[0] {
		case MsgAck:
			if len(b) == 9 {
				c.onAck(binary.BigEndian.Uint64(b[1:9]))
				c.St.AcksRx.Add(1)
			}
		case MsgData:
			p := b[1:]
			if len(p) == 0 {
				continue
			}
			c.St.RxBytes.Add(uint64(len(p)))
			c.St.RxMsgs.Add(1)
			c.fonce.Do(func() { close(c.first) })
			c.noteRcvd(len(p))
			for q := c.rq.Load(); q > 0 && q+int64(len(p)) > MaxRxBuf; q = c.rq.Load() {
				select { // Puffer voll: erst weiterlesen, wenn der Leser Platz gemacht hat
				case <-c.rxFree:
				case <-c.closed:
					return
				}
			}
			c.rq.Add(int64(len(p)))
			select {
			case c.rch <- p:
			case <-c.closed:
				return
			}
		}
	}
}

func (c *Conn) pingLoop() {
	t := time.NewTicker(c.opt.Ping)
	defer t.Stop()
	for {
		select {
		case <-c.closed:
			return
		case <-t.C:
		}
		if time.Since(time.Unix(0, c.alive.Load())) > 3*c.opt.Ping {
			c.fail(errors.New("wstun: kein lebenszeichen"))
			return
		}
		if err := c.ws.WriteControl(websocket.PingMessage, nil, time.Now().Add(5*time.Second)); err != nil {
			c.fail(err)
			return
		}
	}
}

// First ist zu, sobald das erste Datenbyte angekommen ist.
func (c *Conn) First() <-chan struct{} { return c.first }

// --- Quittungen senden ---------------------------------------------------------------------------------------------

func (c *Conn) noteRcvd(n int) {
	if !c.opt.Ack {
		return
	}
	c.amu.Lock()
	c.rcvd += uint64(n)
	due := c.rcvd-c.ackedOut >= uint64(c.opt.AckBytes)
	if !due && c.ackTimer == nil {
		c.ackTimer = time.AfterFunc(c.opt.AckDelay, c.ackNow)
	}
	c.amu.Unlock()
	if due {
		c.ackNow()
	}
}

func (c *Conn) ackNow() {
	c.amu.Lock()
	if c.ackTimer != nil {
		c.ackTimer.Stop()
		c.ackTimer = nil
	}
	v := c.rcvd
	if v == c.ackedOut {
		c.amu.Unlock()
		return
	}
	c.ackedOut = v
	c.amu.Unlock()
	var m [9]byte
	m[0] = MsgAck
	binary.BigEndian.PutUint64(m[1:], v)
	c.wmu.Lock()
	_ = c.ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
	err := c.ws.WriteMessage(websocket.BinaryMessage, m[:])
	c.wmu.Unlock()
	if err != nil {
		c.fail(err)
		return
	}
	c.St.AcksTx.Add(1)
}

// --- Flusssteuerung ------------------------------------------------------------------------------------------------

func (c *Conn) onAck(a uint64) { c.flow.OnAck(a) }

// reserve wartet, bis das Fenster mindestens ein Byte frei hat (wie TCP: das ganze Stueck geht dann).
func (c *Conn) reserve() error {
	if !c.opt.FC {
		return nil
	}
	return c.flow.Reserve(c.closed, &c.wdl)
}

func (c *Conn) noteSent(n int) { c.flow.Sent(n, c.opt.FC) }

func (c *Conn) send(b []byte) error {
	c.wmu.Lock()
	defer c.wmu.Unlock()
	if dl := c.wdl.Load(); dl != 0 {
		_ = c.ws.SetWriteDeadline(time.Unix(0, dl))
	} else {
		_ = c.ws.SetWriteDeadline(time.Now().Add(10 * time.Second))
	}
	w, err := c.ws.NextWriter(websocket.BinaryMessage)
	if err != nil {
		return err
	}
	_, _ = w.Write([]byte{MsgData})
	_, _ = w.Write(b)
	if err := w.Close(); err != nil {
		return err
	}
	c.St.TxBytes.Add(uint64(len(b)))
	c.St.TxMsgs.Add(1)
	return nil
}

// --- net.Conn ------------------------------------------------------------------------------------------------------

// Read liefert Nutzbytes (Strom).
func (c *Conn) Read(b []byte) (int, error) {
	if len(c.rbuf) == 0 {
		p, err := c.next()
		if err != nil {
			return 0, err
		}
		c.rbuf = p
	}
	n := copy(b, c.rbuf)
	c.rbuf = c.rbuf[n:]
	return n, nil
}

func (c *Conn) next() ([]byte, error) {
	for {
		var tc <-chan time.Time
		var t *time.Timer
		if dl := c.rdl.Load(); dl != 0 {
			d := time.Until(time.Unix(0, dl))
			if d <= 0 {
				return nil, os.ErrDeadlineExceeded
			}
			t = time.NewTimer(d)
			tc = t.C
		}
		select {
		case p, ok := <-c.rch:
			if t != nil {
				t.Stop()
			}
			if !ok {
				if e := c.rerr.Load(); e != nil {
					return nil, *e
				}
				return nil, errClosed
			}
			c.rq.Add(-int64(len(p)))
			select {
			case c.rxFree <- struct{}{}:
			default:
			}
			return p, nil
		case <-tc:
			return nil, os.ErrDeadlineExceeded
		case <-c.rdlCh:
			if t != nil {
				t.Stop()
			}
			continue // Frist geaendert
		}
	}
}

// Write schickt den Strom in Nachrichten zu hoechstens MaxPayload; blockiert vor dem Fenster (Flusssteuerung).
func (c *Conn) Write(b []byte) (int, error) {
	n := 0
	for len(b) > 0 {
		k := len(b)
		if k > MaxPayload {
			k = MaxPayload
		}
		if err := c.reserve(); err != nil {
			return n, err
		}
		// vor dem Senden zaehlen: die Quittung kann (kurze RTT, Schreiber verdraengt) vor einem noteSent danach
		// ankommen; onAck kappte sie dann auf das alte sent, die Bytes blieben fuer immer "unterwegs" und das Fenster
		// schloesse sich (CI-Haenger 21bb100). Scheitert send, ist die Verbindung ohnehin zu.
		c.noteSent(k)
		if err := c.send(b[:k]); err != nil {
			c.fail(err)
			return n, err
		}
		n += k
		b = b[k:]
	}
	return n, nil
}

// Close schliesst den WebSocket (Close 1000).
func (c *Conn) Close() error {
	c.wmu.Lock()
	_ = c.ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""), time.Now().Add(time.Second))
	c.wmu.Unlock()
	c.fail(errClosed)
	return nil
}

// Fail schickt {"t":"err","code":…} und schliesst mit dem Close-Code (z. B. Frist fuer das erste Datenbyte).
func (c *Conn) Fail(code string, closeCode int) {
	c.wmu.Lock()
	_ = WriteErr(c.ws, code, closeCode)
	c.wmu.Unlock()
	c.fail(errors.New("wstun: " + code))
}

// Done ist zu, sobald die Verbindung weg ist.
func (c *Conn) Done() <-chan struct{} { return c.closed }

func (c *Conn) LocalAddr() net.Addr  { return c.local }
func (c *Conn) RemoteAddr() net.Addr { return c.remote }

func (c *Conn) SetDeadline(t time.Time) error {
	_ = c.SetReadDeadline(t)
	return c.SetWriteDeadline(t)
}

func (c *Conn) SetReadDeadline(t time.Time) error {
	if t.IsZero() {
		c.rdl.Store(0)
	} else {
		c.rdl.Store(t.UnixNano())
	}
	select {
	case c.rdlCh <- struct{}{}:
	default:
	}
	return nil
}

func (c *Conn) SetWriteDeadline(t time.Time) error {
	if t.IsZero() {
		c.wdl.Store(0)
	} else {
		c.wdl.Store(t.UnixNano())
	}
	c.flow.Wake()
	return nil
}

// Snap: Zaehler fuer Diagnose.
func (c *Conn) Snap() Snapshot {
	return Snapshot{Window: int64(c.flow.Window()), Inflight: c.St.Inflight.Load(), MaxInflight: c.St.MaxInflight.Load(), RTTus: c.St.MinRTTus.Load(),
		BWBps: c.St.BWBps.Load(), Tx: c.St.TxBytes.Load(), Rx: c.St.RxBytes.Load(), AcksRx: c.St.AcksRx.Load(), AcksTx: c.St.AcksTx.Load(),
		BlockedMs: c.flow.BlockedNs() / 1e6}
}

// --- Handschlag ----------------------------------------------------------------------------------------------------

// WriteErr: {"t":"err","v":1,"code":…} und Close-Rahmen (ohne die Verbindung zu schliessen). Nur im Handschlag bzw.
// unter dem Schreib-Schloss aufrufen.
func WriteErr(ws *websocket.Conn, code string, closeCode int) error {
	r := Reply{T: "err", V: Version, Code: code}
	if code == "version" {
		r.Max = Version
	}
	b, _ := json.Marshal(r)
	_ = ws.SetWriteDeadline(time.Now().Add(2 * time.Second))
	err := ws.WriteMessage(websocket.TextMessage, b)
	_ = ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(closeCode, code), time.Now().Add(time.Second))
	return err
}

// ParseAuth prueft die erste Nachricht -> Fehlercode ("" = in Ordnung) und Close-Code.
func ParseAuth(mt int, b []byte) (Auth, string, int) {
	var a Auth
	if mt != websocket.TextMessage || len(b) > MaxText {
		return a, "bad", CloseBad
	}
	if err := json.Unmarshal(b, &a); err != nil || a.T != "auth" || a.V < 1 {
		return a, "bad", CloseBad
	}
	if a.V != Version {
		return a, "version", CloseBad
	}
	if a.Mode != "ice" {
		return a, "mode", CloseBad
	}
	if a.User == "" || a.Cred == "" || len(a.User) > 256 || len(a.Cred) > 256 {
		return a, "auth", CloseAuth
	}
	return a, "", 0
}

// ErrRejected: Antwort err des Knotens (Code im Text).
type ErrRejected struct{ Code string }

func (e *ErrRejected) Error() string { return "wstun: abgelehnt: " + e.Code }

// ClientAuth (App-Seite, Tests und Pruefstand): auth senden, Antwort lesen -> ok-Antwort bzw. *ErrRejected.
func ClientAuth(ws *websocket.Conn, user, cred, role string, timeout time.Duration) (Reply, error) {
	b, _ := json.Marshal(Auth{T: "auth", V: Version, Mode: "ice", User: user, Cred: cred, Role: role})
	_ = ws.SetWriteDeadline(time.Now().Add(timeout))
	if err := ws.WriteMessage(websocket.TextMessage, b); err != nil {
		return Reply{}, err
	}
	_ = ws.SetWriteDeadline(time.Time{})
	_ = ws.SetReadDeadline(time.Now().Add(timeout))
	mt, rb, err := ws.ReadMessage()
	_ = ws.SetReadDeadline(time.Time{})
	if err != nil {
		return Reply{}, err
	}
	var r Reply
	if mt != websocket.TextMessage || json.Unmarshal(rb, &r) != nil {
		return Reply{}, errors.New("wstun: antwort kein json")
	}
	if r.T != "ok" {
		return r, &ErrRejected{Code: r.Code}
	}
	return r, nil
}
