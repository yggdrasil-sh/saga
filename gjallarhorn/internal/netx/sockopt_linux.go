//go:build linux

package netx

import (
	"net"

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
