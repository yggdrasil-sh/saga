package main

import (
	"sort"
	"strings"
	"testing"

	"github.com/pion/webrtc/v4"
)

// Pruefung tcpstau: Das Signal aendert die Aushandlung nicht. transport-cc (Kopf-Erweiterung und rtcp-fb) war auf den
// TCP-Wegen schon vorher in der Antwort (ConfigureTWCCSender, Rueckmeldung fuer den Uplink); mit HORN_TCP_STAU an
// traegt nur der Downlink jetzt die Nummern. Die Antwort an das Geraet muss in beiden Faellen dieselben extmap- und
// rtcp-fb-Zeilen haben.
func TestTCPStauSDPGleich(t *testing.T) {
	cli, err := webrtc.NewPeerConnection(webrtc.Configuration{}) // Standard-Medien wie ein Browser: transport-cc angeboten
	if err != nil {
		t.Fatal(err)
	}
	defer cli.Close()
	for _, k := range []webrtc.RTPCodecType{webrtc.RTPCodecTypeAudio, webrtc.RTPCodecTypeVideo} {
		if _, err := cli.AddTransceiverFromKind(k, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionSendrecv}); err != nil {
			t.Fatal(err)
		}
	}
	offer, err := cli.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(offer.SDP, "transport-wide-cc") {
		t.Fatal("Angebot ohne transport-cc")
	}
	answer := func(mode string) []string {
		api, err := newSFUAPI(webrtc.SettingEngine{}, false, nil, &tcpHook{mode: mode, on: true})
		if err != nil {
			t.Fatal(err)
		}
		pc, err := api.NewPeerConnection(webrtc.Configuration{})
		if err != nil {
			t.Fatal(err)
		}
		defer pc.Close()
		if err := pc.SetRemoteDescription(offer); err != nil {
			t.Fatal(err)
		}
		a, err := pc.CreateAnswer(nil)
		if err != nil {
			t.Fatal(err)
		}
		var out []string
		for _, ln := range strings.Split(a.SDP, "\r\n") {
			if strings.HasPrefix(ln, "a=extmap:") || strings.HasPrefix(ln, "a=rtcp-fb:") || strings.HasPrefix(ln, "m=") {
				out = append(out, ln)
			}
		}
		sort.Strings(out)
		return out
	}
	an, aus := answer(""), answer(tcpStauOff)
	if strings.Join(an, "\n") != strings.Join(aus, "\n") {
		t.Fatalf("Antwort unterscheidet sich:\nan:  %q\naus: %q", an, aus)
	}
	if !strings.Contains(strings.Join(aus, "\n"), "transport-wide-cc") {
		t.Fatalf("transport-cc schon vorher nicht ausgehandelt? %q", aus)
	}
}
