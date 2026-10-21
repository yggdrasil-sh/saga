package main

import (
	"errors"
	"fmt"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// Waechter (Konzept v2 §2.5, Paket P6): "gjallarhorn watch" laeuft im Knoten-Image als PID 1 bzw. unter systemd als
// Hauptprozess und startet den eigentlichen Knoten ("node") als Kindprozess. Kein Docker-Socket noetig.
//
//   - Beendet sich der Knoten mit Code 75 (Update bereit), prueft der Waechter HORN_DATA/bin/next erneut gegen den
//     festen Release-Schluessel (Signatur ueber Version, Architektur und SHA-256) und macht es zu bin/current.
//   - Gestartet wird bin/current, wenn es sich pruefen laesst und nicht als schlecht markiert ist, sonst das eigene Binaer
//     aus dem Image. Stuerzt ein geladenes Binaer dreimal binnen 30 s nach dem Start ab, kommt die Version auf
//     bin/bad und der Waechter faellt auf das Image-Binaer zurueck.
//   - Andere Enden: Neustart mit Backoff 1 ... 30 s. SIGTERM/SIGINT gehen an den Knoten (danach 25 s Frist).
//   - Marker HORN_DATA/removed (von Saga entfernt): kein Start mehr, der Waechter wartet nur.

type binDir struct{ dir string }

func (b binDir) p(n string) string { return filepath.Join(b.dir, n) }

func readTrim(p string) string {
	x, err := os.ReadFile(p)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(x))
}

// check prueft bin/<name> (+ .sig, .ver) -> Version.
func (b binDir) check(name string) (string, error) {
	ver := readTrim(b.p(name + ".ver"))
	sig := readTrim(b.p(name + ".sig"))
	bin, err := os.ReadFile(b.p(name))
	if err != nil {
		return "", err
	}
	if ver == "" || sig == "" {
		return "", errors.New("version oder signatur fehlt")
	}
	return ver, verifyRelease(releaseKey(), bin, sig, ver, releaseArch())
}

// promote: bin/next -> bin/current, wenn die Signatur passt (sonst verwerfen).
func (b binDir) promote() {
	if _, err := os.Stat(b.p("next")); err != nil {
		return
	}
	ver, err := b.check("next")
	if err == nil && !newerThan(ver, version) {
		err = fmt.Errorf("version %s nicht neuer als das Image (%s)", ver, version)
	}
	if err != nil {
		log.Printf("waechter: bin/next abgelehnt (%v) - verworfen", err)
		for _, n := range []string{"next", "next.sig", "next.ver"} {
			_ = os.Remove(b.p(n))
		}
		return
	}
	for _, n := range []string{"", ".sig", ".ver"} {
		if err := os.Rename(b.p("next"+n), b.p("current"+n)); err != nil {
			log.Printf("waechter: %v", err)
			return
		}
	}
	log.Printf("waechter: version %s uebernommen", ver)
}

// pick: zu startendes Binaer und Version ("" = Image-Binaer).
func (b binDir) pick(self string) (string, string) {
	if _, err := os.Stat(b.p("current")); err != nil {
		return self, ""
	}
	ver, err := b.check("current")
	if err != nil {
		log.Printf("waechter: bin/current nicht verwendbar (%v) - Image-Binaer", err)
		return self, ""
	}
	if ver == readTrim(b.p("bad")) {
		return self, ""
	}
	if !newerThan(ver, version) { // nach einem Image-Update: nie ein aelteres (oder gleiches) bin/current statt des Images
		log.Printf("waechter: bin/current (%s) nicht neuer als das Image (%s) - Image-Binaer", ver, version)
		return self, ""
	}
	return b.p("current"), ver
}

func runWatch(_ []string) error {
	data := env("HORN_DATA", "/data")
	self, err := os.Executable()
	if err != nil {
		return err
	}
	bd := binDir{filepath.Join(data, "bin")}
	_ = os.MkdirAll(bd.dir, 0o755)
	sig := make(chan os.Signal, 2)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT, syscall.SIGHUP)
	fails, back := 0, time.Second
	for {
		if _, err := os.Stat(filepath.Join(data, "removed")); err == nil {
			log.Printf("waechter: Knoten wurde in Saga entfernt - kein Start. Container/Dienst loeschen oder HORN_DATA leeren und neu koppeln.")
			for x := range sig {
				if x != syscall.SIGHUP {
					return nil
				}
			}
		}
		bd.promote()
		bin, ver := bd.pick(self)
		cmd := exec.Command(bin, "node")
		cmd.Env = append(os.Environ(), "HORN_WATCHED=1")
		cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
		if err := cmd.Start(); err != nil {
			log.Printf("waechter: start %s: %v", bin, err)
			if bin != self {
				_ = os.WriteFile(bd.p("bad"), []byte(ver+"\n"), 0o644)
			}
			time.Sleep(back)
			continue
		}
		t0 := time.Now()
		done := make(chan error, 1)
		go func() { done <- cmd.Wait() }()
		var werr error
		stopping := false
	wait:
		for {
			select {
			case x := <-sig:
				_ = cmd.Process.Signal(x)
				if x == syscall.SIGHUP {
					continue
				}
				stopping = true
				select {
				case werr = <-done:
				case <-time.After(25 * time.Second):
					_ = cmd.Process.Kill()
					werr = <-done
				}
				break wait
			case werr = <-done:
				break wait
			}
		}
		if stopping {
			return nil
		}
		code := 0
		var ee *exec.ExitError
		if errors.As(werr, &ee) {
			code = ee.ExitCode()
		}
		if code == exitUpdate {
			fails, back = 0, time.Second
			continue
		}
		if bin != self && time.Since(t0) < 30*time.Second {
			if fails++; fails >= 3 {
				log.Printf("waechter: version %s stuerzt ab - zurueck auf das Image-Binaer", ver)
				_ = os.WriteFile(bd.p("bad"), []byte(ver+"\n"), 0o644)
				fails = 0
			}
		} else if time.Since(t0) > time.Minute {
			fails, back = 0, time.Second
		}
		log.Printf("waechter: knoten beendet (code %d), neuer start in %v", code, back)
		time.Sleep(back)
		if back *= 2; back > 30*time.Second {
			back = 30 * time.Second
		}
	}
}
