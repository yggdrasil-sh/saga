//go:build !linux

package netx

import "net"

// NotSentLowat ist ausserhalb von Linux ohne Wirkung.
var NotSentLowat = 16 * 1024

// Tune setzt ausserhalb von Linux nur TCP_NODELAY.
func Tune(c *net.TCPConn) bool {
	_ = c.SetNoDelay(true)
	return false
}
