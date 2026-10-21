//go:build !linux

package netx

import (
	"net"
	"time"
)

// NotSentLowat ist ausserhalb von Linux ohne Wirkung.
var NotSentLowat = 16 * 1024

// Tune setzt ausserhalb von Linux nur TCP_NODELAY.
func Tune(c *net.TCPConn) bool {
	_ = c.SetNoDelay(true)
	return false
}

// TCPLoss ist ausserhalb von Linux nicht verfuegbar.
func TCPLoss(*net.TCPConn) (time.Duration, uint32, bool) { return 0, 0, false }
