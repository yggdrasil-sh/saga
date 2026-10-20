//go:build linux

package netx

import (
	"net"
	"time"

	"golang.org/x/sys/unix"
)

// NotSentLowat begrenzt ungesendete Bytes im Kernel (H2). Die Warteschlange
// liegt damit im Knoten, wo er Rahmen verwerfen kann.
var NotSentLowat = 16 * 1024

// Tune setzt TCP_NODELAY, TCP_NOTSENT_LOWAT und, wenn erlaubt, BBR.
// Rueckgabe: ob BBR aktiv ist.
func Tune(c *net.TCPConn) (bbr bool) {
	_ = c.SetNoDelay(true)
	rc, err := c.SyscallConn()
	if err != nil {
		return false
	}
	_ = rc.Control(func(fd uintptr) {
		_ = unix.SetsockoptInt(int(fd), unix.IPPROTO_TCP, unix.TCP_NOTSENT_LOWAT, NotSentLowat)
		if unix.SetsockoptString(int(fd), unix.IPPROTO_TCP, unix.TCP_CONGESTION, "bbr") == nil {
			bbr = true
			return
		}
		if s, err := unix.GetsockoptString(int(fd), unix.IPPROTO_TCP, unix.TCP_CONGESTION); err == nil && len(s) >= 3 && s[:3] == "bbr" {
			bbr = true
		}
	})
	return bbr
}

// TCPLoss meldet Rundlaufzeit und Zahl der Wiederholungen einer
// Verbindung (TCP_INFO). ok=false, wenn nicht lesbar.
func TCPLoss(c *net.TCPConn) (rtt time.Duration, retrans uint32, ok bool) {
	rc, err := c.SyscallConn()
	if err != nil {
		return 0, 0, false
	}
	_ = rc.Control(func(fd uintptr) {
		ti, err := unix.GetsockoptTCPInfo(int(fd), unix.IPPROTO_TCP, unix.TCP_INFO)
		if err == nil {
			rtt, retrans, ok = time.Duration(ti.Rtt)*time.Microsecond, ti.Total_retrans, true
		}
	})
	return
}
