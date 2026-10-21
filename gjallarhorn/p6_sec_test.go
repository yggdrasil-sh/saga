package main

import (
	"testing"
	"time"
)

// F6 (Pruefung 2.5): SemVer-Rangfolge inkl. Vorabversionen.
func TestVersionRangfolge(t *testing.T) {
	order := []string{"2.4.9", "2.5.0-probe", "2.5.0-rc.1", "2.5.0-rc.2", "2.5.0-rc.10", "2.5.0", "2.5.1", "2.10.0", "10.0.0"}
	// Text gegen Text in ASCII-Folge: "probe" < "rc"
	for i := range order {
		for j := range order {
			c, ok := cmpVersion(order[i], order[j])
			if !ok || c != cmpInt(i, j) {
				t.Errorf("cmp(%s,%s)=%d,%v", order[i], order[j], c, ok)
			}
		}
	}
	for _, bad := range []string{"", "2.5", "2.5.0.1", "v2.5.0", "2.5.x", "2.5.0-", "2.5.0-rc..1", "a.b.c"} {
		if _, ok := cmpVersion(bad, "2.5.0"); ok {
			t.Errorf("%q als Version angenommen", bad)
		}
	}
	if newerThan("2.5.0", "2.5.0") || newerThan("2.5.0-rc.3", "2.5.0") || !newerThan("2.5.0", "2.5.0-rc.3") || newerThan("x", "2.5.0") {
		t.Error("newerThan")
	}
}

// F6: aeltere (auch korrekt signierte) Zielversion wird nicht geladen; der Waechter startet nie ein aelteres bin/current
// als das Image-Binaer und verwirft ein aelteres bin/next.
func TestUpdateKeinDowngrade(t *testing.T) {
	sk := testRelease(t)
	t.Setenv("HORN_WATCHED", "1")
	old := version
	version = "2.5.1"
	defer func() { version = old }()
	u, s, code := newTestUpdater(t, "http://127.0.0.1:1")
	for _, w := range []string{"2.5.0", "2.5.1-rc.4", "2.4.9", "2.5.1"} {
		u.target(w, false)
		u.step(time.Now())
		if u.want != "" || u.state != "" || s.draining.Load() || *code != -1 {
			t.Fatalf("%s: want=%q state=%q", w, u.want, u.state)
		}
	}
	u.target("2.5.2-rc.1", false)
	if u.want != "2.5.2-rc.1" {
		t.Fatalf("neuere Vorabversion nicht uebernommen: %q", u.want)
	}

	bd := binDir{t.TempDir()}
	bin := []byte("#!/bin/true\n")
	writeBin(t, bd.dir, "current", "2.5.0", bin, signRel(sk, "2.5.0", releaseArch(), bin))
	if b, _ := bd.pick("/self"); b != "/self" {
		t.Fatal("aelteres bin/current statt Image gestartet")
	}
	writeBin(t, bd.dir, "current", "2.5.1", bin, signRel(sk, "2.5.1", releaseArch(), bin))
	if b, _ := bd.pick("/self"); b != "/self" {
		t.Fatal("gleiches bin/current statt Image gestartet")
	}
	writeBin(t, bd.dir, "next", "2.5.0", bin, signRel(sk, "2.5.0", releaseArch(), bin))
	bd.promote()
	if b, _ := bd.pick("/self"); b != "/self" || readTrim(bd.p("current.ver")) != "2.5.1" {
		t.Fatal("aelteres bin/next uebernommen")
	}
	writeBin(t, bd.dir, "next", "2.5.2", bin, signRel(sk, "2.5.2", releaseArch(), bin))
	bd.promote()
	if b, v := bd.pick("/self"); b != bd.p("current") || v != "2.5.2" {
		t.Fatalf("neueres bin/next: %s %s", b, v)
	}
}
