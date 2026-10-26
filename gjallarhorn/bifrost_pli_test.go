package main

import (
	"encoding/binary"
	"io"
	"net"
	"sync/atomic"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// vp8Test baut ein VP8-Paket (ein Rahmen je Paket, PictureID 15 Bit, TID 0).
func vp8Test(seq uint16, key bool) []byte {
	b := make([]byte, 12, 12+6+64)
	b[0] = 0x80
	b[1] = 96 | 0x80
	binary.BigEndian.PutUint16(b[2:], seq)
	binary.BigEndian.PutUint32(b[4:], uint32(seq)*3000)
	binary.BigEndian.PutUint32(b[8:], 7)
	pl := byte(1)
	if key {
		pl = 0
	}
	b = append(b, 0x90, 0xa0, 0x80|byte(seq>>8), byte(seq), 0x20, pl)
	return append(b, make([]byte, 64)...)
}

// TestBrueckeSchluesselbildNachfrage (BH3): Ein Bruecken-Ausgang wurde nur einmal (beim Anlegen) zugewiesen. Verwarf er
// nach Stau (H3) bis zum Schluesselbild, ging genau eine PLI raus - fiel sie in die Drossel (eine je Quelle und 500 ms),
// fragte niemand nach: Die Gegenseite sieht keine Luecke (Folgenummern lueckenlos), ihre Empfaenger fordern nichts an,
// und ein stehender Bildschirm blieb eingefroren bzw. schwarz, bis der Sender von sich aus ein Schluesselbild schickt.
// Jetzt fragt die Bruecke wie die Beine in jedem Takt nach, solange sie wartet.
func TestBrueckeSchluesselbildNachfrage(t *testing.T) {
	oldPLI, oldAge := fwd.PLIInterval, fwd.MaxQueueAge
	fwd.PLIInterval = 30 * time.Millisecond
	defer func() { fwd.PLIInterval, fwd.MaxQueueAge = oldPLI, oldAge }()

	n := sfu.NewNode(sfu.Config{Name: "k", Tick: time.Hour})
	defer n.Close()
	room, err := n.CreateRoom("raum-bruecke", nil)
	if err != nil {
		t.Fatal(err)
	}
	l, err := room.AddLeg(sfu.Grant{Room: "raum-bruecke", Peer: "xaver", Leg: "xaver-v", Role: sfu.RoleVideo, Pub: sfu.PubScreen}, sfu.PathTCP, sfu.Want{})
	if err != nil {
		t.Fatal(err)
	}
	var plis atomic.Int32
	src, err := l.AddSource(fwd.Video, "h", true, 7, 0, func(uint32) { plis.Add(1) })
	if err != nil {
		t.Fatal(err)
	}
	c1, c2 := net.Pipe()
	defer c2.Close()
	go func() { _, _ = io.Copy(io.Discard, c2) }()
	b := newBridge(&server{}, "bruecke-pli", room, "gegenueber", true, c1, nil)
	defer b.fl.Close()
	defer c1.Close()

	b.tick() // Kennungen vergeben
	b.smu.Lock()
	sid := b.sids[src]
	b.want[sid] = true
	b.smu.Unlock()
	if sid == 0 {
		t.Fatal("keine Kennung fuer die Quelle")
	}
	b.tick() // Ausgang anlegen: wartet auf ein Schluesselbild
	if plis.Load() != 1 {
		t.Fatalf("plis nach dem Anlegen: %d", plis.Load())
	}
	time.Sleep(40 * time.Millisecond)
	b.tick()
	if plis.Load() < 2 {
		t.Fatalf("wartender Bruecken-Ausgang fragt nicht nach (plis %d)", plis.Load())
	}
	// Schluesselbild: der Ausgang laeuft
	src.Inject(vp8Test(1, true))
	src.Inject(vp8Test(2, false))
	time.Sleep(20 * time.Millisecond)
	b.smu.Lock()
	o := b.outs[src]
	b.smu.Unlock()
	if o == nil || o.Current() != src {
		t.Fatal("bruecken-ausgang nicht umgeschaltet")
	}
	for i := 0; i < 100 && o.Pkts.Load() < 2; i++ { // beide geschrieben (auch: Schreiber hat MaxQueueAge gelesen)
		time.Sleep(5 * time.Millisecond)
	}
	// Stau: ab dem naechsten Rahmen bis zum Schluesselbild verwerfen (eine PLI, dann nachfragen)
	fwd.MaxQueueAge = -1
	time.Sleep(40 * time.Millisecond)
	before := plis.Load()
	src.Inject(vp8Test(3, false))
	time.Sleep(20 * time.Millisecond)
	if plis.Load() != before+1 {
		t.Fatalf("stau: plis %d -> %d", before, plis.Load())
	}
	fwd.MaxQueueAge = oldAge
	for i := 0; i < 3; i++ {
		time.Sleep(40 * time.Millisecond)
		b.tick()
	}
	if got := plis.Load(); got < before+3 {
		t.Fatalf("nach Stau fragt die Bruecke nicht nach (plis %d -> %d)", before, got)
	}
}
