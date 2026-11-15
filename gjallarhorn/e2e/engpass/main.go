// engpass: Nachbildung einer Zugangsleitung (Geraet <-> Internet) im Userspace, weil tc/netem im Container fehlt.
// Alle Verbindungen ueber alle Lauschports teilen sich je Richtung EINE Leitung: Warteschlange (FIFO, Grenze in ms
// der Rate), Rate (bit/s), Laufzeit (je Richtung RTT/2) und TCP-Verlust als Head-of-Line-Stau (ein "verlorenes"
// Segment haelt seine Verbindung 1,25 RTT auf, wie Linux mit RACK; der Rest der Leitung laeuft weiter).
// Ist die Warteschlange voll, liest engpass nicht weiter - der Stau wandert in den Sendepuffer des Absenders (wie bei
// einer echten Leitung, nur ohne Paketverlust; TCP-Verlust gibt es getrennt ueber -loss).
//
//	engpass -map 8505=127.0.0.1:8501,8508=127.0.0.1:8503 -up 1500 -down 1500 -rtt 50 -qms 200 -loss 0 -ctl 127.0.0.1:8509
//
// Steuerung zur Laufzeit: GET http://<ctl>/set?up=..&down=..&rtt=..&qms=..&loss=..   GET /stats
package main

import (
	"encoding/json"
	"flag"
	"log"
	"math/rand"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const mss = 1448

type params struct {
	rate   int64 // bit/s, 0 = unbegrenzt
	oneWay time.Duration
	qms    int
	loss   float64
}

type link struct {
	name string
	mu   sync.Mutex
	cond *sync.Cond
	p    params
	q    []*seg
	qb   int // Bytes in der Warteschlange
	last time.Time
	rnd  *rand.Rand

	sent, lossN, maxQ atomic.Int64
	qdelayMax         atomic.Int64
}

type seg struct {
	b   []byte
	out *outq
	enq time.Time
	at  time.Time
}

type outq struct {
	dead atomic.Bool // Empfaenger weg: Rest verwerfen, Quelle schliessen (sonst laeuft ein abgebrochener Download weiter)
	src  net.Conn
	ch   chan *seg
	pend atomic.Int64
	mu   sync.Mutex
	last time.Time // letzte Zustellzeit (Reihenfolge je Verbindung)
	dst  net.Conn
}

func newLink(name string, p params) *link {
	l := &link{name: name, p: p, rnd: rand.New(rand.NewSource(int64(len(name))))}
	l.cond = sync.NewCond(&l.mu)
	go l.run()
	return l
}

func (l *link) limit() int {
	if l.p.rate <= 0 {
		return 1 << 30
	}
	n := int(l.p.rate / 8 * int64(l.p.qms) / 1000)
	if n < 4*mss {
		n = 4 * mss
	}
	return n
}

func (l *link) push(s *seg) {
	l.mu.Lock()
	for l.qb > 0 && l.qb+len(s.b) > l.limit() {
		l.cond.Wait()
	}
	s.enq = time.Now()
	l.q = append(l.q, s)
	l.qb += len(s.b)
	if int64(l.qb) > l.maxQ.Load() {
		l.maxQ.Store(int64(l.qb))
	}
	l.cond.Broadcast()
	l.mu.Unlock()
}

func (l *link) queued(o *outq) int {
	l.mu.Lock()
	defer l.mu.Unlock()
	n := 0
	for _, s := range l.q {
		if s.out == o {
			n++
		}
	}
	return n
}

func (l *link) run() {
	for {
		l.mu.Lock()
		for len(l.q) == 0 {
			l.cond.Wait()
		}
		s := l.q[0]
		p := l.p
		if s.out.dead.Load() { // tote Verbindung belegt die Leitung nicht
			l.q = l.q[1:]
			l.qb -= len(s.b)
			l.cond.Broadcast()
			l.mu.Unlock()
			continue
		}
		l.mu.Unlock()
		now := time.Now()
		dep := now
		if l.last.After(dep) {
			dep = l.last
		}
		if p.rate > 0 {
			dep = dep.Add(time.Duration(int64(len(s.b)) * 8 * int64(time.Second) / p.rate))
		}
		if d := time.Until(dep); d > 0 {
			time.Sleep(d)
		}
		l.last = dep
		l.mu.Lock()
		l.q = l.q[1:]
		l.qb -= len(s.b)
		lost := p.loss > 0 && l.rnd.Float64() < p.loss
		l.cond.Broadcast()
		l.mu.Unlock()
		if qd := time.Since(s.enq); int64(qd) > l.qdelayMax.Load() {
			l.qdelayMax.Store(int64(qd))
		}
		at := dep.Add(p.oneWay)
		if lost {
			l.lossN.Add(1)
			at = at.Add(2*p.oneWay + p.oneWay/2) // +1,25 RTT: schnelle Wiederholung (RACK)
		}
		l.sent.Add(int64(len(s.b)))
		o := s.out
		o.mu.Lock()
		if at.Before(o.last) {
			at = o.last // TCP: alles hinter dem Fehlenden wartet
		}
		o.last = at
		o.mu.Unlock()
		s.at = at
		o.pend.Add(1)
		select {
		case o.ch <- s:
		default:
			o.pend.Add(-1)
			log.Printf("%s: Ausgang voll", l.name)
		}
	}
}

func newOut(dst, src net.Conn) *outq {
	o := &outq{ch: make(chan *seg, 65536), dst: dst, src: src}
	go func() {
		for s := range o.ch {
			if d := time.Until(s.at); d > 0 {
				time.Sleep(d)
			}
			if o.dead.Load() {
				o.pend.Add(-1)
				continue
			}
			_, err := dst.Write(s.b)
			o.pend.Add(-1)
			if err != nil {
				o.dead.Store(true)
				if o.src != nil {
					_ = o.src.Close()
				}
				_ = dst.Close()
				for range o.ch {
				}
				return
			}
		}
	}()
	return o
}

var up, down *link

func pipe(src, dst net.Conn, l *link) {
	o := newOut(dst, src)
	buf := make([]byte, 16<<10)
	for {
		n, err := src.Read(buf)
		for off := 0; off < n; off += mss {
			e := off + mss
			if e > n {
				e = n
			}
			l.push(&seg{b: append([]byte(nil), buf[off:e]...), out: o})
		}
		if err != nil {
			// Ende nach dem Leeren weitergeben
			go func() {
				for {
					if o.pend.Load() == 0 && l.queued(o) == 0 {
						break
					}
					time.Sleep(10 * time.Millisecond)
				}
				if tc, ok := dst.(*net.TCPConn); ok {
					_ = tc.CloseWrite()
				} else {
					_ = dst.Close()
				}
			}()
			return
		}
	}
}

var rcvbuf = flag.Int("rcvbuf", 65536, "SO_RCVBUF/SO_SNDBUF der Engpass-Sockets")

func tune(c net.Conn) {
	if tc, ok := c.(*net.TCPConn); ok {
		_ = tc.SetNoDelay(true)
		if *rcvbuf > 0 {
			_ = tc.SetReadBuffer(*rcvbuf)
			_ = tc.SetWriteBuffer(*rcvbuf)
		}
	}
}

func serve(lis, target string) {
	ln, err := net.Listen("tcp", lis)
	if err != nil {
		log.Fatal(err)
	}
	for {
		c, err := ln.Accept()
		if err != nil {
			log.Fatal(err)
		}
		go func(c net.Conn) {
			u, err := net.Dial("tcp", target)
			if err != nil {
				c.Close()
				return
			}
			tune(c)
			tune(u)
			go pipe(c, u, up)
			pipe(u, c, down)
		}(c)
	}
}

func main() {
	maps := flag.String("map", "127.0.0.1:8505=127.0.0.1:8501", "lauschen=ziel,...")
	upK := flag.Int64("up", 0, "kbit/s Geraet -> Netz (0 = unbegrenzt)")
	downK := flag.Int64("down", 0, "kbit/s Netz -> Geraet")
	rtt := flag.Int("rtt", 20, "ms")
	qms := flag.Int("qms", 200, "Warteschlange in ms der Rate")
	loss := flag.Float64("loss", 0, "TCP-Verlust je Segment (0.01 = 1 %)")
	ctl := flag.String("ctl", "127.0.0.1:8509", "Steuerung")
	flag.Parse()
	mk := func(k int64) params {
		return params{rate: k * 1000, oneWay: time.Duration(*rtt) * time.Millisecond / 2, qms: *qms, loss: *loss}
	}
	up, down = newLink("up", mk(*upK)), newLink("down", mk(*downK))
	for _, m := range strings.Split(*maps, ",") {
		l, t, _ := strings.Cut(m, "=")
		if !strings.Contains(l, ":") {
			l = "127.0.0.1:" + l
		}
		go serve(l, t)
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/set", func(w http.ResponseWriter, r *http.Request) {
		q0 := r.URL.Query()
		q := map[string]string{} // letzter Wert gilt (Voreinstellung zuerst, dann Abweichungen)
		for k, v := range q0 {
			q[k] = v[len(v)-1]
		}
		for _, l := range []*link{up, down} {
			l.mu.Lock()
			if v := q[l.name]; v != "" {
				k, _ := strconv.ParseInt(v, 10, 64)
				l.p.rate = k * 1000
			}
			if v := q["rtt"]; v != "" {
				ms, _ := strconv.Atoi(v)
				l.p.oneWay = time.Duration(ms) * time.Millisecond / 2
			}
			if v := q["qms"]; v != "" {
				l.p.qms, _ = strconv.Atoi(v)
			}
			if v := q["loss"]; v != "" {
				l.p.loss, _ = strconv.ParseFloat(v, 64)
			}
			l.cond.Broadcast()
			l.mu.Unlock()
		}
		_, _ = w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/stats", func(w http.ResponseWriter, r *http.Request) {
		out := map[string]any{}
		for _, l := range []*link{up, down} {
			l.mu.Lock()
			out[l.name] = map[string]any{"kbit": l.p.rate / 1000, "one_way_ms": l.p.oneWay.Milliseconds(), "qms": l.p.qms, "loss": l.p.loss,
				"sent": l.sent.Load(), "losses": l.lossN.Load(), "q_bytes": l.qb, "q_max": l.maxQ.Load(), "qdelay_max_ms": l.qdelayMax.Load() / 1e6}
			if r.URL.Query().Get("reset") == "1" {
				l.maxQ.Store(0)
				l.qdelayMax.Store(0)
			}
			l.mu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(out)
	})
	log.Printf("engpass: %s up=%d down=%d kbit/s rtt=%d ms q=%d ms loss=%.3f", *maps, *upK, *downK, *rtt, *qms, *loss)
	log.Fatal(http.ListenAndServe(*ctl, mux))
}
