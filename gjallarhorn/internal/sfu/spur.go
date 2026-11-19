package sfu

import (
	"fmt"
	"hash/fnv"
	"log"
	"os"
	"strings"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Diagnose (HORN_SPUR=1, nur Pruefstand und Labor): je Bein und Sekunde eine Zeile mit Schaetzung, GCC-Ziel, Rate,
// Sperrzeit und Belegung der Plaetze (NODE.md, Testen). Keine Kennungen von Personen oder Geraeten: Teilnehmende
// erscheinen als kurzer Hash ihrer Peer-Kennung (spurID), damit sich die Zeilen der Beine eines Raums zuordnen lassen.
var spurOn = os.Getenv("HORN_SPUR") == "1"

// SpurOn: Diagnose an (fuer Zeilen ausserhalb des Pakets)
var SpurOn = spurOn

func (l *Leg) spur(now int64) {
	if !spurOn || now-l.spurAt < 1e9 {
		return
	}
	l.spurAt = now
	e := &l.est
	var b strings.Builder
	for _, s := range l.slots {
		if s.class == "audio" {
			continue
		}
		own := "-"
		if s.owner != nil {
			own = spurID(s.owner.ID)
		}
		lay := s.layer
		if lay == "" {
			lay = "p"
		}
		st := ""
		pk := uint64(0)
		if o := s.out; o != nil {
			if l.spurPk == nil {
				l.spurPk = map[*fwd.Out]uint64{}
			}
			n := o.Pkts.Load()
			pk = n - l.spurPk[o]
			l.spurPk[o] = n
			if o.Current() == nil {
				st += "x"
			}
			if p := o.Pending(); p != nil {
				st += "w" + p.Layer
			}
		}
		if m := l.mb[mbCls(s)]; s.owner != nil && (m[mbBack].dw > 0 || m[mbFull].dw > 0) { // Aufschub Mindestform (2159)
			st += fmt.Sprintf("~%.0f/%.0f", m[mbBack].dw.Seconds(), m[mbFull].dw.Seconds())
		}
		fmt.Fprintf(&b, " %s:%s%d@%s/%d%s", s.class[:2], lay, s.tid, own, pk, st)
	}
	hold := float64(e.holdUntil-now) / 1e9
	if hold < 0 {
		hold = 0
	}
	me := "?"
	if l.Peer != nil {
		me = spurID(l.Peer.ID)
	}
	over := l.ExtOver != nil && l.ExtOver()
	jam := l.jamAt != 0 && now-l.jamAt < int64(time.Second)
	jh := ""
	if l.jamAt != 0 && now-l.jamAt < int64(jamHold) { // Sperre nach Stau: Restzeit, b = keine Proben mehr
		jh = fmt.Sprintf(" halt=%.0fs", float64(l.jamAt+int64(jamHold)-now)/1e9)
		if l.jamBad {
			jh += "b"
		}
	}
	if l.pathN > 0 {
		jh += fmt.Sprintf(" netz=%d", l.pathN)
	}
	if l.ExtStat != nil {
		jh += fmt.Sprintf(" loss=%.1f%%", 100*l.ExtStat().Loss)
	}
	if l.jamN > 0 {
		jh += fmt.Sprintf(" stufen=%d/%dms", l.jamN, l.jamRTT0.Milliseconds())
	}
	log.Printf("spur %s est=%dk gcc=%dk rate=%dk sperre=%.1fs backoff=%.0fs lag=%v ready=%v probe=%d over=%v rtt=%d/%dms jam=%v%s%s",
		me, int(e.bps/1000), int(e.gccLast/1000), int(e.rateTick/1000), hold, e.backoff.Seconds(), e.lag, e.ready, l.pr.stage, over,
		l.rttPrev.Milliseconds(), l.rttFloor.Milliseconds(), jam, jh, b.String())
}

// spurID: 16-Bit-Hash der Peer-Kennung (nicht umkehrbar, nur zum Zuordnen der Zeilen)
func spurID(id string) string {
	h := fnv.New32a()
	h.Write([]byte(id))
	v := h.Sum32()
	return fmt.Sprintf("%04x", (v>>16)^(v&0xffff))
}
