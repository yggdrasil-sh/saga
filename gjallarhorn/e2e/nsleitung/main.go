//go:build linux && amd64

// nsleitung: Zugangsleitung EINES Geraets (Browser in eigenem Netz-Namensraum) im Userspace - fuer Messreihen ohne
// tc/netem (tests/browser_rang345.js). Der Browser laeuft per "ip netns exec <ns>" ohne Weg nach draussen; nsleitung
// lauscht IM Namensraum auf 127.0.0.1:<port> (TCP und UDP) und reicht alles an 127.0.0.1:<port> im Host-Namensraum
// weiter (Saga, Knoten, TURN). Je Richtung EINE Leitung fuer alle Verbindungen: Rate (kbit/s, mit IP-Kopf), FIFO mit
// Grenze in ms (UDP: Tail-Drop, TCP: Rueckstau), Laufzeit RTT/2, zufaelliger UDP-Verlust je Richtung.
//
//	nsleitung -ns /run/netns/r345e1 -tcp 8881,18881 -udp 18881,3478 -ctl 127.0.0.1:18891 [-up 1000 -down 1000 -rtt 150 -loss 0.03 -qms 300]
//
// Steuerung (Host): GET /set?up=&down=&rtt=&loss=&qms= (kbit/s, ms, Anteil) | GET /flap?ms=3000 (Netzwechsel: alle
// Verbindungen weg, so lange nichts durch, danach neue Quell-Ports am Host - wie eine neue Adresse) | GET /stats
// Nur fuer Tests, keine Abhaengigkeiten ausser der Standardbibliothek. Nur linux/amd64 (Nummer von setns, CLONE_NEWNET).
package main

import (
	"encoding/json"
	"flag"
	"log"
	"math/rand"
	"net"
	"net/http"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const sysSetns = 308 // setns(2) auf linux/amd64 (syscall kennt die Nummer nicht)

type pkt struct {
	b  []byte
	f  func([]byte)
	at time.Time
}

type link struct {
	mu      sync.Mutex
	rate    int64 // bit/s, 0 = frei
	oneWay  time.Duration
	qms     int
	loss    float64
	next    time.Time
	ch      chan *pkt
	rnd     *rand.Rand
	blocked bool

	Pkts, Bytes, LossDrop, QDrop, BlockDrop int64
	MaxQms                                  int64
}

func newLink(seed int64) *link {
	l := &link{ch: make(chan *pkt, 1<<16), rnd: rand.New(rand.NewSource(seed)), qms: 300}
	go func() {
		for p := range l.ch {
			if d := time.Until(p.at); d > 0 {
				time.Sleep(d)
			}
			p.f(p.b)
		}
	}()
	return l
}

// send -> Wartezeit in der Schlange (fuer den TCP-Rueckstau); udp: darf verworfen werden
func (l *link) send(b []byte, f func([]byte), udp bool) time.Duration {
	l.mu.Lock()
	now := time.Now()
	if l.blocked {
		l.BlockDrop++
		l.mu.Unlock()
		return 0
	}
	if udp && l.loss > 0 && l.rnd.Float64() < l.loss {
		l.LossDrop++
		l.mu.Unlock()
		return 0
	}
	start := now
	if l.next.After(now) {
		start = l.next
	}
	wait := start.Sub(now)
	if l.rate > 0 {
		if udp && wait > time.Duration(l.qms)*time.Millisecond {
			l.QDrop++
			l.mu.Unlock()
			return wait
		}
		hdr := 28
		if !udp {
			hdr = 40
		}
		l.next = start.Add(time.Duration(int64(len(b)+hdr) * 8 * int64(time.Second) / l.rate))
	} else {
		l.next = now
	}
	if w := wait.Milliseconds(); w > l.MaxQms {
		l.MaxQms = w
	}
	l.Pkts++
	l.Bytes += int64(len(b))
	at := l.next.Add(l.oneWay)
	l.mu.Unlock()
	l.ch <- &pkt{b: b, f: f, at: at}
	return wait
}

func (l *link) set(rate int64, rtt time.Duration, loss float64, qms int) {
	l.mu.Lock()
	l.rate, l.oneWay, l.loss = rate, rtt/2, loss
	if qms > 0 {
		l.qms = qms
	}
	l.mu.Unlock()
}

func (l *link) setBlocked(v bool) {
	l.mu.Lock()
	l.blocked = v
	l.mu.Unlock()
}

type leitung struct {
	up, down *link
	mu       sync.Mutex
	udp      map[string]*net.UDPConn // Innen-Adresse/Port -> Host-Socket
	tcp      map[net.Conn]net.Conn
	gen      int
	params   map[string]float64
}

// inNS fuehrt fn auf einem eigenen OS-Thread im Namensraum aus; der Thread endet danach (LockOSThread ohne Unlock).
func inNS(path string, fn func() error) error {
	errc := make(chan error, 1)
	go func() {
		runtime.LockOSThread()
		f, err := os.Open(path)
		if err != nil {
			errc <- err
			return
		}
		defer f.Close()
		if _, _, e := syscall.RawSyscall(sysSetns, f.Fd(), syscall.CLONE_NEWNET, 0); e != 0 {
			errc <- e
			return
		}
		errc <- fn()
	}()
	return <-errc
}

func (s *leitung) udpPort(in *net.UDPConn, port int) {
	host := &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: port}
	buf := make([]byte, 65536)
	for {
		n, src, err := in.ReadFromUDP(buf)
		if err != nil {
			log.Printf("udp %d: %v", port, err)
			return
		}
		b := append([]byte(nil), buf[:n]...)
		k := src.String() + ">" + strconv.Itoa(port)
		s.mu.Lock()
		blocked := s.up.blocked
		out := s.udp[k]
		if out == nil && !blocked {
			out, err = net.DialUDP("udp", nil, host)
			if err != nil {
				s.mu.Unlock()
				continue
			}
			s.udp[k] = out
			go func(out *net.UDPConn, src *net.UDPAddr) {
				rb := make([]byte, 65536)
				for {
					m, e := out.Read(rb)
					if e != nil {
						return
					}
					d := append([]byte(nil), rb[:m]...)
					s.down.send(d, func(x []byte) { in.WriteToUDP(x, src) }, true)
				}
			}(out, src)
		}
		s.mu.Unlock()
		if out == nil {
			s.up.send(b, nil, true) // zaehlt als BlockDrop
			continue
		}
		s.up.send(b, func(x []byte) { out.Write(x) }, true)
	}
}

func (s *leitung) pipe(src, dst net.Conn, l *link) {
	buf := make([]byte, 16384)
	for {
		n, err := src.Read(buf)
		if n > 0 {
			for o := 0; o < n; o += 1448 {
				e := o + 1448
				if e > n {
					e = n
				}
				seg := append([]byte(nil), buf[o:e]...)
				if w := l.send(seg, func(x []byte) { dst.Write(x) }, false); w > time.Duration(l.qms)*time.Millisecond {
					time.Sleep(w - time.Duration(l.qms)*time.Millisecond) // Rueckstau statt Verlust
				}
			}
		}
		if err != nil {
			// Ende erst nach den Daten in der Leitung weitergeben
			l.send(nil, func([]byte) {
				if tc, ok := dst.(*net.TCPConn); ok {
					tc.CloseWrite()
				} else {
					dst.Close()
				}
			}, false)
			return
		}
	}
}

func (s *leitung) tcpPort(ln net.Listener, port int) {
	for {
		c, err := ln.Accept()
		if err != nil {
			log.Printf("tcp %d: %v", port, err)
			return
		}
		go func(c net.Conn) {
			s.mu.Lock()
			blocked := s.up.blocked
			s.mu.Unlock()
			if blocked {
				c.Close()
				return
			}
			h, err := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)), 5*time.Second)
			if err != nil {
				c.Close()
				return
			}
			s.mu.Lock()
			s.tcp[c] = h
			s.mu.Unlock()
			go s.pipe(c, h, s.up)
			s.pipe(h, c, s.down)
			time.Sleep(2 * time.Second)
			s.mu.Lock()
			delete(s.tcp, c)
			s.mu.Unlock()
			c.Close()
			h.Close()
		}(c)
	}
}

func (s *leitung) flap(ms int) {
	s.up.setBlocked(true)
	s.down.setBlocked(true)
	s.mu.Lock()
	for k, u := range s.udp {
		u.Close()
		delete(s.udp, k)
	}
	for a, b := range s.tcp {
		a.Close()
		b.Close()
	}
	s.gen++
	s.mu.Unlock()
	time.Sleep(time.Duration(ms) * time.Millisecond)
	s.up.setBlocked(false)
	s.down.setBlocked(false)
}

func ports(v string) []int {
	var out []int
	for _, p := range strings.Split(v, ",") {
		if n, err := strconv.Atoi(strings.TrimSpace(p)); err == nil && n > 0 {
			out = append(out, n)
		}
	}
	return out
}

func main() {
	ns := flag.String("ns", "", "Pfad des Netz-Namensraums (/run/netns/<name>)")
	tcpP := flag.String("tcp", "", "TCP-Ports (Komma)")
	udpP := flag.String("udp", "", "UDP-Ports (Komma)")
	ctl := flag.String("ctl", "127.0.0.1:0", "Steuerung (Host)")
	up := flag.Int64("up", 0, "kbit/s hoch (0 = frei)")
	down := flag.Int64("down", 0, "kbit/s runter (0 = frei)")
	rtt := flag.Int("rtt", 0, "RTT in ms")
	loss := flag.Float64("loss", 0, "UDP-Verlust je Richtung (Anteil)")
	qms := flag.Int("qms", 300, "Warteschlange in ms")
	flag.Parse()
	s := &leitung{up: newLink(1), down: newLink(2), udp: map[string]*net.UDPConn{}, tcp: map[net.Conn]net.Conn{}, params: map[string]float64{}}
	apply := func(u, d int64, r int, lo float64, q int) {
		s.up.set(u*1000, time.Duration(r)*time.Millisecond, lo, q)
		s.down.set(d*1000, time.Duration(r)*time.Millisecond, lo, q)
		s.mu.Lock()
		s.params = map[string]float64{"up": float64(u), "down": float64(d), "rtt": float64(r), "loss": lo, "qms": float64(q)}
		s.mu.Unlock()
	}
	apply(*up, *down, *rtt, *loss, *qms)
	err := inNS(*ns, func() error {
		for _, p := range ports(*tcpP) {
			ln, err := net.Listen("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(p)))
			if err != nil {
				return err
			}
			go s.tcpPort(ln, p)
		}
		for _, p := range ports(*udpP) {
			c, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: p})
			if err != nil {
				return err
			}
			go s.udpPort(c, p)
		}
		return nil
	})
	if err != nil {
		log.Fatal(err)
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/set", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		g := func(k string, def float64) float64 {
			if v, err := strconv.ParseFloat(q.Get(k), 64); err == nil {
				return v
			}
			return def
		}
		s.mu.Lock()
		p := s.params
		s.mu.Unlock()
		apply(int64(g("up", p["up"])), int64(g("down", p["down"])), int(g("rtt", p["rtt"])), g("loss", p["loss"]), int(g("qms", p["qms"])))
		w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/flap", func(w http.ResponseWriter, r *http.Request) {
		ms, _ := strconv.Atoi(r.URL.Query().Get("ms"))
		if ms <= 0 {
			ms = 3000
		}
		go s.flap(ms)
		w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/stats", func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		defer s.mu.Unlock()
		st := func(l *link) map[string]int64 {
			l.mu.Lock()
			defer l.mu.Unlock()
			return map[string]int64{"pkts": l.Pkts, "bytes": l.Bytes, "loss": l.LossDrop, "qdrop": l.QDrop, "block": l.BlockDrop, "maxq_ms": l.MaxQms}
		}
		json.NewEncoder(w).Encode(map[string]any{"up": st(s.up), "down": st(s.down), "params": s.params, "udp_flows": len(s.udp), "tcp_conns": len(s.tcp), "flaps": s.gen})
	})
	ln, err := net.Listen("tcp", *ctl)
	if err != nil {
		log.Fatal(err)
	}
	log.Printf("nsleitung %s ctl %s", *ns, ln.Addr())
	log.Fatal(http.Serve(ln, mux))
}
