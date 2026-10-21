package main

import (
	"bufio"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

// benchCascade (P9): zwei Knoten, die Haelfte der Bots an jedem, eine Bruecke dazwischen (Entwicklungsmodus, Loopback).
// Gemessen: CPU beider Knoten, Rate ueber die Bruecke, Empfang und Einfrieren bei den Bots (alle sehen alle).
func benchCascade(exe string, n, video, secs, lastN int, path, data string) error {
	legsPerBot := 2
	if path == "udp" {
		legsPerBot = 1
	}
	start := func(tmp string) (string, func(), error) {
		cmd := exec.Command(exe, "bench-node")
		cmd.Env = append(os.Environ(), "HORN_MODE=node", "HORN_DEV=1", "HORN_LISTEN=127.0.0.1:0", "HORN_ICE_IPS=127.0.0.1", "HORN_DATA="+tmp,
			"HORN_TLS_SELF=1", "HORN_PROXY_PROTOCOL=0", "HORN_TLS_CERT=", "HORN_PUBLIC_IP=",
			fmt.Sprintf("HORN_MAX_LEGS=%d", n*legsPerBot+8), "HORN_UDP="+map[bool]string{true: "1", false: "0"}[path == "udp"])
		out, err := cmd.StdoutPipe()
		if err != nil {
			return "", nil, err
		}
		cmd.Stderr = os.Stderr
		if err := cmd.Start(); err != nil {
			return "", nil, err
		}
		stop := func() {
			_ = cmd.Process.Signal(os.Interrupt)
			done := make(chan struct{})
			go func() { _ = cmd.Wait(); close(done) }()
			select {
			case <-done:
			case <-time.After(5 * time.Second):
				_ = cmd.Process.Kill()
				<-done
			}
		}
		port := 0
		sc := bufio.NewScanner(out)
		for sc.Scan() {
			if _, err := fmt.Sscanf(sc.Text(), "LISTEN %d", &port); err == nil {
				break
			}
		}
		go func() { _, _ = io.Copy(io.Discard, out) }()
		if port == 0 {
			stop()
			return "", nil, errors.New("bench-knoten startet nicht")
		}
		return fmt.Sprintf("https://127.0.0.1:%d", port), stop, nil
	}
	tmp, err := os.MkdirTemp("", "horn-bench-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(tmp)
	var bases [2]string
	for i := range bases {
		d := filepath.Join(tmp, fmt.Sprint(i))
		_ = os.MkdirAll(d, 0o700)
		b, stop, err := start(d)
		if err != nil {
			return err
		}
		defer stop()
		bases[i] = b
	}
	hc := &http.Client{Timeout: 20 * time.Second, Transport: &http.Transport{Proxy: nil,
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}} // nur Loopback zu den eigenen Kindprozessen
	rooms := [2]string{"bench-a-" + randHex(4), "bench-b-" + randHex(4)}
	for i := range bases {
		if err := postJSON(hc, bases[i]+"/api/dev/room", map[string]string{"room": rooms[i]}, &struct{}{}); err != nil {
			return err
		}
	}
	api, err := sfuBotAPI(path)
	if err != nil {
		return err
	}
	layers := []string{"l", "m"}
	if path == "udp" {
		layers = []string{"l", "m", "h"}
	}
	fmt.Fprintf(os.Stderr, "bench --sfu --cascade: %d Bots auf 2 Knoten (%d mit Video, Last-N %d), Weg %s, %d s ...\n", n, video, lastN, path, secs)
	bots := make([]*sfuBot, n)
	var wg sync.WaitGroup
	sem := make(chan struct{}, 4)
	var cerr atomic.Value
	t0 := time.Now()
	for i := 0; i < n; i++ {
		side := i % 2
		b := &sfuBot{peer: fmt.Sprintf("bot%c%02d", 'a'+side, i), path: path, video: i < video, layers: layers}
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
			if err := b.join(hc, bases[side], api, rooms[side], lastN, "desktop"); err != nil {
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
	if err := postJSON(hc, bases[0]+"/api/dev/cascade", map[string]string{"bridge": "bench-bruecke-" + randHex(4), "url": bases[1],
		"room": rooms[0], "peerroom": rooms[1]}, &struct{}{}); err != nil {
		return err
	}
	deadline := time.Now().Add(30 * time.Second)
	connected := 0
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
	if connected < n*legsPerBot {
		return fmt.Errorf("nur %d von %d Beinen verbunden", connected, n*legsPerBot)
	}
	connS := time.Since(t0).Seconds()
	for _, b := range bots {
		b.start()
	}
	time.Sleep(8 * time.Second) // Anlauf: Bruecke, Nachfrage, Sprecher, Schichten
	bridgeBytes := func(base string) (uint64, uint64) {
		var r struct {
			Bridges map[string]struct {
				BytesIn  uint64 `json:"bytes_in"`
				BytesOut uint64 `json:"bytes_out"`
			} `json:"bridges"`
		}
		resp, err := hc.Get(base + "/api/dev/bridges")
		if err != nil {
			return 0, 0
		}
		defer resp.Body.Close()
		_ = json.NewDecoder(resp.Body).Decode(&r)
		var in, out uint64
		for _, x := range r.Bridges {
			in += x.BytesIn
			out += x.BytesOut
		}
		return in, out
	}
	var s1 [2]nodeStats
	for i := range bases {
		if s1[i], err = fetchStats(hc, bases[i]); err != nil {
			return err
		}
	}
	bi1, bo1 := bridgeBytes(bases[0])
	var rx1 uint64
	f1, fr1 := 0, 0
	for _, b := range bots {
		rx1 += b.rxPkts.Load()
		f, _, fr := b.freezes()
		f1 += f
		fr1 += fr
	}
	time.Sleep(time.Duration(secs) * time.Second)
	var s2 [2]nodeStats
	for i := range bases {
		if s2[i], err = fetchStats(hc, bases[i]); err != nil {
			return err
		}
	}
	bi2, bo2 := bridgeBytes(bases[0])
	var rx2 uint64
	f2, fr2 := 0, 0
	seenAll := 0
	for _, b := range bots {
		rx2 += b.rxPkts.Load()
		f, _, fr := b.freezes()
		f2 += f
		fr2 += fr
		au, _ := b.seen()
		if len(au) >= 3 || len(au) >= n-1 {
			seenAll++
		}
	}
	dt := s2[0].Uptime - s1[0].Uptime
	type side struct {
		CPUPct   float64 `json:"cpu_pct"`
		CPUusPkt float64 `json:"cpu_us_per_pkt"`
		PPSOut   float64 `json:"pps_out"`
		MbitOut  float64 `json:"mbit_out"`
		Dropped  uint64  `json:"frames_dropped"`
		RSSMB    float64 `json:"rss_mb"`
	}
	var sides [2]side
	for i := range sides {
		cpu := float64(s2[i].CPUns - s1[i].CPUns)
		pOut := float64(s2[i].PktsOut - s1[i].PktsOut)
		sides[i] = side{CPUPct: round(100*cpu/(dt*1e9), 2), CPUusPkt: round(cpu/1000/max(pOut, 1), 2), PPSOut: round(pOut/dt, 0),
			MbitOut: round(float64(s2[i].BytesOut-s1[i].BytesOut)*8/dt/1e6, 2), Dropped: s2[i].FramesDropped - s1[i].FramesDropped,
			RSSMB: round(float64(s2[i].RSS)/1e6, 1)}
	}
	res := map[string]any{"kind": "bench", "mode": "sfu-cascade", "time": time.Now().UTC().Format(time.RFC3339), "version": version,
		"n": n, "video": video, "lastn": lastN, "path": path, "secs": round(dt, 1), "cores": numCPU(), "connect_s": round(connS, 2),
		"node_a": sides[0], "node_b": sides[1], "bridge_mbit_a_to_b": round(float64(bo2-bo1)*8/dt/1e6, 2),
		"bridge_mbit_b_to_a": round(float64(bi2-bi1)*8/dt/1e6, 2), "bot_rx_pps": round(float64(rx2-rx1)/dt, 0),
		"bot_freezes": f2 - f1, "bot_frames": fr2 - fr1, "bots_hear_others": seenAll}
	b, _ := json.MarshalIndent(res, "", " ")
	fmt.Println(string(b))
	if dir := filepath.Join(data, "results"); dirWritable(dir) {
		name := filepath.Join(dir, "bench-cascade-"+time.Now().UTC().Format("20060102-150405")+".json")
		if err := os.WriteFile(name, b, 0o640); err == nil {
			fmt.Fprintf(os.Stderr, "gespeichert: %s\n", name)
		}
	}
	return nil
}
