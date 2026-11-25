package main

import (
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"
	"time"
)

// Vektoren saga-horn/v1 (tests/vectors/horn_e2e.json) gegen den Frame-E2E des Mess-Bots horn-bot (2263-2).
type hornVec struct {
	Keys []struct {
		N  int    `json:"n"`
		K  string `json:"K"`
		FK string `json:"fk"`
	} `json:"keys"`
	Frames []struct {
		Name   string `json:"name"`
		Kind   string `json:"kind"`
		Type   string `json:"type"`
		H      int    `json:"h"`
		KeyN   int    `json:"key_n"`
		SSRC   uint32 `json:"ssrc"`
		TS     uint32 `json:"ts"`
		Ctr    uint32 `json:"ctr"`
		Plain  string `json:"plain"`
		IV     string `json:"iv"`
		Out    string `json:"out"`
		IVMode string `json:"iv_mode"`
	} `json:"frames"`
	Cases []struct {
		Name  string `json:"name"`
		Have  []int  `json:"have"`
		Steps []struct {
			Data   string `json:"data"`
			Kind   string `json:"kind"`
			Type   string `json:"type"`
			At     int    `json:"at"`
			Expect string `json:"expect"`
			Plain  string `json:"plain"`
		} `json:"steps"`
	} `json:"cases"`
	Window struct {
		Steps []struct {
			TS uint32 `json:"ts"`
			OK bool   `json:"ok"`
		} `json:"steps"`
	} `json:"window"`
}

func loadHornVec(t *testing.T) hornVec {
	b, err := os.ReadFile("../tests/vectors/horn_e2e.json")
	if err != nil {
		t.Fatal(err)
	}
	var v hornVec
	if err := json.Unmarshal(b, &v); err != nil {
		t.Fatal(err)
	}
	return v
}

func unhex(t *testing.T, s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestHornBotE2EVektorenSchluessel(t *testing.T) {
	v := loadHornVec(t)
	for i, k := range v.Keys {
		K := unhex(t, k.K)
		if got := hex.EncodeToString(e2eFK(K)); got != k.FK {
			t.Fatalf("FK n=%d: %s", k.N, got)
		}
		if i+1 < len(v.Keys) && hex.EncodeToString(e2eRatchet(K)) != v.Keys[i+1].K {
			t.Fatalf("Ratsche n=%d", k.N)
		}
	}
}

func TestHornBotE2EVektorenRahmen(t *testing.T) {
	v := loadHornVec(t)
	keys := map[int][]byte{}
	for _, k := range v.Keys {
		keys[k.N] = unhex(t, k.K)
	}
	for _, f := range v.Frames {
		video, key := f.Kind == "video", f.Type == "key"
		if e2eHeader(video, key) != f.H {
			t.Fatalf("%s: Kopf %d", f.Name, f.H)
		}
		// Empfang
		r := newE2ERecv()
		r.setKey("a", f.KeyN, keys[f.KeyN], "aaaaaaaaaaaaaaaa")
		pl, err := r.open("a", video, key, unhex(t, f.Out))
		if err != nil || !bytes.Equal(pl, unhex(t, f.Plain)) {
			t.Fatalf("%s: oeffnen %v", f.Name, err)
		}
		if f.IVMode == "fc" {
			continue
		}
		// Senden bitgleich
		s := newE2ESender()
		s.set(f.KeyN, keys[f.KeyN], 0)
		s.ctr[[2]uint32{uint32(f.KeyN), f.SSRC}] = f.Ctr
		out := s.seal(video, key, f.SSRC, f.TS, unhex(t, f.Plain))
		if hex.EncodeToString(out) != f.Out {
			t.Fatalf("%s: versiegelt anders", f.Name)
		}
		var iv [12]byte
		binary.BigEndian.PutUint32(iv[0:], f.SSRC)
		binary.BigEndian.PutUint32(iv[4:], f.TS)
		binary.BigEndian.PutUint32(iv[8:], f.Ctr)
		if hex.EncodeToString(iv[:]) != f.IV {
			t.Fatalf("%s: IV", f.Name)
		}
	}
}

func TestHornBotE2EVektorenFaelle(t *testing.T) {
	v := loadHornVec(t)
	keys := map[int][]byte{}
	for _, k := range v.Keys {
		keys[k.N] = unhex(t, k.K)
	}
	t0 := time.Unix(1_800_000_000, 0)
	for _, c := range v.Cases {
		r := newE2ERecv()
		var now time.Time
		r.now = func() time.Time { return now }
		for _, n := range c.Have {
			r.setKey("a", n, keys[n], "aaaaaaaaaaaaaaaa")
		}
		for i, s := range c.Steps {
			now = t0.Add(time.Duration(s.At) * time.Millisecond)
			pl, err := r.open("a", s.Kind == "video", s.Type == "key", unhex(t, s.Data))
			got := "ok"
			if err != nil {
				got = err.Error()
			}
			if got != s.Expect {
				t.Fatalf("%s Schritt %d: %s statt %s", c.Name, i, got, s.Expect)
			}
			if err == nil && s.Plain != "" && !bytes.Equal(pl, unhex(t, s.Plain)) {
				t.Fatalf("%s Schritt %d: Klartext", c.Name, i)
			}
		}
	}
}

func TestHornBotE2EVektorenFenster(t *testing.T) {
	v := loadHornVec(t)
	var w replayWindow
	for i, s := range v.Window.Steps {
		ok := w.check(s.TS)
		if ok != s.OK {
			t.Fatalf("Schritt %d ts %d: %v statt %v", i, s.TS, ok, s.OK)
		}
		if ok {
			w.accept(s.TS)
		}
	}
}

// Sender -> Empfaenger mit Schluesselwechsel (Verlassen: neuer Schluessel nach Wartezeit) und Ratsche (Beitritt).
func TestHornBotE2EWechsel(t *testing.T) {
	s, r := newE2ESender(), newE2ERecv()
	k0 := e2eRandKey()
	s.set(0, k0, 0)
	r.setKey("x", 0, k0, "0123456789abcdef")
	var gens []int
	r.onGen = func(_ string, n int, _ time.Time) { gens = append(gens, n) }
	frame := append([]byte{0x31, 0x0c, 0x00}, bytes.Repeat([]byte{7}, 100)...)
	if _, err := r.open("x", true, false, s.seal(true, false, 1, 3000, frame)); err != nil {
		t.Fatal(err)
	}
	k1 := e2eRatchet(k0) // Beitritt: Sender ratscht, Empfaenger ratscht beim ersten Rahmen
	s.set(1, k1, 0)
	if _, err := r.open("x", true, false, s.seal(true, false, 1, 6000, frame)); err != nil {
		t.Fatal("Ratsche:", err)
	}
	k2 := e2eRandKey()
	s.set(2, k2, 50*time.Millisecond)
	if s.gen() != 1 {
		t.Fatal("zu frueh umgeschaltet")
	}
	r.setKey("x", 2, k2, "0123456789abcdef")
	time.Sleep(60 * time.Millisecond)
	if _, err := r.open("x", true, false, s.seal(true, false, 1, 9000, frame)); err != nil || s.gen() != 2 {
		t.Fatal("Wechsel:", err, s.gen())
	}
	time.Sleep(10 * time.Millisecond)
	if len(gens) != 3 {
		t.Fatalf("Generationen %v", gens)
	}
}
