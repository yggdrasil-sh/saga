package turnpipe

import (
	"bytes"
	"errors"
	"net"
	"os"
	"sync/atomic"
	"testing"
	"time"
)

func listen(t *testing.T) *net.UDPConn {
	t.Helper()
	c, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func pair(t *testing.T, h *Hub) (ice, relay *Conn) {
	t.Helper()
	ice = h.Attach(listen(t), ICE, nil)
	relay = h.Attach(listen(t), Relay, nil)
	t.Cleanup(func() { ice.Close(); relay.Close() })
	return ice, relay
}

func readTimeout(t *testing.T, c *Conn, d time.Duration) ([]byte, net.Addr, error) {
	t.Helper()
	_ = c.SetReadDeadline(time.Now().Add(d))
	b := make([]byte, 4096)
	n, a, err := c.ReadFrom(b)
	_ = c.SetReadDeadline(time.Time{})
	return b[:n], a, err
}

func rtp(n int) []byte {
	b := make([]byte, n)
	b[0] = 0x80 // RTP (RFC 7983)
	return b
}

// Zustellung im Prozess in beide Richtungen, Absender = Adresse des echten Sockets, nichts ueber den Kernel.
func TestDeliverBothWays(t *testing.T) {
	h := NewHub()
	ice, relay := pair(t, h)
	if _, err := ice.WriteTo([]byte{0x80, 1, 2}, relay.LocalAddr()); err != nil {
		t.Fatal(err)
	}
	b, a, err := readTimeout(t, relay, time.Second)
	if err != nil || !bytes.Equal(b, []byte{0x80, 1, 2}) || a.String() != ice.LocalAddr().String() {
		t.Fatalf("ice->relay: %x von %v, %v", b, a, err)
	}
	// AddrPort-Weg (pion/ice) und ::ffff:-Form derselben Adresse
	ap := relay.key
	if _, err := ice.WriteToAddrPort([]byte{0x80, 3}, ap); err != nil {
		t.Fatal(err)
	}
	if b, _, err := readTimeout(t, relay, time.Second); err != nil || b[1] != 3 {
		t.Fatalf("addrport: %x %v", b, err)
	}
	if _, err := relay.WriteTo([]byte{0x80, 4}, &net.UDPAddr{IP: net.ParseIP("::ffff:127.0.0.1"), Port: int(ice.key.Port())}); err != nil {
		t.Fatal(err)
	}
	n, from, err := ice.ReadFromAddrPort(make([]byte, 64))
	if err != nil || n != 2 || from != relay.key {
		t.Fatalf("relay->ice: %d von %v, %v", n, from, err)
	}
	if h.Stats.Kernel.Load() != 0 || h.Stats.Pkts.Load() != 3 {
		t.Fatalf("zaehler: kernel %d pkts %d", h.Stats.Kernel.Load(), h.Stats.Pkts.Load())
	}
}

// Gegendruck: Liest das Relay-Ende nicht weiter (TURN-Verbindung staut), wartet der Schreiber auf der ICE-Seite,
// sobald die kleine Warteschlange voll ist; liest es wieder, geht es weiter.
func TestBackpressure(t *testing.T) {
	h := NewHub()
	h.Limit = 4 << 10
	h.MaxWait = 5 * time.Second
	ice, relay := pair(t, h)
	var done atomic.Int64
	go func() {
		for i := 0; i < 50; i++ {
			if _, err := ice.WriteTo(rtp(1200), relay.LocalAddr()); err != nil {
				return
			}
			done.Add(1)
		}
	}()
	time.Sleep(200 * time.Millisecond)
	if n := done.Load(); n < 2 || n > 5 {
		t.Fatalf("ohne Lesen %d Pakete geschrieben, erwartet 3-4 (Limit 4 KiB)", n)
	}
	if q := relay.Queued(); q > h.Limit+1200 {
		t.Fatalf("warteschlange %d > limit", q)
	}
	// STUN wartet nie (ICE-Antworten des Lesezweigs)
	stun := make([]byte, 100)
	t0 := time.Now()
	if _, err := ice.WriteTo(stun, relay.LocalAddr()); err != nil || time.Since(t0) > 50*time.Millisecond {
		t.Fatalf("stun hat gewartet: %v %v", time.Since(t0), err)
	}
	for i := 0; i < 51; i++ {
		if _, _, err := readTimeout(t, relay, time.Second); err != nil {
			t.Fatalf("lesen %d: %v", i, err)
		}
	}
	if done.Load() != 50 {
		t.Fatalf("nach dem Lesen %d von 50", done.Load())
	}
	if h.Stats.Waits.Load() == 0 || h.Stats.Dropped.Load() != 0 {
		t.Fatalf("waits %d dropped %d", h.Stats.Waits.Load(), h.Stats.Dropped.Load())
	}
}

// Haengt die TURN-Verbindung, verwirft der Schreiber nach MaxWait (kein ewiges Warten); eine Schreibfrist gilt.
func TestMaxWaitAndDeadline(t *testing.T) {
	h := NewHub()
	h.Limit = 1000
	h.MaxWait = 100 * time.Millisecond
	ice, relay := pair(t, h)
	_, _ = ice.WriteTo(rtp(1200), relay.LocalAddr()) // leere Warteschlange nimmt immer ein Paket
	t0 := time.Now()
	n, err := ice.WriteTo(rtp(1200), relay.LocalAddr())
	if err != nil || n != 1200 || time.Since(t0) < 90*time.Millisecond {
		t.Fatalf("maxwait: %d %v nach %v", n, err, time.Since(t0))
	}
	if h.Stats.Timeouts.Load() != 1 {
		t.Fatalf("timeouts %d", h.Stats.Timeouts.Load())
	}
	_ = ice.SetWriteDeadline(time.Now().Add(20 * time.Millisecond))
	t0 = time.Now()
	if _, err := ice.WriteTo(rtp(1200), relay.LocalAddr()); !errors.Is(err, os.ErrDeadlineExceeded) || time.Since(t0) > 80*time.Millisecond {
		t.Fatalf("schreibfrist: %v nach %v", err, time.Since(t0))
	}
	_ = ice.SetWriteDeadline(time.Time{})
}

// Schliessen weckt: ein wartender Schreiber kehrt zurueck, wenn sein eigenes Ende oder das Ziel schliesst.
func TestCloseWakes(t *testing.T) {
	for _, own := range []bool{true, false} {
		h := NewHub()
		h.Limit = 1000
		h.MaxWait = 10 * time.Second
		ice, relay := pair(t, h)
		_, _ = ice.WriteTo(rtp(1200), relay.LocalAddr())
		res := make(chan error, 1)
		go func() {
			_, err := ice.WriteTo(rtp(1200), relay.LocalAddr())
			res <- err
		}()
		time.Sleep(50 * time.Millisecond)
		if own {
			ice.Close()
		} else {
			relay.Close()
		}
		select {
		case err := <-res:
			if own && !errors.Is(err, net.ErrClosed) || !own && err != nil {
				t.Fatalf("own=%v: %v", own, err)
			}
		case <-time.After(time.Second):
			t.Fatalf("own=%v: schreiber haengt", own)
		}
		if _, err := ice.WriteTo(rtp(10), relay.LocalAddr()); own && !errors.Is(err, net.ErrClosed) {
			t.Fatalf("schreiben nach close: %v", err)
		}
	}
}

// Lesefrist und Schliessen wecken einen Lesenden (pion/ice: SetDeadline(now) + Close beim Abbau).
func TestReadDeadlineAndClose(t *testing.T) {
	h := NewHub()
	ice, _ := pair(t, h)
	if _, _, err := readTimeout(t, ice, 30*time.Millisecond); !errors.Is(err, os.ErrDeadlineExceeded) {
		t.Fatalf("lesefrist: %v", err)
	}
	res := make(chan error, 1)
	go func() {
		_, _, err := ice.ReadFrom(make([]byte, 10))
		res <- err
	}()
	time.Sleep(30 * time.Millisecond)
	_ = ice.SetDeadline(time.Now())
	if err := <-res; !errors.Is(err, os.ErrDeadlineExceeded) {
		t.Fatalf("SetDeadline(now): %v", err)
	}
	go func() {
		_ = ice.SetDeadline(time.Time{})
		_, _, err := ice.ReadFrom(make([]byte, 10))
		res <- err
	}()
	time.Sleep(30 * time.Millisecond)
	ice.Close()
	select {
	case err := <-res:
		if !errors.Is(err, net.ErrClosed) {
			t.Fatalf("close: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("lesender haengt nach close")
	}
}

// Richtung Knoten wird nie gewartet: ein volles ICE-Ende verwirft.
func TestICEEndDrops(t *testing.T) {
	h := NewHub()
	ice, relay := pair(t, h)
	t0 := time.Now()
	for i := 0; i < Cap/1000+50; i++ {
		_, _ = relay.WriteTo(rtp(1000), ice.LocalAddr())
	}
	if time.Since(t0) > 500*time.Millisecond {
		t.Fatalf("relay->ice hat gewartet: %v", time.Since(t0))
	}
	if h.Stats.Dropped.Load() == 0 || ice.Queued() > Cap {
		t.Fatalf("dropped %d queued %d", h.Stats.Dropped.Load(), ice.Queued())
	}
}

// Kein offenes Relais im Prozess: Relay -> Relay und ICE -> ICE gehen nicht durch den Hub, sondern an den
// Kernel-Weg (dort sitzt der Schutz des Aufrufers); unbekannte Ziele ebenso.
func TestOnlyOppositeKinds(t *testing.T) {
	h := NewHub()
	var kern atomic.Int64
	guard := func(b []byte, a net.Addr) (int, error) { kern.Add(1); return len(b), nil }
	r1 := h.Attach(listen(t), Relay, guard)
	r2 := h.Attach(listen(t), Relay, guard)
	i1 := h.Attach(listen(t), ICE, nil)
	i2 := h.Attach(listen(t), ICE, nil)
	defer func() { r1.Close(); r2.Close(); i1.Close(); i2.Close() }()
	_, _ = r1.WriteTo(rtp(10), r2.LocalAddr())
	if kern.Load() != 1 || r2.Queued() != 0 {
		t.Fatalf("relay->relay im prozess (kern %d, q %d)", kern.Load(), r2.Queued())
	}
	_, _ = r1.WriteTo(rtp(10), &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 9})
	if kern.Load() != 2 {
		t.Fatal("unbekanntes ziel nicht ueber den schutz")
	}
	// ICE -> ICE: ueber den echten Socket (wie ohne Kopplung), kommt dort per pump an
	_, _ = i1.WriteTo(rtp(10), i2.LocalAddr())
	if b, a, err := readTimeout(t, i2, time.Second); err != nil || len(b) != 10 || a.String() != i1.LocalAddr().String() {
		t.Fatalf("ice->ice kernel: %d %v %v", len(b), a, err)
	}
	if h.Stats.Pkts.Load() != 0 || h.Stats.Kernel.Load() != 3 {
		t.Fatalf("pkts %d kernel %d", h.Stats.Pkts.Load(), h.Stats.Kernel.Load())
	}
}

// Verkehr von ausserhalb des Hubs (echter Socket) kommt weiter an.
func TestKernelIngress(t *testing.T) {
	h := NewHub()
	ice, _ := pair(t, h)
	o := listen(t)
	defer o.Close()
	if _, err := o.WriteTo([]byte("hallo"), ice.LocalAddr()); err != nil {
		t.Fatal(err)
	}
	b, a, err := readTimeout(t, ice, time.Second)
	if err != nil || string(b) != "hallo" || a.String() != o.LocalAddr().String() {
		t.Fatalf("%q von %v: %v", b, a, err)
	}
}

// Nach dem Schliessen ist das Ende abgemeldet: Pakete an seine Adresse gehen an den Kernel-Weg.
func TestCloseUnregisters(t *testing.T) {
	h := NewHub()
	ice, relay := pair(t, h)
	relay.Close()
	if h.View().Ends != 1 {
		t.Fatalf("ends %d", h.View().Ends)
	}
	_, _ = ice.WriteTo(rtp(10), relay.LocalAddr())
	if h.Stats.Kernel.Load() != 1 {
		t.Fatal("geschlossenes ende noch im hub")
	}
}

// Viele Schreiber, ein Leser: nichts geht verloren, Reihenfolge je Schreiber bleibt, keine haengenden Schreiber.
func TestConcurrentWriters(t *testing.T) {
	h := NewHub()
	h.Limit = 3000
	h.MaxWait = 5 * time.Second
	ice, relay := pair(t, h)
	const W, N = 4, 300
	for w := 0; w < W; w++ {
		go func(w int) {
			for i := 0; i < N; i++ {
				b := rtp(200)
				b[1], b[2], b[3] = byte(w), byte(i>>8), byte(i)
				if _, err := ice.WriteTo(b, relay.LocalAddr()); err != nil {
					return
				}
			}
		}(w)
	}
	last := [W]int{-1, -1, -1, -1}
	buf := make([]byte, 400)
	for k := 0; k < W*N; k++ {
		_ = relay.SetReadDeadline(time.Now().Add(2 * time.Second))
		n, _, err := relay.ReadFrom(buf)
		if err != nil || n != 200 {
			t.Fatalf("nach %d: %d %v", k, n, err)
		}
		w, i := int(buf[1]), int(buf[2])<<8|int(buf[3])
		if i != last[w]+1 {
			t.Fatalf("schreiber %d: %d nach %d", w, i, last[w])
		}
		last[w] = i
		if k%50 == 0 {
			time.Sleep(time.Millisecond) // langsamer Leser
		}
	}
	if h.Stats.Dropped.Load() != 0 || h.Stats.Timeouts.Load() != 0 {
		t.Fatalf("dropped %d timeouts %d", h.Stats.Dropped.Load(), h.Stats.Timeouts.Load())
	}
}

func BenchmarkPipe(b *testing.B) {
	h := NewHub()
	ice := h.Attach(mustListen(), ICE, nil)
	relay := h.Attach(mustListen(), Relay, nil)
	defer ice.Close()
	defer relay.Close()
	go func() {
		buf := make([]byte, 2048)
		for {
			if _, _, err := relay.ReadFrom(buf); err != nil {
				return
			}
		}
	}()
	p := rtp(1200)
	b.SetBytes(int64(len(p)))
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_, _ = ice.WriteTo(p, relay.LocalAddr())
	}
}

func mustListen() *net.UDPConn {
	c, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		panic(err)
	}
	return c
}
