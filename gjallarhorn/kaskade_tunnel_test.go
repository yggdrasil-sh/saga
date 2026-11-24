package main

import (
	"fmt"
	"testing"
	"time"
)

// Bugs-DB 2357: eingebauter Knoten im Tunnel-Betrieb (Weg "ws": Rahmen-Weg der Browser B2 und A3-Tunnel der Apps),
// Kaskade zu einem zweiten Knoten (Bifroest, A waehlt, B nimmt an), dort ein Geraet ueber UDP (Android im Feld).
// Gemessen je Richtung und Spur: Ton und Bild vom UDP-Geraet bei beiden Tunnel-Beinen (Rahmen bzw. RTP) und
// umgekehrt, Pegel ueber die Bruecke. Im Feld sah das Web das Bild des Android-Geraets, hoerte es aber nicht.
func TestKaskadeTonZuTunnelBeinen(t *testing.T) {
	a, ahs, url := tunnelServer(t)
	a.iceTCP = false
	b := testNodeServer(t, true)
	ba, bb := ahs.URL, "https://"+b.mux.Addr().String()
	hc := httpClient()
	if err := postJSON(hc, ba+"/api/dev/room", map[string]string{"room": "raum-tunl"}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	// B: "Android" ueber UDP (ein Bein, Ton + Bild)
	rb := startBotsAs(t, bb, "raum-udpb", "andr", []string{"udp"}, 2, "l", "m")
	rb[0].level.Store(40)
	// A: Browser ueber den Rahmen-Weg (Ton- und Bild-Leitung) und App ueber den A3-Tunnel (ICE-TCP im WebSocket)
	web := joinFramesDev(t, ba, url, "raum-tunl", "web", 0)
	user, cred := tunnelCreds(t, a)
	api, err := sfuBotAPI("ws")
	if err != nil {
		t.Fatal(err)
	}
	app := &sfuBot{peer: "app", path: "ws", video: true, layers: []string{"l"}}
	app.level.Store(90)
	app.bend = func(role, sdp string) string { return bendTo(sdp, appTunnel(t, url, user, cred, role)) }
	if err := app.join(hc, ba, api, "raum-tunl", 1, "desktop"); err != nil {
		t.Fatal(err)
	}
	defer app.close()
	if !waitFor(10*time.Second, func() bool { return app.connected() == 2 }) {
		t.Fatal("App nicht verbunden")
	}
	app.start()
	stop := make(chan struct{})
	defer close(stop)
	go web.a.sender(stop, false, 0)
	go web.v.sender(stop, true, 1200)
	if err := postJSON(hc, ba+"/api/dev/cascade", map[string]string{"bridge": "bruecke2357", "url": bb, "room": "raum-tunl",
		"peerroom": "raum-udpb"}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	count := func(rx *frClient, csrc uint32) (n, lv int) {
		for _, f := range rx.frames() {
			if f.csrc == csrc {
				n++
				if f.x&0x7f == 40 {
					lv++
				}
			}
		}
		return
	}
	var why string
	ok := waitFor(20*time.Second, func() bool {
		ida, idb := roomCSRCs(t, ba, "raum-tunl"), roomCSRCs(t, bb, "raum-udpb")
		if len(ida) != 3 || len(idb) != 3 {
			why = fmt.Sprintf("teilnehmende a=%v b=%v", ida, idb)
			return false
		}
		ca := ida["andr0"]
		wa, wlv := count(web.a, ca)
		wv, _ := count(web.v, ca)
		aau, avi := app.seen()
		bau, bvi := rb[0].seen()
		why = fmt.Sprintf("web<-andr Ton %d (Pegel %d) Bild %d; app<-andr Ton %d Bild %d (Pegel %v); andr<-web Ton %d Bild %d; andr<-app Ton %d Bild %d",
			wa, wlv, wv, aau[ca], avi[ca], app.levels()[ca], bau[idb["web"]], bvi[idb["web"]], bau[idb["app"]], bvi[idb["app"]])
		return wa > 30 && wlv > 20 && wv > 10 && aau[ca] > 30 && avi[ca] > 10 && app.levels()[ca] == 40 &&
			bau[idb["web"]] > 30 && bvi[idb["web"]] > 10 && bau[idb["app"]] > 30 && bvi[idb["app"]] > 10
	})
	t.Log(why)
	if !ok {
		t.Fatal(why)
	}
}
