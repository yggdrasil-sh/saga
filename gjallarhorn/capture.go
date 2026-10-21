package main

import (
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
	"sync"
)

// capture: NUR fuer Tests (HORN_CAPTURE=<ordner>). Schreibt die ersten Pakete jeder eingehenden Spur unveraendert
// (RTP, je Paket 2 Byte Laenge + Paket) nach <ordner>/<video|audio>-<ssrc>.rtp. Damit prueft der Test, dass der Knoten
// nur Ende-zu-Ende-verschluesselte Rahmen sieht (tests/anrufe_horn_knoten.py). Im Betrieb aus (nil, kein Aufwand).
type capture struct {
	dir string
	max int
	mu  sync.Mutex
}

func newCapture(dir string) *capture {
	_ = os.MkdirAll(dir, 0o700)
	return &capture{dir: dir, max: 3000}
}

func (c *capture) tap(video bool, ssrc uint32, read func([]byte) (int, error)) func([]byte) (int, error) {
	kind := "audio"
	if video {
		kind = "video"
	}
	f, err := os.OpenFile(filepath.Join(c.dir, fmt.Sprintf("%s-%d.rtp", kind, ssrc)), os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return read
	}
	n := 0
	var hdr [2]byte
	return func(b []byte) (int, error) {
		k, err := read(b)
		if k > 0 && n < c.max && f != nil {
			n++
			binary.BigEndian.PutUint16(hdr[:], uint16(k))
			c.mu.Lock()
			_, _ = f.Write(hdr[:])
			_, _ = f.Write(b[:k])
			c.mu.Unlock()
			if n == c.max {
				_ = f.Close()
				f = nil
			}
		}
		if err != nil && f != nil {
			_ = f.Close()
			f = nil
		}
		return k, err
	}
}
