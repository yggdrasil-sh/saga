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
	// ExtProfile und ExtOff/ExtLen beschreiben den Kopferweiterungsblock
	// (ohne die 4 Byte Profil/Laenge); ExtLen=0 ohne Erweiterung.
	ExtProfile     uint16
	ExtOff, ExtLen int
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
	h.ExtProfile, h.ExtOff, h.ExtLen = 0, 0, 0
	if ext {
		if len(b) < off+4 {
			return false
		}
		h.ExtProfile = binary.BigEndian.Uint16(b[off:])
		h.ExtLen = 4 * int(binary.BigEndian.Uint16(b[off+2:]))
		h.ExtOff = off + 4
		off += 4 + h.ExtLen
		if off > len(b) {
			return false
		}
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

// Extension sucht die Kopferweiterung id (RFC 8285, ein- und zweibytig) und
// liefert ihre Daten als Teilstueck von b. Keine Allokation.
func Extension(b []byte, h *Header, id uint8) []byte {
	if h.ExtLen == 0 || id == 0 || h.ExtOff+h.ExtLen > len(b) {
		return nil
	}
	e := b[h.ExtOff : h.ExtOff+h.ExtLen]
	switch {
	case h.ExtProfile == 0xBEDE:
		for i := 0; i < len(e); {
			x := e[i]
			if x == 0 { // Fuellbyte
				i++
				continue
			}
			eid, l := x>>4, int(x&0x0f)+1
			if eid == 15 {
				return nil
			}
			i++
			if i+l > len(e) {
				return nil
			}
			if eid == id {
				return e[i : i+l]
			}
			i += l
		}
	case h.ExtProfile&0xfff0 == 0x1000:
		for i := 0; i < len(e); {
			if e[i] == 0 {
				i++
				continue
			}
			if i+2 > len(e) {
				return nil
			}
			eid, l := e[i], int(e[i+1])
			i += 2
			if i+l > len(e) {
				return nil
			}
			if eid == id {
				return e[i : i+l]
			}
			i += l
		}
	}
	return nil
}

// AudioLevel liest RFC 6464 (ssrc-audio-level): Pegel 0..127 (-dBov, 127 =
// Stille) und das Sprach-Bit. ok=false ohne Erweiterung.
func AudioLevel(b []byte, h *Header, id uint8) (level uint8, voice, ok bool) {
	e := Extension(b, h, id)
	if len(e) < 1 {
		return 127, false, false
	}
	return e[0] & 0x7f, e[0]&0x80 != 0, true
}

// VP8 beschreibt den Payload-Descriptor eines VP8-Pakets (RFC 7741).
// Gelesen wird nur der Descriptor und das erste Byte des Rahmenkopfs; der
// (Ende-zu-Ende-verschluesselte) Rahmen bleibt unberuehrt.
type VP8 struct {
	FrameStart bool // S=1 und PartID=0: erstes Paket eines Rahmens
	Key        bool // erstes Paket eines Schluesselbilds

	HasPicID  bool
	PicIDLong bool   // 15 statt 7 Bit
	PicID     uint16 // PictureID
	HasTL0    bool
	TL0       uint8 // TL0PICIDX
	HasTID    bool
	TID       uint8 // zeitliche Schicht 0..3
	Sync      bool  // Y: Schichtwechsel nach oben hier moeglich
	PicOff    uint8 // Lage der PictureID im Descriptor
	TL0Off    uint8
	HdrLen    uint8 // Laenge des Descriptors
}

// ParseVP8 wertet den VP8-Payload-Descriptor aus. Kaputte Descriptoren
// liefern einen leeren Wert (kein Rahmenanfang).
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
			return VP8{}
		}
		f := p[1]
		i = 2
		if f&0x80 != 0 { // I: PictureID
			if len(p) <= i {
				return VP8{}
			}
			v.HasPicID = true
			v.PicOff = uint8(i)
			if p[i]&0x80 != 0 {
				if len(p) <= i+1 {
					return VP8{}
				}
				v.PicIDLong = true
				v.PicID = uint16(p[i]&0x7f)<<8 | uint16(p[i+1])
				i += 2
			} else {
				v.PicID = uint16(p[i])
				i++
			}
		}
		if f&0x40 != 0 { // L: TL0PICIDX
			if len(p) <= i {
				return VP8{}
			}
			v.HasTL0 = true
			v.TL0Off = uint8(i)
			v.TL0 = p[i]
			i++
		}
		if f&0x30 != 0 { // T oder K
			if len(p) <= i {
				return VP8{}
			}
			if f&0x20 != 0 {
				v.HasTID = true
				v.TID = p[i] >> 6
				v.Sync = p[i]&0x20 != 0
			}
			i++
		}
	}
	v.HdrLen = uint8(i)
	if !s || pid != 0 {
		return
	}
	v.FrameStart = true
	if len(p) > i {
		v.Key = p[i]&0x01 == 0 // P-Bit 0 = Schluesselbild
	}
	return
}

// RewriteVP8 setzt PictureID und TL0PICIDX im Descriptor neu (an Ort und
// Stelle, Breite der PictureID bleibt). p muss zu v gehoeren.
func RewriteVP8(p []byte, v *VP8, picID uint16, tl0 uint8) {
	if v.HasPicID && int(v.PicOff) < len(p) {
		i := int(v.PicOff)
		if v.PicIDLong {
			if i+1 < len(p) {
				p[i] = 0x80 | byte(picID>>8)&0x7f
				p[i+1] = byte(picID)
			}
		} else {
			p[i] = byte(picID) & 0x7f
		}
	}
	if v.HasTL0 && int(v.TL0Off) < len(p) {
		p[v.TL0Off] = tl0
	}
}

// PicIDMask liefert die Maske der PictureID (7 oder 15 Bit).
func (v *VP8) PicIDMask() uint16 {
	if v.PicIDLong {
		return 0x7fff
	}
	return 0x7f
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
