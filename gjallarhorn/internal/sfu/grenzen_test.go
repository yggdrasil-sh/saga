package sfu

import (
	"errors"
	"fmt"
	"runtime"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// 2.6 (Bugs-DB 2416, 2263-3): Knoten mit 200 Beinen (neuer Standard HORN_MAX_LEGS) - zwei Anrufe mit je 50 Geraeten
// ueber TCP (Ton- und Bild-Bein je Geraet). Prueft: keine versteckte Grenze unter 200 (vorab angelegte Felder), das
// 201. Bein und das 51. Geraet eines Raums werden abgelehnt, ein Takt mit 50 Sprechenden bleibt kurz. Speicher und
// Taktdauer stehen im Protokoll (-v) fuer die Messreihe 2263-9.
func TestKnoten200Beine(t *testing.T) {
	if testing.Short() {
		t.Skip("200 Beine - nur im vollen Lauf")
	}
	runtime.GC()
	var m0, m1 runtime.MemStats
	runtime.ReadMemStats(&m0)
	n := NewNode(Config{Name: "k", Tick: time.Hour, MaxLegs: 200})
	defer n.Close()
	type geraet struct {
		a, v *Leg
		src  *fwd.Source
	}
	var rooms []*Room
	var all []geraet
	for ri := 0; ri < 2; ri++ {
		ref := fmt.Sprintf("raum%d", ri)
		r, err := n.CreateRoom(ref, nil) // MaxPeers 50 (Standard)
		if err != nil {
			t.Fatal(err)
		}
		rooms = append(rooms, r)
		for i := 0; i < 50; i++ {
			id := fmt.Sprintf("p%d-%d", ri, i)
			pub := PubAudio | PubVideo
			a, err := r.AddLeg(Grant{Room: ref, Peer: id, Leg: id + "-a", Role: RoleAudio, Pub: pub}, PathTCP, Want{Audio: 3})
			if err != nil {
				t.Fatalf("Bein %d: %v", len(all)*2, err)
			}
			v, err := r.AddLeg(Grant{Room: ref, Peer: id, Leg: id + "-v", Role: RoleVideo, Pub: pub}, PathTCP,
				Want{Speaker: true, Tiles: 9})
			if err != nil {
				t.Fatalf("Bein %d: %v", len(all)*2+1, err)
			}
			src, err := a.AddSource(fwd.Audio, "a", false, uint32(1000+len(all)), 1, nil)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := v.AddSource(fwd.Video, "l", false, uint32(5000+len(all)), 0, nil); err != nil {
				t.Fatal(err)
			}
			all = append(all, geraet{a, v, src})
		}
	}
	if ld := n.Load(); ld.Legs != 200 || ld.Peers != 100 {
		t.Fatalf("Last %+v", ld)
	}
	// 201. Bein: Knoten voll; 51. Geraet in einem Raum: Raum voll
	if _, err := rooms[0].AddLeg(Grant{Room: "raum0", Peer: "p0-0", Leg: "extra", Role: RoleAudio}, PathTCP, Want{Audio: 3}); !errors.Is(err, ErrFull) {
		t.Fatalf("201. Bein: %v", err)
	}
	all[0].v.Close()
	if _, err := rooms[0].AddLeg(Grant{Room: "raum0", Peer: "neu", Leg: "neu-a", Role: RoleAudio}, PathTCP, Want{Audio: 3}); !errors.Is(err, ErrFull) {
		t.Fatalf("51. Geraet: %v", err)
	}
	runtime.GC()
	runtime.ReadMemStats(&m1)
	// alle sprechen (Gemurmel): Takt fuer beide Raeume
	now := fwd.Now()
	var worst, sum time.Duration
	const ticks = 50
	for k := 0; k < ticks; k++ {
		now += int64(200 * time.Millisecond)
		for i, g := range all {
			g.src.VoicePkts.Add(5)
			g.src.LevelSum.Add(uint32(5 * (40 + (i+k)%30)))
			g.src.LastVoice.Store(now)
		}
		t0 := time.Now()
		for _, r := range rooms {
			r.tickAt(now)
		}
		d := time.Since(t0)
		sum += d
		worst = max(worst, d)
	}
	heap := float64(m1.HeapInuse-m0.HeapInuse) / (1 << 20)
	t.Logf("200 Beine (2 x 50 Geraete, TCP): Heap +%.1f MiB (%.0f KiB je Bein), Takt beider Raeume im Mittel %s, hoechstens %s",
		heap, heap*1024/200, sum/ticks, worst)
	// je Empfaenger 3 Ton-Plaetze, alle belegt
	for _, g := range all[1:] {
		busy := 0
		for _, s := range g.a.Layout() {
			if s.Peer != "" {
				busy++
			}
		}
		if busy != 3 {
			t.Fatalf("%s: %d Ton-Plaetze belegt", g.a.Ref, busy)
		}
	}
	if sum/ticks > 50*time.Millisecond {
		t.Errorf("Takt %s im Mittel (Takt 200 ms)", sum/ticks)
	}
}

// 2.6 (Saga Bugs-DB 2447): "open" mit max_peers wirkt auch auf einen schon offenen Raum (Folgeknoten: das erste Bein
// kam vor der Nachricht aus Saga und legte den Raum mit dem Standard 50 an). Senken und Heben, laufende Geraete bleiben.
func TestRaumGrenzeNachtraeglich(t *testing.T) {
	n := NewNode(Config{Name: "k", Tick: time.Hour, MaxLegs: 200})
	defer n.Close()
	r, err := n.CreateRoom("raumspaet", nil) // Standard 50
	if err != nil {
		t.Fatal(err)
	}
	join := func(id string) error {
		_, err := r.AddLeg(Grant{Room: "raumspaet", Peer: id, Leg: id + "-a", Role: RoleAudio, Pub: PubAudio}, PathUDP, Want{Audio: 3})
		return err
	}
	for i := 0; i < 3; i++ {
		if err := join(fmt.Sprintf("p%d", i)); err != nil {
			t.Fatal(err)
		}
	}
	if r2, _ := n.CreateRoom("raumspaet", &Limits{MaxPeers: 3}); r2 != r {
		t.Fatal("anderer Raum")
	}
	if err := join("p3"); !errors.Is(err, ErrFull) {
		t.Fatalf("4. Geraet bei max_peers 3: %v", err)
	}
	if got := len(r.peers); got != 3 {
		t.Fatalf("Geraete nach dem Senken %d", got)
	}
	n.CreateRoom("raumspaet", &Limits{MaxPeers: 5})
	if err := join("p3"); err != nil {
		t.Fatalf("4. Geraet bei max_peers 5: %v", err)
	}
	n.CreateRoom("raumspaet", nil) // ohne Grenze: bleibt 5
	n.CreateRoom("raumspaet", &Limits{})
	if err := join("p4"); err != nil {
		t.Fatal(err)
	}
	if err := join("p5"); !errors.Is(err, ErrFull) {
		t.Fatalf("6. Geraet bei max_peers 5: %v", err)
	}
}
