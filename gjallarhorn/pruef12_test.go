package main

import (
	"sync"
	"testing"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/gcc"
	"github.com/pion/interceptor/pkg/twcc"
	"github.com/pion/rtp"
	"github.com/pion/sdp/v3"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Pruefung von HORN_BWE=eigen: Doppel gehen bei eigen an pion-GCC vorbei (bwe.go) - zaehlt dessen Verlust-Regler sie
// dann wirklich nicht mehr? Hier mit dem echten pion-Schaetzer: jedes vierte Paket ein Doppel, das der Empfaenger nicht
// bestaetigt; sonst kein Verlust. Gemessen wird der Verlustanteil, den pion-GCC fuehrt (averageLoss), ohne Doppel,
// bei mit (Doppel gehen durch GCC) und bei eigen (an GCC vorbei) - und mit dem Abgleich des Weiterleiters davor
// (abgleich: dieselbe Quelle mit 25 % Doppeln laeuft durch fwd.Source und einen Ausgang in die Huelle).
//
// Befund (haelt den Stand fest): Das Vorbeileiten aendert nichts. Die Rueckmeldung des Empfaengers fuehrt die
// transport-cc-Nummer des Doppels als nicht angekommen; pion findet sie nicht in seiner Liste gesendeter Pakete und
// reicht dafuer eine leere Bestaetigung weiter (interceptor internal/cc, unpack...Chunk), und der Verlust-Regler zaehlt
// jede Bestaetigung ohne Ankunftszeit als verloren (pkg/gcc, updateLossEstimate). Es hilft nur, die Doppel gar nicht
// erst hinauszuschicken: Mit dem Abgleich je Quelle (fwd.Source.admit) erreicht keines die Huelle, pion-GCC fuehrt
// keinen Verlust, und das Ziel bleibt, wo es ohne Doppel steht (rund 1400 statt 134 kbit/s).
func TestPruefDoppelGCCVerlust(t *testing.T) {
	loss := map[string]float64{}
	ziel := map[string]int{}
	for _, c := range []struct {
		name, mode string
		dup, fwd   bool
	}{{"ohne", bweEigen, false, false}, {"mit", bweMit, true, false}, {"eigen", bweEigen, true, false}, {"abgleich", bweEigen, true, true}} {
		est, err := gcc.NewSendSideBWE(gcc.SendSideBWEInitialBitrate(1_000_000), gcc.SendSideBWEPacer(gcc.NewNoOpPacer()))
		if err != nil {
			t.Fatal(err)
		}
		b := newBWEWrap(est, c.mode)
		info := &interceptor.StreamInfo{SSRC: 7, MimeType: mimeVP8, RTPHeaderExtensions: []interceptor.RTPHeaderExtension{{URI: sdp.TransportCCURI, ID: 5}}}
		w := b.AddStream(info, interceptor.RTPWriterFunc(func(_ *rtp.Header, pl []byte, _ interceptor.Attributes) (int, error) { return len(pl), nil }))
		rec := twcc.NewRecorder(1)
		var mu sync.Mutex // rec, tw: im Fall abgleich schreibt der Schreiber des Beins
		tw := uint16(0)
		out := 0
		// write: ein Paket in die Huelle; arrives = der Empfaenger zaehlt und bestaetigt es
		write := func(h *rtp.Header, pl []byte, arrives bool) {
			mu.Lock()
			defer mu.Unlock()
			tw++
			out++
			_ = h.SetExtension(5, []byte{byte(tw >> 8), byte(tw)})
			if _, err := w.Write(h, pl, nil); err != nil {
				t.Error(err)
			}
			if arrives {
				rec.Record(7, tw, time.Now().UnixMicro())
			}
		}
		send := func(seq uint16, start, end, arrives bool) {
			write(&rtp.Header{Version: 2, PayloadType: 96, SSRC: 7, SequenceNumber: seq, Marker: end}, make([]byte, 1000), arrives)
		}
		var src *fwd.Source
		if c.fwd {
			// Quelle -> Weiterleiter -> Huelle. Der Empfaenger bestaetigt jede RTP-Folgenummer nur einmal (ein zweites
			// Exemplar verwirft er am Wiederholungsschutz) - mit dem Abgleich kommt keines an.
			leg := fwd.NewLeg()
			defer leg.Close()
			o := leg.NewOut("v", "p", fwd.Video, fwd.ClassSpeaker)
			seen := map[uint16]bool{}
			o.Bind(bweTestW(func(h *rtp.Header, pl []byte) {
				first := !seen[h.SequenceNumber]
				seen[h.SequenceNumber] = true
				write(h, pl, first)
			}), 7, 96)
			src = &fwd.Source{Owner: "p", Layer: "m", Kind: fwd.Video, Clock: 90000}
			o.Assign(src)
			send = func(seq uint16, start, end, _ bool) {
				pkt := make([]byte, 12+1000)
				pkt[0], pkt[1] = 0x80, 96
				if end {
					pkt[1] |= 0x80
				}
				pkt[2], pkt[3] = byte(seq>>8), byte(seq)
				ts := uint32(seq/3) * 1080
				pkt[4], pkt[5], pkt[6], pkt[7] = byte(ts>>24), byte(ts>>16), byte(ts>>8), byte(ts)
				pkt[11] = 9
				if start {
					pkt[12] = 0x10 // VP8: Rahmenanfang
				}
				if seq >= 1003 {
					pkt[13] = 1 // kein Schluesselbild
				}
				src.Inject(pkt)
			}
		}
		seq := uint16(1000)
		for i := 0; i < 300; i++ { // Bilder von drei Paketen alle 12 ms (pion-GCC bildet Gruppen nach Sendeabstand)
			for k := 0; k < 3; k++ {
				send(seq, k == 0, k == 2, true)
				if c.dup && k == 1 {
					send(seq, k == 0, k == 2, false) // Doppel: geht hinaus, der Empfaenger bestaetigt es nicht
				}
				seq++
			}
			time.Sleep(12 * time.Millisecond)
			if i%5 == 4 {
				mu.Lock()
				fb := rec.BuildFeedbackPacket()
				mu.Unlock()
				if err := b.WriteRTCP(fb, nil); err != nil {
					t.Fatal(err)
				}
			}
		}
		time.Sleep(100 * time.Millisecond)
		loss[c.name], _ = est.GetStats()["averageLoss"].(float64)
		ziel[c.name] = est.GetTargetBitrate() / 1000
		cnt := b.m.Counters()
		mu.Lock()
		n := out
		mu.Unlock()
		t.Logf("%s: pion-GCC fuehrt Verlust %.1f %%, Ziel %d kbit/s; %d Pakete in die Huelle; Detektor: verloren %d, Doppel unbestaetigt %d", c.name, loss[c.name]*100, ziel[c.name], n, cnt.Lost, cnt.DupUnacked)
		if cnt.Lost != 0 {
			t.Fatalf("%s: Detektor zaehlt %d verlorene Pakete", c.name, cnt.Lost)
		}
		if c.fwd {
			if n != 900 || src.Dups.Load() != 300 || cnt.DupUnacked != 0 {
				t.Fatalf("abgleich: %d Pakete in die Huelle (erwartet 900), %d Doppel der Quelle verworfen (erwartet 300), %d Doppel in der Huelle", n, src.Dups.Load(), cnt.DupUnacked)
			}
		}
		_ = est.Close()
	}
	if loss["ohne"] > 0.01 || loss["mit"] < 0.15 {
		t.Fatalf("Aufbau stimmt nicht: Verlust ohne Doppel %.3f, bei mit %.3f", loss["ohne"], loss["mit"])
	}
	if loss["eigen"] < 0.15 {
		t.Fatalf("eigen: pion-GCC fuehrt nur noch %.1f %% Verlust - das Vorbeileiten wirkt jetzt; diesen Test und NODE.md anpassen", loss["eigen"]*100)
	}
	if loss["abgleich"] > 0.01 || ziel["abgleich"] < ziel["ohne"]*9/10 || ziel["mit"] > ziel["ohne"]/2 {
		t.Fatalf("mit Abgleich: Verlust %.1f %%, Ziel %d kbit/s - ohne Doppel %d, mit Doppeln ohne Abgleich %d", loss["abgleich"]*100, ziel["abgleich"], ziel["ohne"], ziel["mit"])
	}
}

// bweTestW: Schreibziel eines Ausgangs fuer Tests (fwd.Writer).
type bweTestW func(h *rtp.Header, pl []byte)

func (f bweTestW) WriteRTP(h *rtp.Header, pl []byte) (int, error) { f(h, pl); return len(pl), nil }
