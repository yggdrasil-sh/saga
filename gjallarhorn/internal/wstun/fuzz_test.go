package wstun

import (
	"bytes"
	"encoding/binary"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// Pruefer A3: Fuzz fuer die erste Nachricht und die Binaerrahmen (Daten/Quittung/unbekannt). Ohne -fuzz laufen nur die
// Saatwerte (schnell, Teil der normalen Reihe).

func FuzzParseAuth(f *testing.F) {
	for _, s := range []string{`{"t":"auth","v":1,"mode":"ice","user":"1:u","cred":"c"}`, `{"t":"auth","v":2}`, `{}`, `[]`, `null`,
		`{"t":"auth","v":1e300,"mode":"ice","user":"u","cred":"c"}`, `{"t":"auth","v":-1}`, "\xff\xfe"} {
		f.Add(true, []byte(s))
	}
	f.Fuzz(func(t *testing.T, text bool, b []byte) {
		mt := websocket.BinaryMessage
		if text {
			mt = websocket.TextMessage
		}
		a, code, cc := ParseAuth(mt, b)
		switch code {
		case "":
			if cc != 0 || a.T != "auth" || a.V != Version || a.Mode != "ice" || a.User == "" || a.Cred == "" || len(a.User) > 256 || len(a.Cred) > 256 || len(b) > MaxText {
				t.Fatalf("angenommen, aber ungueltig: %q -> %+v", b, a)
			}
		case "bad", "version", "mode":
			if cc != CloseBad {
				t.Fatalf("%s mit Close %d", code, cc)
			}
		case "auth":
			if cc != CloseAuth {
				t.Fatalf("auth mit Close %d", cc)
			}
		default:
			t.Fatalf("unbekannter Code %q", code)
		}
	})
}

// FuzzRahmen: beliebige Binaernachrichten an die Knoten-Seite (Fenster an) - kein Absturz, Quittungen nie ueber das
// Gesendete, angenommene Nutzbytes = Daten-Nachrichten ohne Art-Byte.
func FuzzRahmen(f *testing.F) {
	var ack [9]byte
	ack[0] = MsgAck
	binary.BigEndian.PutUint64(ack[1:], 1<<63)
	f.Add([]byte{MsgData, 'x'}, ack[:], []byte{0x02, 1})
	f.Add([]byte{MsgAck}, []byte{MsgData}, []byte{})
	f.Add([]byte{MsgData, 1, 2, 3}, []byte{MsgAck, 0, 0, 0, 0, 0, 0, 0, 3}, []byte{MsgAck, 0, 0, 0, 0, 0, 0, 0, 2})
	ch := make(chan *Conn, 1)
	up := websocket.Upgrader{}
	hs := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, err := up.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		ch <- New(ws, nil, nil, Options{FC: true})
	}))
	defer hs.Close()
	url := "ws" + strings.TrimPrefix(hs.URL, "http")
	f.Fuzz(func(t *testing.T, m1, m2, m3 []byte) {
		ws, _, err := websocket.DefaultDialer.Dial(url, nil)
		if err != nil {
			t.Fatal(err)
		}
		defer ws.Close()
		srv := <-ch
		defer srv.Close()
		// etwas senden, damit Quittungen etwas zu quittieren haben
		if _, err := srv.Write([]byte("hallo")); err != nil {
			t.Fatal(err)
		}
		var want []byte
		for _, m := range [][]byte{m1, m2, m3} {
			if len(m) > MaxPayload+1 {
				m = m[:MaxPayload+1]
			}
			if len(m) > 1 && m[0] == MsgData {
				want = append(want, m[1:]...)
			}
			_ = ws.WriteMessage(websocket.BinaryMessage, m)
		}
		_ = ws.WriteMessage(websocket.BinaryMessage, []byte{MsgData, 0xEE}) // Ende-Marke
		want = append(want, 0xEE)
		got := make([]byte, 0, len(want))
		buf := make([]byte, 4096)
		_ = srv.SetReadDeadline(time.Now().Add(3 * time.Second))
		for len(got) < len(want) {
			n, err := srv.Read(buf)
			if err != nil {
				t.Fatalf("lesen: %v (bisher %d von %d)", err, len(got), len(want))
			}
			got = append(got, buf[:n]...)
		}
		if !bytes.Equal(got, want) {
			t.Fatalf("Nutzbytes verschieden: %d statt %d", len(got), len(want))
		}
		if sn := srv.Snap(); sn.Inflight < 0 || sn.Inflight > 5 {
			t.Fatalf("Inflight %d (gesendet 5)", sn.Inflight)
		}
	})
}
