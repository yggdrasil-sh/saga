package main

import (
	"crypto/ed25519"
	"crypto/sha256"
	_ "embed"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"runtime"
	"strings"
)

// Release-Schluessel fuer Selbst-Updates (Konzept v2 §2.5, Paket P6). Der Waechter und der Knoten laden ein neues
// gjallarhorn-Binaer nur, wenn seine Ed25519-Signatur zu diesem festen Schluessel passt.
//
// Konfigurationsstelle: gjallarhorn/release.pub (eine Zeile Base64, 32 Byte; Kommentare mit #). Sven erzeugt das
// Schluesselpaar einmal mit tools/horn-release.py keygen, legt den GEHEIMEN Teil als CI-Secret HORN_RELEASE_KEY ab und
// traegt den oeffentlichen Teil in release.pub ein. Saga liest dieselbe Datei (saga/horn_ops.py) fuer das Deploy-Skript.
// Ohne Schluessel sind Selbst-Updates aus (der Knoten meldet "aus", Saga zeigt den Hinweis).
// Tests bauen mit -ldflags "-X main.releaseKeyOverride=<Base64>" (Testschluessel, nie im Repo).
//
// Signiert wird (tools/horn-release.py sign):
//
//	"gjallarhorn-release/1\n" + version + "\n" + "linux-" + arch + "\n" + hex(sha256(binaer)) + "\n"
//
// Die Datei <binaer>.sig enthaelt die 64 Byte Signatur als Base64. Version und Architektur gehoeren zur Signatur: ein
// altes oder fremdes Binaer laesst sich nicht unterschieben.

//go:embed release.pub
var releasePubFile string

// releaseKeyOverride setzen nur Test-Builds (ldflags).
var releaseKeyOverride = ""

const releaseDomain = "gjallarhorn-release/1"

func parseReleaseKey(s string) ed25519.PublicKey {
	for _, ln := range strings.Split(s, "\n") {
		ln = strings.TrimSpace(ln)
		if ln == "" || strings.HasPrefix(ln, "#") {
			continue
		}
		b, err := base64.StdEncoding.DecodeString(ln)
		if err != nil || len(b) != ed25519.PublicKeySize {
			return nil
		}
		return ed25519.PublicKey(b)
	}
	return nil
}

// releaseKey liefert den festen Release-Schluessel oder nil (Updates aus).
func releaseKey() ed25519.PublicKey {
	if releaseKeyOverride != "" {
		return parseReleaseKey(releaseKeyOverride)
	}
	return parseReleaseKey(releasePubFile)
}

func releaseArch() string { return "linux-" + runtime.GOARCH }

func releaseMsg(ver, arch string, bin []byte) []byte {
	h := sha256.Sum256(bin)
	return []byte(releaseDomain + "\n" + ver + "\n" + arch + "\n" + hex.EncodeToString(h[:]) + "\n")
}

var errNoReleaseKey = errors.New("kein Release-Schluessel (gjallarhorn/release.pub)")

// errSigMismatch: Signatur passt nicht (Saga zeigt dafuer einen uebersetzten Text, Code "sig").
var errSigMismatch = errors.New("signatur passt nicht zum Release-Schluessel")

// verifyRelease prueft ein Binaer gegen den Release-Schluessel.
func verifyRelease(key ed25519.PublicKey, bin []byte, sig, ver, arch string) error {
	if len(key) != ed25519.PublicKeySize {
		return errNoReleaseKey
	}
	sb, err := base64.StdEncoding.DecodeString(strings.TrimSpace(sig))
	if err != nil || len(sb) != ed25519.SignatureSize {
		return fmt.Errorf("%w (unlesbar)", errSigMismatch)
	}
	if !validVersion(ver) {
		return fmt.Errorf("version %q ungueltig", ver)
	}
	if !ed25519.Verify(key, releaseMsg(ver, arch, bin), sb) {
		return errSigMismatch
	}
	return nil
}

// validVersion: SemVer-artig, nur harmlose Zeichen (landet in URL und Dateinamen).
func validVersion(v string) bool {
	if v == "" || len(v) > 40 {
		return false
	}
	for i := 0; i < len(v); i++ {
		c := v[i]
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c == '.' || c == '-') {
			return false
		}
	}
	return v[0] >= '0' && v[0] <= '9'
}
