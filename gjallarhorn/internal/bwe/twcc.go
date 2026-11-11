package bwe

import "github.com/pion/rtcp"

// Status eines gemeldeten Pakets in einer transport-cc-Rueckmeldung.
const (
	StLost   = iota // nicht angekommen
	StRecv          // angekommen, mit Ankunftszeit
	StNoTime        // angekommen, ohne Ankunftszeit gemeldet
)

// refWrap: Die Bezugszeit einer Rueckmeldung hat 24 bit in Schritten von 64 ms.
const (
	refBits  = 24
	refUnit  = 64_000 // us
	refRange = int64(1) << refBits
)

// Unpack geht die Pakete einer transport-cc-Rueckmeldung in Reihenfolge durch und ruft fn je Paket mit Folgenummer,
// Status und Ankunftszeit (us seit der Bezugszeit null des Empfaengers, nicht abgewickelt) auf. Format nach
// draft-holmer-rmcat-transport-wide-cc-extensions-01: Basis-Folgenummer, Anzahl, Bezugszeit (x 64 ms), Chunks mit je
// einem Status pro Paket (Lauflaenge oder Vektor) und danach je empfangenem Paket ein Abstand zum vorigen (us).
// Liefert false, wenn die Rueckmeldung in sich nicht stimmt (zu wenige Abstaende bzw. Chunks); bis dahin gemeldete
// Pakete sind dann schon durchgereicht.
func Unpack(fb *rtcp.TransportLayerCC, fn func(seq uint16, status int, arrivalUs int64)) bool {
	seq := fb.BaseSequenceNumber
	left := int(fb.PacketStatusCount)
	t := int64(fb.ReferenceTime) * refUnit
	di := 0
	one := func(sym uint16) bool {
		switch sym {
		case rtcp.TypeTCCPacketNotReceived:
			fn(seq, StLost, 0)
		case rtcp.TypeTCCPacketReceivedSmallDelta, rtcp.TypeTCCPacketReceivedLargeDelta:
			if di >= len(fb.RecvDeltas) {
				return false
			}
			t += fb.RecvDeltas[di].Delta
			di++
			fn(seq, StRecv, t)
		default: // "empfangen, ohne Abstand"
			fn(seq, StNoTime, 0)
		}
		seq++
		left--
		return true
	}
	for _, ch := range fb.PacketChunks {
		if left <= 0 {
			break
		}
		switch c := ch.(type) {
		case *rtcp.RunLengthChunk:
			for i := 0; i < int(c.RunLength) && left > 0; i++ {
				if !one(c.PacketStatusSymbol) {
					return false
				}
			}
		case *rtcp.StatusVectorChunk:
			// der letzte Vektor ist auf volle 7 bzw. 14 Symbole aufgefuellt: nur bis zur gemeldeten Anzahl lesen
			for i := 0; i < len(c.SymbolList) && left > 0; i++ {
				if !one(c.SymbolList[i]) {
					return false
				}
			}
		default:
			return false
		}
	}
	return left == 0
}

// refUnwrap wickelt die Bezugszeit ab: liefert den Versatz (us), der zu den Ankunftszeiten dieser Rueckmeldung zu
// addieren ist, damit sie ueber den 24-bit-Ueberlauf hinweg stetig bleiben.
type refUnwrap struct {
	have bool
	last uint32
	off  int64 // Ueberlaeufe x refRange x refUnit
}

func (u *refUnwrap) offset(ref uint32) int64 {
	ref &= uint32(refRange - 1)
	if u.have {
		d := int64(ref) - int64(u.last)
		if d < -refRange/2 {
			u.off += refRange * refUnit
		} else if d > refRange/2 {
			u.off -= refRange * refUnit
		}
	}
	u.have, u.last = true, ref
	return u.off
}
