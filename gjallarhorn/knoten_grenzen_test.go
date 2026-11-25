package main

import "testing"

// 2.6 (Bugs-DB 2416, 2263-3): neue Standards der Knoten-Grenzen - HORN_MAX_LEGS 200 (Tonanruf mit 50 Geraeten ueber
// TCP = 100 Beine, mit Reserve), HORN_MAX_PER_IP 40 (20 Geraete hinter einer Adresse). Ungueltige Werte fallen auf den
// Standard zurueck (bis 2.5 nahm der Knoten bei ungueltigem HORN_MAX_LEGS gar kein Bein an).
func TestKnotenGrenzenStandard(t *testing.T) {
	t.Setenv("HORN_DATA", t.TempDir())
	t.Setenv("HORN_MODE", "node")
	t.Setenv("HORN_TLS_SELF", "1")
	t.Setenv("HORN_SAGA", "")
	t.Setenv("HORN_SAGA_CTL", "")
	for _, c := range []struct {
		legs, perIP  string
		wantL, wantI int
	}{
		{"", "", 200, 40},
		{"abc", "0", 200, 40},
		{"-3", "x", 200, 40},
		{"500", "7", 500, 7},
		{"48", "12", 48, 12}, // Werte bis 2.5 weiter einstellbar
	} {
		t.Setenv("HORN_MAX_LEGS", c.legs)
		t.Setenv("HORN_MAX_PER_IP", c.perIP)
		cfg, err := loadConfig(false)
		if err != nil {
			t.Fatal(err)
		}
		if cfg.MaxLegs != c.wantL || cfg.MaxPerIP != c.wantI || cfg.maxPerIP() != c.wantI {
			t.Fatalf("HORN_MAX_LEGS=%q HORN_MAX_PER_IP=%q: %d/%d, erwartet %d/%d", c.legs, c.perIP, cfg.MaxLegs, cfg.MaxPerIP, c.wantL, c.wantI)
		}
	}
	if (&config{}).maxPerIP() != defMaxPerIP || defMaxLegs != 200 || defMaxPerIP != 40 {
		t.Fatal("Standard je Adresse ohne Wert")
	}
}
