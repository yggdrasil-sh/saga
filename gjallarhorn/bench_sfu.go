package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

// benchSFU misst den echten Weiterleiter: n Bots in einem Raum, die ersten
// video mit Kamera (TCP: Schichten l+m, UDP: l+m+h), drei davon sprechen
// unterschiedlich laut. Der Knoten waehlt Top-3-Ton, Sprecher, Last-N und
// Schichten selbst.
func benchSFU(hc *http.Client, base string, st0 nodeStats, n, video, secs, lastN int, path, data string) error {
	room := "bench-" + randHex(4)
	if err := postJSON(hc, base+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		return err
	}
	api, err := sfuBotAPI(path)
	if err != nil {
		return err
	}
	layers := []string{"l", "m"}
	legsPerBot := 2
	if path == "udp" {
		layers = []string{"l", "m", "h"}
		legsPerBot = 1
	}
	fmt.Fprintf(os.Stderr, "bench --sfu: %d Bots (%d mit Video, Last-N %d), Weg %s, %d s ...\n", n, video, lastN, path, secs)
	bots := make([]*sfuBot, n)
	t0 := time.Now()
	var wg sync.WaitGroup
	sem := make(chan struct{}, 4)
	var cerr atomic.Value
	for i := 0; i < n; i++ {
		b := &sfuBot{peer: fmt.Sprintf("bot%02d", i), path: path, video: i < video, layers: layers}
		lv := uint32(127)
		switch i {
		case 0:
			lv = 20
		case 1:
			lv = 35
		case 2:
			lv = 45
		}
		b.level.Store(lv)
		bots[i] = b
		wg.Add(1)
		go func() {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			if err := b.join(hc, base, api, room, lastN, "desktop"); err != nil {
				cerr.Store(err)
			}
		}()
	}
	wg.Wait()
	defer func() {
		for _, b := range bots {
			b.close()
		}
	}()
	if e, ok := cerr.Load().(error); ok && e != nil {
		return fmt.Errorf("bot: %w", e)
	}
	connected := 0
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		connected = 0
		for _, b := range bots {
			connected += b.connected()
		}
		if connected == n*legsPerBot {
			break
		}
		time.Sleep(200 * time.Millisecond)
	}
	connS := time.Since(t0).Seconds()
	if connected < n*legsPerBot {
		return fmt.Errorf("nur %d von %d Beinen verbunden", connected, n*legsPerBot)
	}
	for _, b := range bots {
		b.start()
	}
	time.Sleep(6 * time.Second) // Anlauf: Sprechererkennung, Schichten, Schaetzung
	s1, err := fetchStats(hc, base)
	if err != nil {
		return err
	}
	var rx1, lost1 uint64
	f1, fr1 := 0, 0
	for _, b := range bots {
		rx1 += b.rxPkts.Load()
		lost1 += b.rxLost.Load()
		f, _, fr := b.freezes()
		f1 += f
		fr1 += fr
	}
	time.Sleep(time.Duration(secs) * time.Second)
	s2, err := fetchStats(hc, base)
	if err != nil {
		return err
	}
	var rx2, lost2 uint64
	f2, fr2 := 0, 0
	for _, b := range bots {
		rx2 += b.rxPkts.Load()
		lost2 += b.rxLost.Load()
		f, _, fr := b.freezes()
		f2 += f
		fr2 += fr
	}
	dt := s2.Uptime - s1.Uptime
	pOut := float64(s2.PktsOut - s1.PktsOut)
	pIn := float64(s2.PktsIn - s1.PktsIn)
	cpu := float64(s2.CPUns - s1.CPUns)
	legs := n * legsPerBot
	r := benchResult{
		Kind: "bench", Mode: "sfu", Time: time.Now().UTC().Format(time.RFC3339), Version: version,
		N: n, Video: video, LastN: lastN, Path: path, Secs: round(dt, 1), Cores: numCPU(),
		Legs: legs, Connected: connected, ConnectS: round(connS, 2),
		CPUPct: round(100*cpu/(dt*1e9), 2), CPUusPkt: round(cpu/1000/pOut, 2), CPUusPktIO: round(cpu/1000/(pOut+pIn), 2),
		PPSIn: round(pIn/dt, 0), PPSOut: round(pOut/dt, 0),
		MbitIn: round(float64(s2.BytesIn-s1.BytesIn)*8/dt/1e6, 2), MbitOut: round(float64(s2.BytesOut-s1.BytesOut)*8/dt/1e6, 2),
		RSSBaseMB: round(float64(st0.RSS)/1e6, 1), RSSMB: round(float64(s2.RSS)/1e6, 1),
		RAMLegMB: round(float64(s2.RSS-st0.RSS)/1e6/float64(legs), 2), RAMPartMB: round(float64(s2.RSS-st0.RSS)/1e6/float64(n), 2),
		AllocsPkt: round(float64(s2.Mallocs-s1.Mallocs)/(pOut+pIn), 2),
		Dropped:   s2.FramesDropped - s1.FramesDropped, Stalls: s2.Stalls - s1.Stalls, Overflow: s2.Overflow - s1.Overflow,
		MaxQMs: round(s2.MaxQMs, 2), FwdP95us: s2.FwdP95us,
		BotPPS: round(float64(rx2-rx1)/dt, 0), ExpPPS: round(pOut/dt, 0),
		BBRConns: s2.BBRConns, TCPConns: s2.TCPConns,
		TIDSkipped: s2.TIDSkipped - s1.TIDSkipped, Switches: s2.Switches - s1.Switches, PLIp95ms: s2.PLIp95ms,
		Freezes: f2 - f1, BotFrames: fr2 - fr1,
	}
	if got := rx2 - rx1; got > 0 {
		r.BotLossPct = round(100*float64(lost2-lost1)/float64(got+lost2-lost1), 3)
	}
	var ld struct {
		Load struct {
			DownKbit int `json:"down_kbit"`
			Legs     int `json:"legs"`
		} `json:"load"`
	}
	if resp, err := hc.Get(base + "/api/dev/load"); err == nil {
		_ = json.NewDecoder(resp.Body).Decode(&ld)
		resp.Body.Close()
	}
	if ld.Load.Legs > 0 {
		r.EstKbitLeg = ld.Load.DownKbit / ld.Load.Legs
	}
	printBench(os.Stdout, r)
	if dir := filepath.Join(data, "results"); dirWritable(dir) {
		name := filepath.Join(dir, "bench-sfu-"+time.Now().UTC().Format("20060102-150405")+".json")
		b, _ := json.MarshalIndent(r, "", " ")
		if err := os.WriteFile(name, b, 0o640); err == nil {
			fmt.Fprintf(os.Stderr, "gespeichert: %s\n", name)
		}
	}
	return nil
}
