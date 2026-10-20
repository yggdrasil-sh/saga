package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Strukturen der gespeicherten Messlaeufe (siehe web/app.js).
type pRTT struct {
	P50 *float64 `json:"p50"`
	P95 *float64 `json:"p95"`
	N   int      `json:"n"`
}

type pPhase struct {
	Path      string  `json:"path"`
	Profile   string  `json:"profile"`
	OK        bool    `json:"ok"`
	Reason    string  `json:"reason"`
	ConnectMs []*int  `json:"connect_ms"`
	RTT       pRTT    `json:"rtt_ms"`
	WSRTT     pRTT    `json:"ws_rtt_ms"`
	Secs      float64 `json:"secs"`
	Audio     *struct {
		Conceal *float64 `json:"conceal_pct"`
		JB      *float64 `json:"jb_ms"`
		Loss    *float64 `json:"loss_pct"`
	} `json:"audio"`
	Video *struct {
		Tracks  int      `json:"tracks"`
		Frz5    *float64 `json:"freezes_per5min_track"`
		FrzS5   *float64 `json:"freeze_s_per5min_track"`
		FPS     *float64 `json:"fps"`
		LossPct *float64 `json:"loss_pct"`
		Freezes *float64 `json:"freezes"`
		FreezeS *float64 `json:"freeze_s"`
	} `json:"video"`
	Down struct {
		Stable float64 `json:"stable"`
	} `json:"down_kbps"`
	Up struct {
		Stable float64 `json:"stable"`
	} `json:"up_kbps"`
	Node []legStats `json:"node"`
	// ungueltig (Seite im Hintergrund, kein Ton): zaehlt im Go/No-Go nicht
	Invalid      string   `json:"invalid"`
	ExpectedDown *float64 `json:"expected_down_kbps"`
	JBT          any      `json:"jbt"`
	TileProfile  string   `json:"tile_profile"`
}

// valid: verbunden und gueltig gemessen.
func (p pPhase) valid() bool { return p.OK && p.Invalid == "" }

// expectedDown: erwartete Downlink-Rate in kbit/s laut Profil. Aeltere
// Ergebnisse ohne Messwert: aus den Raten der Messseite (Ton 40 kbit/s,
// Sprecher 1,5 Mbit/s, Kacheln 250 kbit/s).
func (p pPhase) expectedDown() float64 {
	if p.ExpectedDown != nil && *p.ExpectedDown > 0 {
		return *p.ExpectedDown
	}
	if p.Profile == "a25" {
		return 3 * 40
	}
	return 3*40 + 1500 + 10*250
}

type pResult struct {
	File    string   `json:"-"`
	Net     string   `json:"net"`
	Device  string   `json:"device"`
	Browser string   `json:"browser"`
	Version string   `json:"version"`
	OS      string   `json:"os"`
	Saved   string   `json:"saved"`
	Mic     string   `json:"mic_permission"`
	Phases  []pPhase `json:"phases"`
	Base    *struct {
		WS pRTT `json:"ws_rtt_ms"`
	} `json:"base"`
}

var pathName = map[string]string{"udp": "UDP", "tcp": "ICE-TCP", "turn": "TURN-TLS"}
var profName = map[string]string{"v12": "12 Video", "a25": "25 Ton"}

func loadResults(dir string) ([]pResult, []benchResult, error) {
	ents, err := os.ReadDir(dir)
	if err != nil {
		return nil, nil, err
	}
	var rs []pResult
	var bs []benchResult
	names := []string{}
	for _, e := range ents {
		if !e.IsDir() && strings.HasSuffix(e.Name(), ".json") {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	for _, n := range names {
		b, err := os.ReadFile(filepath.Join(dir, n))
		if err != nil {
			continue
		}
		if strings.HasPrefix(n, "bench-") {
			var br benchResult
			if json.Unmarshal(b, &br) == nil && br.Kind == "bench" {
				bs = append(bs, br)
			}
			continue
		}
		var r pResult
		if json.Unmarshal(b, &r) == nil && len(r.Phases) > 0 {
			r.File = n
			rs = append(rs, r)
		}
	}
	return rs, bs, nil
}

func runReport(args []string, w io.Writer) error {
	fs := flag.NewFlagSet("report", flag.ContinueOnError)
	data := fs.String("data", env("HORN_DATA", "/data"), "Datenordner")
	lokal := fs.Bool("lokal", false, "lokale Testlaeufe (Netz e2e-lokal) mitbewerten")
	if err := fs.Parse(normFlags(args)); err != nil {
		return err
	}
	includeLocal = *lokal
	rs, bs, err := loadResults(filepath.Join(*data, "results"))
	if err != nil {
		return fmt.Errorf("ergebnisse: %w", err)
	}
	writeReport(w, rs, bs, time.Now())
	return nil
}

func fv(p *float64, digits int) string {
	if p == nil {
		return "–"
	}
	return fmtNum(*p, digits)
}

func fmtNum(x float64, digits int) string {
	s := fmt.Sprintf("%.*f", digits, x)
	return strings.Replace(s, ".", ",", 1)
}

func maxConnect(c []*int) string {
	m := -1
	for _, x := range c {
		if x != nil && *x > m {
			m = *x
		}
	}
	if m < 0 {
		return "–"
	}
	return fmt.Sprint(m)
}

func platform(r pResult) string {
	b := r.Browser
	if r.Version != "" {
		b += " " + r.Version
	}
	return b + " / " + r.OS
}

// verdict ist das Ergebnis eines Kriteriums.
type verdict string

const (
	vGo   verdict = "Go"
	vNoGo verdict = "No-Go"
	vGrey verdict = "Grenzbereich"
	vNone verdict = "nicht gemessen"
	vInfo verdict = "Info"
)

func writeReport(w io.Writer, rs []pResult, bs []benchResult, now time.Time) {
	fmt.Fprintf(w, "# Horn-Probe – Bericht\n\n")
	fmt.Fprintf(w, "Erstellt %s · Knoten %s · %d Messläufe · %d Bench-Läufe\n\n", now.UTC().Format("2006-01-02 15:04 UTC"), version, len(rs), len(bs))

	fmt.Fprintf(w, "## Messläufe je Gerät, Netz und Weg\n\n")
	if len(rs) == 0 {
		fmt.Fprintf(w, "_Noch keine Messläufe. Messseite öffnen und „Messen“ drücken._\n\n")
	} else {
		fmt.Fprintf(w, "| Gerät | Netz | Browser | Weg | Profil | Verbunden | Aufbau ms | RTT p50/p95 ms | WSS p95 ms | Ton-Verdeckung %% | Ton-Puffer ms | Einfrieren je Kachel /5 min | Einfrierdauer s /5 min | Runter Mbit/s | Rauf Mbit/s | Bildverlust %% | Knoten verworfen | Warteschlange max ms |\n")
		fmt.Fprintf(w, "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
		for _, r := range rs {
			for _, p := range r.Phases {
				if p.OK && p.Invalid != "" {
					fmt.Fprintf(w, "| %s | %s | %s | %s | %s | ⚠ ungültig: %s | | | | | | | | | | | | |\n", r.Device, esc(r.Net), platform(r), pathName[p.Path], profName[p.Profile], esc(p.Invalid))
					continue
				}
				if !p.OK {
					fmt.Fprintf(w, "| %s | %s | %s | %s | %s | ✘ %s | | | | | | | | | | | | |\n", r.Device, esc(r.Net), platform(r), pathName[p.Path], profName[p.Profile], esc(p.Reason))
					continue
				}
				var drop uint64
				maxQ := 0.0
				for _, n := range p.Node {
					drop += n.Dropped
					if n.MaxQMs > maxQ {
						maxQ = n.MaxQMs
					}
				}
				conceal, jb, frz, frzs, vloss := "–", "–", "–", "–", "–"
				if p.Audio != nil {
					conceal, jb = fv(p.Audio.Conceal, 2), fv(p.Audio.JB, 0)
				}
				if p.Video != nil {
					frz, frzs, vloss = fv(p.Video.Frz5, 2), fv(p.Video.FrzS5, 2), fv(p.Video.LossPct, 2)
				}
				fmt.Fprintf(w, "| %s | %s | %s | %s | %s | ✔ | %s | %s / %s | %s | %s | %s | %s | %s | %s | %s | %s | %d | %s |\n",
					r.Device, esc(r.Net), platform(r), pathName[p.Path], profName[p.Profile], maxConnect(p.ConnectMs),
					fv(p.RTT.P50, 0), fv(p.RTT.P95, 0), fv(p.WSRTT.P95, 0), conceal, jb, frz, frzs,
					fmtNum(p.Down.Stable/1000, 2), fmtNum(p.Up.Stable/1000, 2), vloss, drop, fmtNum(maxQ, 0))
			}
		}
		fmt.Fprintln(w)
	}

	fmt.Fprintf(w, "## Bench (Knoten-Last mit lokalen Bots)\n\n")
	if len(bs) == 0 {
		fmt.Fprintf(w, "_Noch kein Bench. `gjallarhorn bench -n 25 --video 12 --secs 120`_\n\n")
	} else {
		fmt.Fprintf(w, "| Teilnehmende | Video | Weg | Dauer s | Kerne | CPU %% eines Kerns | µs je Paket raus | µs je Paket ein+aus | Pakete/s raus | Mbit/s raus | RAM je Teilnehmendem MB | RAM je Bein MB | Alloc je Paket | verworfene Rahmen | Warteschlange max ms | Verlust Bots %% |\n")
		fmt.Fprintf(w, "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
		for _, b := range bs {
			fmt.Fprintf(w, "| %d | %d | %s | %.0f | %d | %s | %s | %s | %.0f | %s | %s | %s | %s | %d | %s | %s |\n",
				b.N, b.Video, b.Path, b.Secs, b.Cores, fmtNum(b.CPUPct, 1), fmtNum(b.CPUusPkt, 1), fmtNum(b.CPUusPktIO, 1),
				b.PPSOut, fmtNum(b.MbitOut, 1), fmtNum(b.RAMPartMB, 2), fmtNum(b.RAMLegMB, 2), fmtNum(b.AllocsPkt, 1),
				b.Dropped, fmtNum(b.MaxQMs, 0), fmtNum(b.BotLossPct, 3))
		}
		fmt.Fprintln(w)
	}

	fmt.Fprintf(w, "## Go/No-Go (Konzept §5.4)\n\n")
	fmt.Fprintf(w, "Bewertet wird der schlechteste gültige Wert über alle Läufe und beide TCP-Wege (ICE-TCP und TURN-TLS, Profil 12 Video). Ungültige Phasen (Seite im Hintergrund, kein Ton) und lokale Testläufe (Netz „e2e-lokal“) zählen nicht.\n\n")
	fmt.Fprintf(w, "| # | Kriterium | Messwert | Ergebnis |\n|---|---|---|---|\n")
	gs := evaluateGo(rs, bs)
	for _, g := range gs {
		fmt.Fprintf(w, "| %s | %s | %s | %s |\n", g.id, g.what, g.value, g.v)
	}
	fmt.Fprintln(w)
	fmt.Fprintf(w, "**Gesamt:** %s\n", overall(gs))
}

var includeLocal bool

func esc(s string) string { return strings.ReplaceAll(s, "|", "/") }

type goRow struct {
	id, what, value string
	v               verdict
}

func evaluateGo(rs []pResult, bs []benchResult) []goRow {
	var real []pResult
	for _, r := range rs {
		if r.Net != "e2e-lokal" || includeLocal {
			real = append(real, r)
		}
	}
	// TCP-Wege: alle gueltigen Phasen (ICE-TCP und TURN-TLS) mit Profil v12;
	// gewertet wird jeweils die schlechteste
	type tcpRun struct {
		r pResult
		p pPhase
	}
	var tcp []tcpRun
	plat := map[string]bool{}
	for _, r := range real {
		k := platform(r)
		ok := false
		for _, p := range r.Phases {
			if p.Profile == "v12" && (p.Path == "tcp" || p.Path == "turn") && p.OK {
				ok = true
				if p.valid() {
					tcp = append(tcp, tcpRun{r, p})
				}
			}
		}
		plat[k] = plat[k] || ok
	}
	var out []goRow
	// G1
	{
		row := goRow{id: "G1", what: "Verbindung über ICE-TCP oder TURN-TLS je Plattform"}
		if len(plat) == 0 {
			row.value, row.v = "–", vNone
		} else {
			var okL, bad []string
			for k, ok := range plat {
				if ok {
					okL = append(okL, k)
				} else {
					bad = append(bad, k)
				}
			}
			sort.Strings(okL)
			sort.Strings(bad)
			row.value = "✔ " + strings.Join(okL, ", ")
			if len(bad) > 0 {
				row.value += " · ✘ " + strings.Join(bad, ", ")
				row.v = vNoGo
			} else {
				row.v = vGo
			}
			miss := missingPlatforms(plat)
			if len(miss) > 0 {
				row.value += " · fehlt: " + strings.Join(miss, ", ")
				if row.v == vGo {
					row.v = vGrey
				}
			}
		}
		out = append(out, row)
	}
	worst := func(f func(pPhase) *float64) (float64, bool) {
		m, have := 0.0, false
		for _, t := range tcp {
			if x := f(t.p); x != nil {
				if !have || *x > m {
					m = *x
				}
				have = true
			}
		}
		return m, have
	}
	thr := func(id, what string, f func(pPhase) *float64, unit string, goMax, noGoMin float64) goRow {
		row := goRow{id: id, what: what}
		x, ok := worst(f)
		if !ok {
			row.value, row.v = "–", vNone
			return row
		}
		row.value = fmtNum(x, 1) + unit
		switch {
		case x <= goMax:
			row.v = vGo
		case x > noGoMin:
			row.v = vNoGo
		default:
			row.v = vGrey
		}
		return row
	}
	out = append(out, thr("G2", "RTT p95 (≤ 250 ms, No-Go > 350 ms)", func(p pPhase) *float64 { return p.RTT.P95 }, " ms", 250, 350))
	out = append(out, thr("G3", "Ton-Verdeckung (≤ 3 %, No-Go > 6 %)", func(p pPhase) *float64 {
		if p.Audio == nil {
			return nil
		}
		return p.Audio.Conceal
	}, " %", 3, 6))
	{
		row := goRow{id: "G4", what: "Bild-Einfrieren je Kachel (≤ 1 je 5 min, Summe ≤ 2 s)"}
		f, ok1 := worst(func(p pPhase) *float64 {
			if p.Video == nil {
				return nil
			}
			return p.Video.Frz5
		})
		d, _ := worst(func(p pPhase) *float64 {
			if p.Video == nil {
				return nil
			}
			return p.Video.FrzS5
		})
		if !ok1 {
			row.value, row.v = "–", vNone
		} else {
			row.value = fmtNum(f, 2) + " je 5 min, " + fmtNum(d, 2) + " s"
			switch {
			case f <= 1 && d <= 2:
				row.v = vGo
			case f > 2 || d > 4:
				row.v = vNoGo
			default:
				row.v = vGrey
			}
		}
		out = append(out, row)
	}
	{
		row := goRow{id: "G5", what: "stabile Downlink-Rate je Gerät (≥ 80 % der laut Profil erwarteten Rate)"}
		worstR, have := 0.0, false
		var wd, we float64
		for _, t := range tcp {
			e := t.p.expectedDown()
			if e <= 0 {
				continue
			}
			q := t.p.Down.Stable / e
			if !have || q < worstR {
				worstR, wd, we = q, t.p.Down.Stable, e
			}
			have = true
		}
		if !have {
			row.value, row.v = "–", vNone
		} else {
			row.value = fmt.Sprintf("%s %% (%s von %s Mbit/s, schlechtester Lauf)", fmtNum(100*worstR, 0), fmtNum(wd/1000, 2), fmtNum(we/1000, 2))
			if worstR >= 0.8 {
				row.v = vGo
			} else {
				row.v = vNoGo
			}
		}
		out = append(out, row)
	}
	// G6/G7 aus dem Bench
	{
		row := goRow{id: "G6", what: "Knoten-CPU (≤ 10 µs/Paket, 12er-Videoanruf ≤ 10 % eines Kerns, bench 25 ohne Verwerfen)"}
		if len(bs) == 0 {
			row.value, row.v = "–", vNone
		} else {
			// massgeblich: der groesste Lauf (bei Gleichstand der neueste)
			var big *benchResult
			var c12 *benchResult
			var b25 *benchResult
			for i, b := range bs {
				if big == nil || b.N >= big.N {
					big = &bs[i]
				}
				if b.N == 12 && b.Video == 12 {
					c12 = &bs[i]
				}
				if b.N >= 25 {
					b25 = &bs[i]
				}
			}
			us := big.CPUusPkt
			parts := []string{fmt.Sprintf("%s µs/Paket (bench %d/%d)", fmtNum(us, 1), big.N, big.Video)}
			v := vGo
			if us > 25 {
				v = vNoGo
			} else if us > 10 {
				v = vGrey
			}
			if c12 != nil {
				parts = append(parts, "12er-Video "+fmtNum(c12.CPUPct, 1)+" %")
				if c12.CPUPct > 10 && v == vGo {
					v = vGrey
				}
			} else {
				parts = append(parts, "12er-Video nicht gemessen")
			}
			if b25 != nil {
				parts = append(parts, fmt.Sprintf("bench %d: %d verworfen", b25.N, b25.Dropped))
				if b25.Dropped > 0 && v == vGo {
					v = vGrey
				}
			} else {
				parts = append(parts, "bench 25 nicht gemessen")
			}
			row.value, row.v = strings.Join(parts, ", "), v
		}
		out = append(out, row)
	}
	{
		row := goRow{id: "G7", what: "RAM je Video-Bein (≤ 6 MB; gerechnet je Teilnehmendem mit Ton- und Bild-Bein)"}
		m := 0.0
		for _, b := range bs {
			if b.RAMPartMB > m {
				m = b.RAMPartMB
			}
		}
		if len(bs) == 0 {
			row.value, row.v = "–", vNone
		} else {
			row.value = fmtNum(m, 2) + " MB"
			if m <= 6 {
				row.v = vGo
			} else {
				row.v = vNoGo
			}
		}
		out = append(out, row)
	}
	{
		row := goRow{id: "G8", what: "UDP (Info): Verlust ≤ 2 %, RTT p95 ≤ TCP − 20 ms", v: vInfo}
		var parts []string
		for _, r := range real {
			var u, t *pPhase
			for i, p := range r.Phases {
				if p.Profile != "v12" || !p.OK {
					continue
				}
				if p.Path == "udp" {
					u = &r.Phases[i]
				} else if p.Path == "tcp" || (p.Path == "turn" && t == nil) {
					t = &r.Phases[i]
				}
			}
			if u == nil || u.RTT.P95 == nil {
				continue
			}
			s := esc(r.Net) + ": RTT p95 " + fv(u.RTT.P95, 0) + " ms"
			if t != nil && t.RTT.P95 != nil {
				s += " (TCP " + fv(t.RTT.P95, 0) + ")"
			}
			if u.Video != nil {
				s += ", Verlust " + fv(u.Video.LossPct, 2) + " %"
			}
			parts = append(parts, s)
		}
		if len(parts) == 0 {
			row.value = "kein UDP-Lauf"
		} else {
			row.value = strings.Join(parts, "; ")
		}
		out = append(out, row)
	}
	{
		row := goRow{id: "G9", what: "Upload am Testhost (Info, Kapazität je Knoten)", v: vInfo}
		m := 0.0
		for _, b := range bs {
			if b.MbitOut > m {
				m = b.MbitOut
			}
		}
		pm := 0.0
		for _, r := range real {
			for _, p := range r.Phases {
				var bytes uint64
				secs := 0.0
				for _, n := range p.Node {
					bytes += n.BytesOut
					if n.Secs > secs {
						secs = n.Secs
					}
				}
				if secs > 0 {
					if x := float64(bytes) * 8 / secs / 1e6; x > pm {
						pm = x
					}
				}
			}
		}
		row.value = "Bench lokal bis " + fmtNum(m, 1) + " Mbit/s raus; je Messgerät bis " + fmtNum(pm, 1) + " Mbit/s. Die echte Grenze ist der Upload des Anschlusses (12er-Videoanruf ≈ 36 Mbit/s)."
		out = append(out, row)
	}
	return out
}

func missingPlatforms(plat map[string]bool) []string {
	want := []struct{ name, browser, os string }{
		{"Chrome", "Chrome", ""}, {"Firefox", "Firefox", ""}, {"Safari", "Safari", "macOS"},
		{"iOS", "", "iOS"}, {"Android", "", "Android"},
	}
	var miss []string
	for _, w := range want {
		found := false
		for k := range plat {
			if (w.browser == "" || strings.HasPrefix(k, w.browser)) && (w.os == "" || strings.HasSuffix(k, "/ "+w.os)) {
				found = true
			}
		}
		if !found {
			miss = append(miss, w.name)
		}
	}
	return miss
}

func overall(gs []goRow) string {
	var noGo, grey, none []string
	for _, g := range gs {
		if g.v == vInfo || g.id > "G6" {
			continue
		}
		switch g.v {
		case vNoGo:
			noGo = append(noGo, g.id)
		case vGrey:
			grey = append(grey, g.id)
		case vNone:
			none = append(none, g.id)
		}
	}
	switch {
	case len(noGo) > 0:
		s := "No-Go in " + strings.Join(noGo, ", ") + "."
		for _, id := range noGo {
			if id == "G2" || id == "G3" || id == "G4" {
				s += " Folge nach §5.4: Konzept auf „TCP bis 6 Video, darüber UDP oder mehr Knoten“ zurückschneiden."
				break
			}
		}
		return s
	case len(none) > 0:
		return "noch offen – nicht gemessen: " + strings.Join(none, ", ") + "."
	case len(grey) > 0:
		return "Go unter Vorbehalt (Grenzbereich: " + strings.Join(grey, ", ") + ")."
	}
	return "Go (G1–G6 erfüllt)."
}
