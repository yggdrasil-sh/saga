package wstun

import (
	"encoding/binary"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// pair: Knoten-Seite (srv) und App-Seite (cli) ueber einen echten WebSocket.
func pair(t *testing.T, so, co Options) (srv, cli *Conn) {
	t.Helper()
	ch := make(chan *Conn, 1)
	up := websocket.Upgrader{Subprotocols: []string{Subprotocol}}
	hs := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, err := up.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		ch <- New(ws, nil, nil, so)
	}))
	t.Cleanup(hs.Close)
	d := websocket.Dialer{Subprotocols: []string{Subprotocol}}
	ws, _, err := d.Dial("ws"+strings.TrimPrefix(hs.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	cli = New(ws, nil, nil, co)
	srv = <-ch
	t.Cleanup(func() { srv.Close(); cli.Close() })
	return srv, cli
}

func TestRahmenUndQuittungen(t *testing.T) {
	srv, cli := pair(t, Options{FC: true}, Options{Ack: true, AckBytes: 1000, AckDelay: 10 * time.Millisecond})
	// Knoten -> App: 200 KiB (mehr als das kleinste Fenster) kommen vollstaendig an, die App quittiert
	data := make([]byte, 200<<10)
	for i := range data {
		data[i] = byte(i * 7)
	}
	wr := make(chan error, 1)
	go func() { _, err := srv.Write(data); wr <- err }()
	got := make([]byte, len(data))
	_ = cli.SetReadDeadline(time.Now().Add(10 * time.Second))
	if _, err := io.ReadFull(cli, got); err != nil {
		t.Fatalf("%v (%+v)", err, srv.Snap())
	}
	_ = cli.SetReadDeadline(time.Time{})
	if string(got) != string(data) {
		t.Fatal("Strom veraendert")
	}
	if err := <-wr; err != nil { // Write zurueck: Zaehler des Knotens fertig (Tx zaehlt nach dem Senden)
		t.Fatal(err)
	}
	for i := 0; i < 100 && srv.Snap().Inflight != 0; i++ { // letzte Quittung nach ack_ms (unter Last spaeter)
		time.Sleep(10 * time.Millisecond)
	}
	if srv.St.AcksRx.Load() == 0 || cli.St.AcksTx.Load() == 0 {
		t.Fatalf("keine Quittungen: rx %d tx %d", srv.St.AcksRx.Load(), cli.St.AcksTx.Load())
	}
	sn := srv.Snap()
	if sn.Inflight != 0 || sn.Tx != uint64(len(data)) || sn.Window < DefMinWindow {
		t.Fatalf("Zaehler: %+v", sn)
	}
	// Nachrichten hoechstens MaxPayload (200 KiB -> mindestens 4 Nachrichten)
	if srv.St.TxMsgs.Load() < 4 {
		t.Fatalf("nicht gestueckelt: %d Nachrichten", srv.St.TxMsgs.Load())
	}
	// App -> Knoten: ohne Fenster, der Knoten quittiert nicht
	if _, err := cli.Write([]byte("hallo knoten")); err != nil {
		t.Fatal(err)
	}
	b := make([]byte, 64)
	n, err := srv.Read(b)
	if err != nil || string(b[:n]) != "hallo knoten" {
		t.Fatalf("%q %v", b[:n], err)
	}
	time.Sleep(30 * time.Millisecond)
	if cli.St.AcksRx.Load() != 0 || srv.St.AcksTx.Load() != 0 {
		t.Fatal("Knoten darf in v1 nicht quittieren")
	}
}

// Quittung schneller als der Schreiber (CI-Haenger 21bb100): die App quittiert jede Nachricht sofort; kam die Quittung
// vor dem Zaehlen der gesendeten Bytes an, wurde sie gekappt und das Fenster blieb zu. Viele Nachrichten, damit das
// Rennen (falls es wieder auftaucht) mit hoher Rate trifft; Frist statt Haenger.
func TestQuittungVorZaehlung(t *testing.T) {
	srv, cli := pair(t, Options{FC: true}, Options{Ack: true, AckBytes: 1})
	go func() { _, _ = io.Copy(io.Discard, cli) }()
	_ = srv.SetWriteDeadline(time.Now().Add(10 * time.Second))
	data := make([]byte, 128*MaxPayload)
	if n, err := srv.Write(data); err != nil {
		t.Fatalf("nach %d Byte: %v (%+v)", n, err, srv.Snap())
	}
	for i := 0; i < 200 && srv.Snap().Inflight != 0; i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if sn := srv.Snap(); sn.Inflight != 0 {
		t.Fatalf("Quittungen verloren: %+v", sn)
	}
}

// Gegenprobe Fenster: quittiert die App nicht, haelt der Knoten nach dem Fenster an (Write blockiert bis zur Frist).
func TestFensterBlockiertOhneQuittung(t *testing.T) {
	srv, cli := pair(t, Options{FC: true}, Options{})
	go func() { _, _ = io.Copy(io.Discard, cli) }()
	_ = srv.SetWriteDeadline(time.Now().Add(300 * time.Millisecond))
	n, err := srv.Write(make([]byte, 512<<10))
	if !errors.Is(err, os.ErrDeadlineExceeded) {
		t.Fatalf("Write lief durch: %d %v", n, err)
	}
	if n < DefMinWindow || n > DefMinWindow+MaxPayload {
		t.Fatalf("nach %d Byte angehalten, erwartet ~%d", n, DefMinWindow)
	}
	if srv.St.Blocked.Load() == 0 {
		t.Fatal("Wartezeit nicht gezaehlt")
	}
}

// Ohne Fenster (Gegenprobe zur Gegenprobe): dieselbe Menge geht ohne Quittungen durch.
func TestOhneFensterKeinHalt(t *testing.T) {
	srv, cli := pair(t, Options{}, Options{})
	go func() { _, _ = io.Copy(io.Discard, cli) }()
	_ = srv.SetWriteDeadline(time.Now().Add(2 * time.Second))
	if n, err := srv.Write(make([]byte, 512<<10)); err != nil {
		t.Fatalf("%d %v", n, err)
	}
}

// Quittungsabstand: wenige Bytes -> eine Quittung nach AckDelay; viele -> nach AckBytes.
func TestQuittungsabstand(t *testing.T) {
	srv, cli := pair(t, Options{FC: true}, Options{Ack: true})
	go func() { _, _ = io.Copy(io.Discard, cli) }()
	if _, err := srv.Write([]byte("x")); err != nil {
		t.Fatal(err)
	}
	time.Sleep(DefAckDelay / 2)
	if cli.St.AcksTx.Load() != 0 {
		t.Fatal("Quittung vor ack_ms")
	}
	time.Sleep(DefAckDelay + 30*time.Millisecond)
	if cli.St.AcksTx.Load() != 1 {
		t.Fatalf("%d Quittungen statt 1", cli.St.AcksTx.Load())
	}
	for i := 0; i < 4; i++ { // je Nachricht ack_bytes -> je eine Quittung
		if _, err := srv.Write(make([]byte, DefAckBytes)); err != nil {
			t.Fatal(err)
		}
	}
	time.Sleep(10 * time.Millisecond)
	if a := cli.St.AcksTx.Load(); a < 4 || a > 6 {
		t.Fatalf("%d Quittungen fuer 4 x ack_bytes", a)
	}
}

// Unbekannte Arten und kaputte Quittungen werden ignoriert; Quittung ueber das Gesendete hinaus zaehlt nicht.
func TestUnbekannteArten(t *testing.T) {
	ch := make(chan *Conn, 1)
	up := websocket.Upgrader{}
	hs := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, _ := up.Upgrade(w, r, nil)
		ch <- New(ws, nil, nil, Options{FC: true})
	}))
	defer hs.Close()
	ws, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(hs.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	srv := <-ch
	defer srv.Close()
	var big [9]byte
	big[0] = MsgAck
	binary.BigEndian.PutUint64(big[1:], 1<<40)
	for _, m := range [][]byte{{0x02, 1, 2, 3}, {0x7f}, {MsgAck, 1, 2}, big[:], {MsgData}, {MsgData, 'o', 'k'}} {
		_ = ws.WriteMessage(websocket.BinaryMessage, m)
	}
	_ = ws.WriteMessage(websocket.TextMessage, []byte(`{"t":"irgendwas"}`))
	b := make([]byte, 8)
	_ = srv.SetReadDeadline(time.Now().Add(2 * time.Second))
	n, err := srv.Read(b)
	if err != nil || string(b[:n]) != "ok" {
		t.Fatalf("%q %v", b[:n], err)
	}
	if srv.Snap().Inflight != 0 || srv.St.AcksRx.Load() != 1 {
		t.Fatalf("Quittungen: %+v", srv.Snap())
	}
}

func TestParseAuth(t *testing.T) {
	for _, c := range []struct {
		mt   int
		in   string
		code string
		cc   int
	}{
		{websocket.TextMessage, `{"t":"auth","v":1,"mode":"ice","user":"1:u","cred":"c"}`, "", 0},
		{websocket.TextMessage, `{"t":"auth","v":1,"mode":"ice","user":"1:u","cred":"c","role":"video","neu":1}`, "", 0},
		{websocket.BinaryMessage, `{"t":"auth","v":1,"mode":"ice","user":"1:u","cred":"c"}`, "bad", CloseBad},
		{websocket.TextMessage, `kein json`, "bad", CloseBad},
		{websocket.TextMessage, `{"t":"hallo","v":1}`, "bad", CloseBad},
		{websocket.TextMessage, `{"t":"auth","mode":"ice","user":"u","cred":"c"}`, "bad", CloseBad},
		{websocket.TextMessage, `{"t":"auth","v":2,"mode":"ice","user":"u","cred":"c"}`, "version", CloseBad},
		{websocket.TextMessage, `{"t":"auth","v":1,"mode":"turn","user":"u","cred":"c"}`, "mode", CloseBad},
		{websocket.TextMessage, `{"t":"auth","v":1,"mode":"ice","user":"","cred":"c"}`, "auth", CloseAuth},
		{websocket.TextMessage, `{"t":"auth","v":1,"mode":"ice","user":"u","cred":"` + strings.Repeat("x", 5000) + `"}`, "bad", CloseBad},
	} {
		_, code, cc := ParseAuth(c.mt, []byte(c.in))
		if code != c.code || cc != c.cc {
			t.Errorf("%.60s: %q/%d statt %q/%d", c.in, code, cc, c.code, c.cc)
		}
	}
}

// Empfangspuffer je Verbindung gedeckelt (Pruefung F7): liest niemand, nimmt readLoop hoechstens MaxRxBuf (+ eine
// Nachricht) an; danach Gegendruck ueber TCP.
func TestRxPufferGrenze(t *testing.T) {
	srv, cli := pair(t, Options{FC: true}, Options{})
	go func() {
		_ = cli.SetWriteDeadline(time.Now().Add(3 * time.Second))
		_, _ = cli.Write(make([]byte, 4<<20))
	}()
	time.Sleep(500 * time.Millisecond)
	if q := srv.rq.Load(); q > MaxRxBuf+MaxPayload || q < MaxRxBuf/2 {
		t.Fatalf("%d Byte gepuffert (Grenze %d)", q, MaxRxBuf)
	}
	b := make([]byte, 64<<10)
	n := 0
	_ = srv.SetReadDeadline(time.Now().Add(3 * time.Second))
	for n < 1<<20 {
		k, err := srv.Read(b)
		if err != nil {
			t.Fatalf("nach %d Byte: %v", n, err)
		}
		n += k
	}
}
