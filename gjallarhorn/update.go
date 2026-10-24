package main

import (
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Selbst-Update gekoppelter Knoten (Konzept v2 §2.5, Paket P6, Entscheidung: signiertes Binaer, ohne Docker-Socket).
//
// Zielversion = Saga-Version (welcome.want bzw. "version"). Weicht sie ab, laedt der Knoten
//
//	<HORN_UPDATE_BASE>/v<want>/gjallarhorn-linux-<arch>  und  ...-<arch>.sig
//	(Vorabversionen: <HORN_UPDATE_BASE>/horn-testing/gjallarhorn-<want>-linux-<arch>, siehe releaseURL)
//
// (Standard https://github.com/yggdrasil-sh/saga/releases/download), prueft die Ed25519-Signatur gegen den festen
// Release-Schluessel (release.go) und legt das Binaer als HORN_DATA/bin/next ab. Dann:
//   - weitere Knoten bereit (solo=false): Leeren (state draining, Saga schlaegt den Geraeten andere Knoten vor), Neustart
//     sobald leer, spaetestens nach 10 min;
//   - einziger Knoten (solo=true): normal weiterarbeiten und erst in einer Zeit ohne Beine neu starten; nach 24 h meldet er
//     das (Hinweis im Admin-Bereich).
// Neustart = Exit-Code 75 an den Waechter (gjallarhorn watch, watch.go), der die Signatur noch einmal prueft und das neue
// Binaer startet. Ohne Waechter (HORN_WATCHED fehlt) bzw. ohne Release-Schluessel kein Selbst-Update.

const (
	exitUpdate   = 75
	drainMax     = 10 * time.Minute
	soloWarnTime = 24 * time.Hour
)

type updater struct {
	s       *server
	mu      sync.Mutex
	want    string
	solo    bool
	staged  string
	refused string // zuletzt abgelehnte aeltere Zielversion (nur einmal melden)
	state   string // "" | aus | manuell | laden | fehler | bereit | leeren | wartet
	code    string // fuer Saga (uebersetzter Text): nokey | nowatch | sig | download | wait24
	err     string
	since   time.Time
	nextTry time.Time
	backoff time.Duration
	sent    string
	base    string
	client  *http.Client
	exit    func(int)
	shut    func()
	first   time.Duration
}

func newUpdater(s *server) *updater {
	retry := 5 * time.Minute
	if v, err := time.ParseDuration(env("HORN_UPDATE_RETRY", "5m")); err == nil && v >= time.Second {
		retry = v // nur Tests: kuerzerer erster Neuversuch
	}
	return &updater{s: s, first: retry, base: strings.TrimRight(env("HORN_UPDATE_BASE", "https://github.com/yggdrasil-sh/saga/releases/download"), "/"),
		client: &http.Client{Timeout: 10 * time.Minute}, backoff: retry, exit: os.Exit, shut: s.shutdown}
}

func (u *updater) target(want string, solo bool) {
	if u == nil {
		return
	}
	u.mu.Lock()
	switch {
	case want == "" || !validVersion(want) || want == u.want || want == version:
	case !newerThan(want, version): // Downgrade-Schutz: nie auf eine aeltere (signierte) Version mit bekannter Luecke
		if want != u.refused {
			u.refused = want
			log.Printf("update: %s ist nicht neuer als %s - nicht geladen", want, version)
		}
	default:
		u.want, u.nextTry, u.backoff = want, time.Time{}, u.first
	}
	u.solo = solo
	u.mu.Unlock()
}

func (u *updater) isSolo() bool {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.solo
}

func (u *updater) status() map[string]any {
	if u == nil {
		return map[string]any{}
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	m := map[string]any{"state": u.state, "want": u.want, "have": version}
	if u.err != "" {
		m["err"] = u.err
	}
	if u.code != "" {
		m["code"] = u.code
	}
	return m
}

func (u *updater) set(state, err string) { u.setc(state, "", err) }

func (u *updater) setc(state, code, err string) {
	u.mu.Lock()
	if state != u.state {
		u.since = time.Now()
	}
	u.state, u.code, u.err = state, code, err
	key := state + "|" + code + "|" + err + "|" + u.want
	changed := key != u.sent
	u.sent = key
	u.mu.Unlock()
	if changed {
		if c := u.s.ctlc(); c != nil {
			m := u.status()
			m["t"] = "update"
			c.send(m)
		}
	}
}

func (u *updater) loop() {
	for range time.Tick(5 * time.Second) {
		if u.s.closing.Load() {
			return
		}
		u.step(time.Now())
	}
}

// step: ein Schritt der Update-Maschine (Tests rufen ihn direkt).
func (u *updater) step(now time.Time) {
	u.mu.Lock()
	want, solo, staged, since, next := u.want, u.solo, u.staged, u.since, u.nextTry
	u.mu.Unlock()
	switch {
	case want == "" || want == version:
		if u.state != "" {
			u.set("", "")
		}
		return
	case releaseKey() == nil:
		u.setc("aus", "nokey", errNoReleaseKey.Error())
		return
	case os.Getenv("HORN_WATCHED") != "1":
		u.setc("manuell", "nowatch", "ohne Waechter (gjallarhorn watch) kein Selbst-Update")
		return
	}
	if staged != want {
		if now.Before(next) {
			return
		}
		u.set("laden", "")
		if err := u.fetch(want); err != nil {
			u.mu.Lock()
			u.nextTry = now.Add(u.backoff)
			lim := 6 * time.Hour
			if u.first < 5*time.Minute { // Tests (HORN_UPDATE_RETRY): Obergrenze mitverkuerzt
				lim = 30 * u.first
			}
			if u.backoff *= 2; u.backoff > lim {
				u.backoff = lim
			}
			u.mu.Unlock()
			log.Printf("update: %s: %v", want, err)
			code := "download"
			if errors.Is(err, errSigMismatch) {
				code = "sig"
			}
			u.setc("fehler", code, err.Error())
			return
		}
		u.mu.Lock()
		u.staged = want
		u.mu.Unlock()
		log.Printf("update: %s geladen und geprueft", want)
		if solo {
			u.set("wartet", "")
		} else {
			u.s.setDrain(true)
			u.set("leeren", "")
		}
		return
	}
	legs := u.s.node.Load().Legs
	if solo && !u.s.draining.Load() {
		if legs == 0 {
			u.restart()
		} else if now.Sub(since) > soloWarnTime && u.state == "wartet" {
			u.setc("wartet", "wait24", "seit 24 h keine anruffreie Zeit - Update wartet weiter")
		}
		return
	}
	u.s.setDrain(true)
	if u.state != "leeren" {
		u.set("leeren", "")
	}
	if legs == 0 || now.Sub(since) > drainMax {
		u.restart()
	}
}

func (u *updater) restart() {
	log.Printf("update: neustart fuer %s", u.want)
	u.set("neustart", "")
	time.Sleep(200 * time.Millisecond)
	u.shut()
	u.exit(exitUpdate)
}

func (u *updater) get(url string, max int64) ([]byte, error) {
	r, err := u.client.Get(url)
	if err != nil {
		return nil, err
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return nil, fmt.Errorf("%s: http %d", url, r.StatusCode)
	}
	b, err := io.ReadAll(io.LimitReader(r.Body, max+1))
	if err != nil {
		return nil, err
	}
	if int64(len(b)) > max {
		return nil, errors.New("zu gross")
	}
	return b, nil
}

// fetch laedt und prueft das Binaer und legt es als bin/next ab (next.ver, next.sig).
// releaseURL: Freigaben liegen im Release v<version>; Vorabversionen (testing, z. B. 2.5.0-rc.131) im rollenden
// Vorab-Release "horn-testing", das die CI bei jedem testing-Push ersetzt - mit der Version im Dateinamen, damit ein
// Knoten nie ein Binaer einer anderen Version laedt (die Signatur bindet die Version ohnehin).
func releaseURL(base, want, arch string) string {
	if strings.Contains(want, "-") {
		return base + "/horn-testing/gjallarhorn-" + want + "-" + arch
	}
	return base + "/v" + want + "/gjallarhorn-" + arch
}

func (u *updater) fetch(want string) error {
	arch := releaseArch()
	url := releaseURL(u.base, want, arch)
	sig, err := u.get(url+".sig", 4096)
	if err != nil {
		return err
	}
	bin, err := u.get(url, 128<<20)
	if err != nil {
		return err
	}
	if err := verifyRelease(releaseKey(), bin, string(sig), want, arch); err != nil {
		return err
	}
	dir := filepath.Join(u.s.cfg.Data, "bin")
	if err := writeAtomic(filepath.Join(dir, "next"), bin, 0o755); err != nil {
		return err
	}
	if err := writeAtomic(filepath.Join(dir, "next.sig"), sig, 0o644); err != nil {
		return err
	}
	return writeAtomic(filepath.Join(dir, "next.ver"), []byte(want+"\n"), 0o644)
}
