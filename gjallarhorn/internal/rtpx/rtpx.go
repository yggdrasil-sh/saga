// Package rtpx liest die wenigen RTP-Felder, die der Weiterleiter braucht,
// direkt aus dem Puffer und schreibt Folgenummer, Zeitstempel und SSRC an Ort
// und Stelle um. Keine Allokation.
package rtpx

import "encoding/binary"

// Header enthaelt die geparsten Felder eines RTP-Pakets.
type Header struct {
	Marker      bool
	Padding     bool
	PayloadType uint8
	Seq         uint16
	TS          uint32
	SSRC        uint32
	// Off ist der Beginn der Nutzlast, Len ihre Laenge ohne Auffuellung.
	Off, Len int
}

// Parse liest den RTP-Kopf aus b. ok=false bei kaputten Paketen.
func Parse(b []byte, h *Header) bool {
	if len(b) < 12 || b[0]>>6 != 2 {
		return false
	}
	cc := int(b[0] & 0x0f)
	h.Padding = b[0]&0x20 != 0
	ext := b[0]&0x10 != 0
	h.Marker = b[1]&0x80 != 0
	h.PayloadType = b[1] & 0x7f
	h.Seq = binary.BigEndian.Uint16(b[2:])
	h.TS = binary.BigEndian.Uint32(b[4:])
	h.SSRC = binary.BigEndian.Uint32(b[8:])
	off := 12 + 4*cc
	if ext {
		if len(b) < off+4 {
			return false
		}
		off += 4 + 4*int(binary.BigEndian.Uint16(b[off+2:]))
	}
	end := len(b)
	if h.Padding {
		if end <= off {
			return false
		}
		p := int(b[end-1])
		if p == 0 || end-p < off {
			return false
		}
		end -= p
	}
	if end < off {
		return false
	}
	h.Off, h.Len = off, end-off
	return true
}

// VP8 beschreibt den Anfang eines VP8-Rahmens (RFC 7741).
type VP8 struct {
	FrameStart bool // S=1 und PartID=0: erstes Paket eines Rahmens
	Key        bool // erstes Paket eines Schluesselbilds
}

// ParseVP8 wertet den VP8-Payload-Descriptor aus.
func ParseVP8(p []byte) (v VP8) {
	if len(p) < 1 {
		return
	}
	x := p[0]&0x80 != 0
	s := p[0]&0x10 != 0
	pid := p[0] & 0x0f
	i := 1
	if x {
		if len(p) < 2 {
			return
		}
		f := p[1]
		i = 2
		if f&0x80 != 0 { // I: PictureID
			if len(p) <= i {
				return
			}
			if p[i]&0x80 != 0 {
				i += 2
			} else {
				i++
			}
		}
		if f&0x40 != 0 { // L: TL0PICIDX
			i++
		}
		if f&0x30 != 0 { // T oder K
			i++
		}
	}
	if !s || pid != 0 {
		return
	}
	v.FrameStart = true
	if len(p) > i {
		v.Key = p[i]&0x01 == 0 // P-Bit 0 = Schluesselbild
	}
	return
}

// Rewrite setzt Folgenummer, Zeitstempel und SSRC im Puffer neu.
func Rewrite(b []byte, seq uint16, ts, ssrc uint32) {
	binary.BigEndian.PutUint16(b[2:], seq)
	binary.BigEndian.PutUint32(b[4:], ts)
	binary.BigEndian.PutUint32(b[8:], ssrc)
}

// IsRTCP unterscheidet RTCP von RTP nach RFC 5761 (Payload-Typ 64..95).
func IsRTCP(b []byte) bool {
	return len(b) >= 2 && b[1] >= 192 && b[1] <= 223
}
