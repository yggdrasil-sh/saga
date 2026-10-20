package main

// Verbindungsgrenzen (2.4, Fund S1 des Pruefteams): saga-media lauscht selbst auf dem oeffentlichen PORT. Ohne Grenze
// je Client-Adresse konnte eine einzelne Adresse mit billigen Keep-Alive-Verbindungen im Leerlauf MEDIA_MAX_CONNS ganz
// belegen und alle anderen aussperren. Jetzt:
//   - hoechstens MEDIA_MAX_CONNS_IP offene Verbindungen je Adresse (vertraute Proxys aus TRUSTED_PROXIES ausgenommen -
//     hinter einem Reverse Proxy kommt alles von dessen Adresse); darueber wird zuerst die am laengsten ruhende
//     Verbindung dieser Adresse geschlossen, ruht keine, wird die neue sofort geschlossen.
//   - ist die Grenze insgesamt erreicht, wird die am laengsten ruhende Verbindung (gleich welcher Adresse) geschlossen,
//     statt neue Verbindungen im Backlog warten zu lassen. Nur wenn keine ruht, wartet Accept wie bisher.
//   - Leerlauf ohne Anmeldedaten (kein Cookie, kein Authorization-, kein Medien-Token-Kopf) endet nach
//     MEDIA_IDLE_UNAUTH Sekunden (Standard 10), mit Anmeldedaten nach IdleTimeout (75 s) wie bisher.
// Eine ruhende Keep-Alive-Verbindung zu schliessen ist folgenlos: Browser und Apps oeffnen beim naechsten Abruf eine neue.

import (
	"context"
	"io"
	"net"
	"net/http"
	"sync"
	"time"
)

type connKey struct{}

type trackedConn struct {
	net.Conn
	l       *limitListener
	ip      string
	limited bool // zaehlt gegen die Grenze je Adresse (nicht bei vertrauten Proxys)
	once    sync.Once

	// unter l.mu
	idle      bool
	idleSince time.Time
	auth      bool
	timer     *time.Timer
	epoch     uint64
}

func (c *trackedConn) Close() error {
	err := c.Conn.Close()
	c.once.Do(func() { c.l.forget(c) })
	return err
}

// ReadFrom: sendfile der TCP-Verbindung erhalten (net/http prueft auf io.ReaderFrom).
func (c *trackedConn) ReadFrom(r io.Reader) (int64, error) {
	if rf, ok := c.Conn.(io.ReaderFrom); ok {
		return rf.ReadFrom(r)
	}
	return io.Copy(struct{ io.Writer }{c.Conn}, r)
}

// limitListener: Grenzen insgesamt (sem) und je Adresse, Leerlauf-Verwaltung ueber den ConnState-Haken.
type limitListener struct {
	net.Listener
	sem        chan struct{}
	maxIP      int
	trusted    trustSet
	idleUnauth time.Duration
	idleAuth   time.Duration

	mu    sync.Mutex
	all   map[*trackedConn]struct{}
	perIP map[string]map[*trackedConn]struct{}
}

func newLimitListener(ln net.Listener, c *config) *limitListener {
	return &limitListener{Listener: ln, sem: make(chan struct{}, c.maxConns), maxIP: c.maxConnsIP, trusted: c.trusted,
		idleUnauth: c.idleUnauth, idleAuth: 75 * time.Second,
		all: map[*trackedConn]struct{}{}, perIP: map[string]map[*trackedConn]struct{}{}}
}

func (l *limitListener) Accept() (net.Conn, error) {
	for {
		raw, err := l.Listener.Accept()
		if err != nil {
			return nil, err
		}
		a := hostOf(raw.RemoteAddr().String())
		c := &trackedConn{Conn: raw, l: l, ip: a.String(), limited: !l.trusted.has(a)}
		if c.limited && !l.roomForIP(c.ip) {
			_ = raw.Close() // Adresse hat schon zu viele aktive Verbindungen
			continue
		}
		select {
		case l.sem <- struct{}{}:
		default:
			l.evictIdle("") // Grenze insgesamt: die am laengsten ruhende Verbindung schliessen (gibt einen Platz frei)
			l.sem <- struct{}{}
		}
		l.mu.Lock()
		l.all[c] = struct{}{}
		m := l.perIP[c.ip]
		if m == nil {
			m = map[*trackedConn]struct{}{}
			l.perIP[c.ip] = m
		}
		m[c] = struct{}{}
		l.mu.Unlock()
		return c, nil
	}
}

// roomForIP: Platz fuer eine weitere Verbindung dieser Adresse - notfalls deren am laengsten ruhende schliessen.
func (l *limitListener) roomForIP(ip string) bool {
	l.mu.Lock()
	n := 0
	for c := range l.perIP[ip] {
		if c.limited {
			n++
		}
	}
	l.mu.Unlock()
	if n < l.maxIP {
		return true
	}
	return l.evictIdle(ip)
}

// evictIdle: am laengsten ruhende Verbindung (ip == "": gleich welche) schliessen -> true, wenn eine geschlossen wurde.
func (l *limitListener) evictIdle(ip string) bool {
	l.mu.Lock()
	var best *trackedConn
	set := l.all
	if ip != "" {
		set = l.perIP[ip]
	}
	for c := range set {
		if c.idle && (best == nil || c.idleSince.Before(best.idleSince)) {
			best = c
		}
	}
	if best != nil {
		best.idle = false // nicht doppelt waehlen
	}
	l.mu.Unlock()
	if best == nil {
		return false
	}
	_ = best.Close()
	return true
}

func (l *limitListener) forget(c *trackedConn) {
	l.mu.Lock()
	if c.timer != nil {
		c.timer.Stop()
	}
	delete(l.all, c)
	if m := l.perIP[c.ip]; m != nil {
		delete(m, c)
		if len(m) == 0 {
			delete(l.perIP, c.ip)
		}
	}
	l.mu.Unlock()
	<-l.sem
}

// connState: ruhend/aktiv merken; ruhende Verbindungen nach ihrer Leerlaufzeit schliessen.
func (l *limitListener) connState(nc net.Conn, s http.ConnState) {
	c, ok := nc.(*trackedConn)
	if !ok {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	c.epoch++
	if c.timer != nil {
		c.timer.Stop()
		c.timer = nil
	}
	if s != http.StateIdle {
		c.idle = false
		return
	}
	c.idle, c.idleSince = true, time.Now()
	d := l.idleUnauth
	if c.auth {
		d = l.idleAuth
	}
	ep := c.epoch
	c.timer = time.AfterFunc(d, func() {
		l.mu.Lock()
		still := c.idle && c.epoch == ep
		l.mu.Unlock()
		if still {
			_ = c.Close()
		}
	})
}

// connContext: Verbindung im Kontext jeder Anfrage (markAuth).
func connContext(ctx context.Context, nc net.Conn) context.Context {
	if c, ok := nc.(*trackedConn); ok {
		return context.WithValue(ctx, connKey{}, c)
	}
	return ctx
}

// markAuth: Anfrage mit Anmeldedaten -> diese Verbindung darf laenger ruhen.
func markAuth(r *http.Request) {
	c, ok := r.Context().Value(connKey{}).(*trackedConn)
	if !ok || (r.Header.Get("Cookie") == "" && r.Header.Get("Authorization") == "" && r.Header.Get(tokenHeader) == "") {
		return
	}
	c.l.mu.Lock()
	c.auth = true
	c.l.mu.Unlock()
}
