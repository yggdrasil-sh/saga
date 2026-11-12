package main

import (
	"testing"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/gcc"
	"github.com/pion/interceptor/pkg/twcc"
	"github.com/pion/rtp"
	"github.com/pion/sdp/v3"
)

// Pruefung von HORN_BWE=eigen: Doppel gehen bei eigen an pion-GCC vorbei (bwe.go) - zaehlt dessen Verlust-Regler sie
// dann wirklich nicht mehr? Hier mit dem echten pion-Schaetzer: jedes vierte Paket ein Doppel, das der Empfaenger nicht
// bestaetigt; sonst kein Verlust. Gemessen wird der Verlustanteil, den pion-GCC fuehrt (averageLoss), ohne Doppel,
// bei mit (Doppel gehen durch GCC) und bei eigen (an GCC vorbei).
//
// Befund (haelt den Stand fest): Das Vorbeileiten aendert nichts. Die Rueckmeldung des Empfaengers fuehrt die
// transport-cc-Nummer des Doppels als nicht angekommen; pion findet sie nicht in seiner Liste gesendeter Pakete und
// reicht dafuer eine leere Bestaetigung weiter (interceptor internal/cc, unpack...Chunk), und der Verlust-Regler zaehlt
// jede Bestaetigung ohne Ankunftszeit als verloren (pkg/gcc, updateLossEstimate).
func TestPruefDoppelGCCVerlust(t *testing.T) {
	loss := map[string]float64{}
	for _, c := range []struct {
		name, mode string
		dup        bool
	}{{"ohne", bweEigen, false}, {"mit", bweMit, true}, {"eigen", bweEigen, true}} {
		est, err := gcc.NewSendSideBWE(gcc.SendSideBWEInitialBitrate(1_000_000), gcc.SendSideBWEPacer(gcc.NewNoOpPacer()))
		if err != nil {
			t.Fatal(err)
		}
		b := newBWEWrap(est, c.mode)
		info := &interceptor.StreamInfo{SSRC: 7, MimeType: mimeVP8, RTPHeaderExtensions: []interceptor.RTPHeaderExtension{{URI: sdp.TransportCCURI, ID: 5}}}
		w := b.AddStream(info, interceptor.RTPWriterFunc(func(_ *rtp.Header, pl []byte, _ interceptor.Attributes) (int, error) { return len(pl), nil }))
		rec := twcc.NewRecorder(1)
		tw := uint16(0)
		send := func(seq uint16, arrives bool) {
			h := &rtp.Header{Version: 2, PayloadType: 96, SSRC: 7, SequenceNumber: seq}
			tw++
			_ = h.SetExtension(5, []byte{byte(tw >> 8), byte(tw)})
			if _, err := w.Write(h, make([]byte, 1000), nil); err != nil {
				t.Fatal(err)
			}
			if arrives {
				rec.Record(7, tw, time.Now().UnixMicro())
			}
		}
		seq := uint16(1000)
		for i := 0; i < 300; i++ { // Bilder von drei Paketen alle 12 ms (pion-GCC bildet Gruppen nach Sendeabstand)
			for k := 0; k < 3; k++ {
				send(seq, true)
				if c.dup && k == 1 {
					send(seq, false) // Doppel: geht hinaus, der Empfaenger bestaetigt es nicht
				}
				seq++
			}
			time.Sleep(12 * time.Millisecond)
			if i%5 == 4 {
				if err := b.WriteRTCP(rec.BuildFeedbackPacket(), nil); err != nil {
					t.Fatal(err)
				}
			}
		}
		time.Sleep(100 * time.Millisecond)
		loss[c.name], _ = est.GetStats()["averageLoss"].(float64)
		cnt := b.m.Counters()
		t.Logf("%s: pion-GCC fuehrt Verlust %.1f %%, Ziel %d kbit/s; Detektor: verloren %d, Doppel unbestaetigt %d", c.name, loss[c.name]*100, est.GetTargetBitrate()/1000, cnt.Lost, cnt.DupUnacked)
		if cnt.Lost != 0 {
			t.Fatalf("%s: Detektor zaehlt %d verlorene Pakete", c.name, cnt.Lost)
		}
		_ = est.Close()
	}
	if loss["ohne"] > 0.01 || loss["mit"] < 0.15 {
		t.Fatalf("Aufbau stimmt nicht: Verlust ohne Doppel %.3f, bei mit %.3f", loss["ohne"], loss["mit"])
	}
	if loss["eigen"] < 0.15 {
		t.Fatalf("eigen: pion-GCC fuehrt nur noch %.1f %% Verlust - das Vorbeileiten wirkt jetzt; diesen Test und NODE.md anpassen", loss["eigen"]*100)
	}
}
