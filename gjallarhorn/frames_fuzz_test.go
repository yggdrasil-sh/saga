package main

import (
	"testing"

	"github.com/gorilla/websocket"
	"github.com/pion/rtp"
)

// Pruefer B2: Zusammensetzen beliebiger RTP-Nutzlasten (Pakete anderer Teilnehmender) und erste Nachricht des Browsers
// duerfen nie abstuerzen; ein gelieferter Rahmen hat immer den vollen Kopf und hoechstens frMaxMsg.
func FuzzFramesAsm(f *testing.F) {
	f.Add([]byte{0x90, 0xE0, 0x80, 0x01, 0x05, 0x60, 0x00}, uint16(1), true, false)
	f.Add([]byte{0x80, 0x80}, uint16(7), false, true)
	f.Add([]byte{0x10, 0x01, 0x02}, uint16(0), true, false)
	f.Fuzz(func(t *testing.T, p []byte, seq uint16, marker, audio bool) {
		var a frAsm
		for i := 0; i < 3; i++ {
			h := rtp.Header{SequenceNumber: seq + uint16(i), Timestamp: 1000, Marker: marker || i == 2}
			m, _ := a.push(2, audio, &h, p)
			if m != nil && (len(m) < frHdr || m[0] != frMsgFrame || len(m) > frMaxMsg+frHdr) {
				t.Fatalf("Rahmen % x", m[:min(len(m), 16)])
			}
		}
	})
}

func FuzzFramesJoin(f *testing.F) {
	f.Add([]byte(`{"t":"join","v":1,"token":"x","audio":3,"tiles":9,"device":"mobile"}`))
	f.Add([]byte(`{"t":"join","v":2}`))
	f.Fuzz(func(t *testing.T, b []byte) {
		j, code, cc := parseFramesJoin(websocket.TextMessage, b)
		if code == "" && (j.Token == "" || j.Tiles > 30 || j.Audio > 8 || j.Audio < 0 || cc != 0) {
			t.Fatalf("angenommen: %+v", j)
		}
	})
}
