package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"log"
	"net"
	"os"
	"path/filepath"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// Knotenbetrieb gekoppelter Knoten (Konzept v2 §2.4-2.6, Paket P6): Leeren, Adressen, Entfernen.

// setDrain: Leeren an/aus (Admin "Leeren" oder Update). Waehrend des Leerens nimmt der Knoten keine neuen Beine an;
// laufende bleiben, bis die Geraete umgezogen sind (Saga schlaegt ihnen andere Knoten vor).
func (s *server) setDrain(on bool) {
	s.setDraining(on) // P5: meldet den Zustand auch ueber den Ereigniskanal an alle Geraete
}

// drainGate: neue Beine waehrend des Leerens ablehnen (429 wie "voll").
func (s *server) drainGate() error {
	if s.draining.Load() {
		return sfu.ErrFull
	}
	return nil
}

func (s *server) addrs() (string, string) {
	s.addrMu.Lock()
	defer s.addrMu.Unlock()
	return s.v4, s.v6
}

// refreshAddrs: Adressen ueber Saga (whoami) neu ermitteln -> true bei Aenderung.
func (s *server) refreshAddrs() bool {
	if s.sagaBase == "" {
		return false
	}
	v4, v6, dropped := s.publicAddrs(s.sagaBase)
	if v4 == "" && v6 == "" {
		if len(dropped) > 0 {
			log.Printf("adressen: Saga sieht nur interne Adressen %v (VPN?) - HORN_PUBLIC_IP setzen; bisherige bleiben", dropped)
		}
		return false // Saga gerade weg oder nur interne Adressen: nichts aendern
	}
	s.addrMu.Lock()
	defer s.addrMu.Unlock()
	if v4 == s.v4 && v6 == s.v6 {
		return false
	}
	s.v4, s.v6 = v4, v6
	return true
}

// removeSigned: signiertes "remove" von Saga (saga_pub aus der Kopplung): Schluessel und Kopplung loeschen, danach
// startet der Knoten nicht mehr als Knoten (Marker HORN_DATA/removed, der Waechter wartet dann nur noch).
func (s *server) removeSigned(nodeID, sig string, pub ed25519.PublicKey) bool {
	sb, err := base64.StdEncoding.DecodeString(sig)
	if err != nil || len(sb) != ed25519.SignatureSize || nodeID == "" ||
		!ed25519.Verify(pub, []byte("saga-horn-remove/1\n"+nodeID+"\n"+s.cfg.Name), sb) {
		log.Printf("entfernen: signatur ungueltig - ignoriert")
		return false
	}
	d := s.cfg.Data
	for _, f := range []string{"node.key", "pair.json", filepath.Join("tls", "acme", "dns01-account.key")} {
		_ = os.Remove(filepath.Join(d, f))
	}
	_ = os.WriteFile(filepath.Join(d, "removed"), []byte(time.Now().UTC().Format(time.RFC3339)+"\n"), 0o600)
	log.Printf("entfernen: von Saga entfernt - Schluessel geloescht. Container/Dienst kann geloescht werden.")
	s.setDrain(true)
	go func() {
		time.Sleep(500 * time.Millisecond)
		s.shutdown()
		os.Exit(0)
	}()
	return true
}

// certStatus fuer hello und die Meldung "cert".
func (s *server) certStatus() map[string]any {
	m := map[string]any{"mode": s.certs.ModeNow()}
	if p := s.certModeV.Load(); p != nil && m["mode"] != "self" {
		if *p == "dns01" && fileExists(filepath.Join(s.cfg.Data, "tls", "dns01.json")) || m["mode"] != "file" {
			m["mode"] = *p
		}
	}
	na := s.certs.NotAfter()
	switch {
	case !na.IsZero() && time.Until(na) > 0:
		m["state"], m["not_after"] = "ok", na.Unix()
	case s.certs.ModeNow() == "self":
		m["state"] = "self"
	default:
		m["state"] = "fehlt"
	}
	if e := s.certErr.Load(); e != nil && *e != "" {
		m["err"] = *e
	}
	return m
}

func (s *server) reportCert() {
	if c := s.ctlc(); c != nil {
		m := s.certStatus()
		m["t"] = "cert"
		c.send(m)
	}
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

// publicAddrs: oeffentliche Adressen des Knotens fuer Saga (DNS-Eintraege, Port-Pruefung).
// HORN_PUBLIC_IP gewinnt; sonst fragt der Knoten Saga (whoami). Adressen, die nicht im Internet erreichbar sind
// (privat, CGNAT 100.64/10 z. B. NetBird/Tailscale, Loopback, Link-local, ULA), verwirft er: Saga sieht sie, wenn der
// Knoten Saga ueber ein VPN erreicht - als DNS-Eintrag waeren sie fuer Geraete wertlos. HORN_DEV behaelt sie (Tests).
func (s *server) publicAddrs(base string) (v4, v6 string, dropped []string) {
	if len(s.cfg.PublicIPs) > 0 {
		for _, a := range s.cfg.PublicIPs {
			ip := net.ParseIP(a)
			if ip == nil {
				continue
			}
			if ip.To4() != nil && v4 == "" {
				v4 = ip.String()
			} else if ip.To4() == nil && v6 == "" {
				v6 = ip.String()
			}
		}
		return v4, v6, nil
	}
	v4, v6 = whoami(base, "tcp4"), whoami(base, "tcp6")
	if s.cfg.Dev {
		return v4, v6, nil
	}
	if v4 != "" && !globalIP(v4) {
		dropped, v4 = append(dropped, v4), ""
	}
	if v6 != "" && !globalIP(v6) {
		dropped, v6 = append(dropped, v6), ""
	}
	return v4, v6, dropped
}

var cgnat = &net.IPNet{IP: net.IPv4(100, 64, 0, 0), Mask: net.CIDRMask(10, 32)}

func globalIP(a string) bool {
	ip := net.ParseIP(a)
	return ip != nil && ip.IsGlobalUnicast() && !ip.IsPrivate() && !cgnat.Contains(ip)
}
