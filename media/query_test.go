package main

import (
	"bufio"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// queryTargets: Anfrageziele, die am Upstream byte-gleich ankommen muessen (Bugs-DB 2182: ReverseProxy sortierte die
// Query, signierte Foederations-Anfragen decken "@query" ab -> 401). Reihenfolge, doppelte Schluessel, ';',
// Prozent-Kodierung (auch klein geschrieben und ungueltig), '+', leere Werte, Schluessel ohne '=', nur '?'.
var queryTargets = []string{
	"/fed/v2/chat/msgs?ref=abc&after=0",
	"/fed/v2/directory?since=0&cfg=",
	"/fed/v2/directory?since=1763550000.25&cfg=ab12",
	"/api/x?b=2&a=1&a=0&b=1&a=2",
	"/api/x?a=1;b=2&c=3",
	"/api/x?z=;&y=1",
	"/api/x?q=%7e%2F%20x+y&z=%C3%A4&e=%3D%26",
	"/api/x?bad=%zz&a=1",
	"/api/x?bad=%4&a=1",
	"/api/x?leer=&=wert&nur&&x=1",
	"/api/x?",
	"/api/x",
	"/fed/v2/chat/msgs/a%2Fb%20c:d@e!$&'()*+,;=?ref=r%2Fx&after=0",
}

// rawGet: Anfragezeile unveraendert senden (der Go-Client wuerde nichts aendern, so ist es aber sicher).
func rawGet(t *testing.T, addr, target string) string {
	t.Helper()
	c, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(5 * time.Second))
	if _, err := io.WriteString(c, "GET "+target+" HTTP/1.1\r\nHost: saga.example\r\nConnection: close\r\n\r\n"); err != nil {
		t.Fatal(err)
	}
	resp, err := http.ReadResponse(bufio.NewReader(c), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		t.Fatalf("%s: HTTP %d %q", target, resp.StatusCode, b)
	}
	return string(b)
}

func checkTargets(t *testing.T, addr string) {
	t.Helper()
	for _, want := range queryTargets {
		if got := rawGet(t, addr, want); got != want {
			t.Errorf("Upstream sah %q statt %q", got, want)
		}
	}
}

// echoURI: antwortet mit dem Anfrageziel, wie es beim Upstream ankam.
var echoURI = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = io.WriteString(w, r.RequestURI) })

func TestProxyKeepsQuery(t *testing.T) {
	for _, gd := range []string{"", "urlmaxqueryparams=10000"} {
		t.Run("GODEBUG="+gd, func(t *testing.T) {
			if gd != "" {
				t.Setenv("GODEBUG", gd) // setzt Go zur Laufzeit um (jeder gesetzte Wert liess cleanQueryParams neu kodieren)
			}
			up := httptest.NewServer(echoURI)
			defer up.Close()
			c := &config{upstream: strings.TrimPrefix(up.URL, "http://"), blobDir: t.TempDir(), trusted: parseTrust("127.0.0.1"),
				ttl: time.Minute, stall: 5 * time.Second, key: "k"}
			front := httptest.NewServer(newProxy(c, newTransport()))
			defer front.Close()
			checkTargets(t, strings.TrimPrefix(front.URL, "http://"))
		})
	}
}

func TestNodeProxyKeepsQuery(t *testing.T) {
	sock := t.TempDir() + "/node.sock"
	l, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	go http.Serve(l, echoURI)
	c := &config{trusted: parseTrust("127.0.0.1")}
	front := httptest.NewServer(newNodeProxy(sock, "k", c))
	defer front.Close()
	checkTargets(t, strings.TrimPrefix(front.URL, "http://"))
}
