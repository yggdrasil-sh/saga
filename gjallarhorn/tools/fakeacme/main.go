// fakeacme: lokaler ACME-Testserver fuer die Saga-Testreihen (Paket P7), nie produktiv.
//
//	fakeacme -listen 127.0.0.1:0 -txt-url 'http://127.0.0.1:18300/_txt?name=%s' -resolve horn-2.example.org=127.0.0.1:8471 -ca-out ca.pem
//
// dns-01: TXT-Werte per HTTP vom Fake-Cloudflare (JSON-Liste). tls-alpn-01: Handshake zum Knoten ueber -resolve.
// Gibt "LISTEN <port>" aus; GET /_log liefert die Pruefungen.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/acmefake"
)

type multi []string

func (m *multi) String() string     { return strings.Join(*m, ",") }
func (m *multi) Set(v string) error { *m = append(*m, v); return nil }

func main() {
	listen := flag.String("listen", "127.0.0.1:0", "Adresse")
	txt := flag.String("txt-url", "", "URL-Vorlage fuer TXT-Werte (%s = Name)")
	caOut := flag.String("ca-out", "", "Wurzel der Test-CA hierhin schreiben")
	var res multi
	flag.Var(&res, "resolve", "name=host:port fuer tls-alpn-01 (mehrfach)")
	flag.Parse()
	ln, err := net.Listen("tcp", *listen)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	s := acmefake.New()
	s.Base = "http://" + ln.Addr().String()
	m := map[string]string{}
	for _, r := range res {
		if k, v, ok := strings.Cut(r, "="); ok {
			m[strings.ToLower(k)] = v
		}
	}
	s.Dial = func(name string) (net.Conn, error) {
		a := m[strings.ToLower(name)]
		if a == "" {
			a = net.JoinHostPort(name, "443")
		}
		return net.DialTimeout("tcp", a, 5*time.Second)
	}
	if *txt != "" {
		cl := &http.Client{Timeout: 5 * time.Second}
		s.TXT = func(name string) []string {
			r, err := cl.Get(fmt.Sprintf(*txt, url.QueryEscape(name)))
			if err != nil {
				return nil
			}
			defer r.Body.Close()
			var out []string
			_ = json.NewDecoder(r.Body).Decode(&out)
			return out
		}
	}
	if *caOut != "" {
		_ = os.WriteFile(*caOut, s.CAPEM(), 0o644)
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /_log", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		n, lg := s.Snapshot()
		_ = json.NewEncoder(w).Encode(map[string]any{"issued": n, "log": lg})
	})
	mux.Handle("/", s)
	fmt.Printf("LISTEN %d\n", ln.Addr().(*net.TCPAddr).Port)
	_ = http.Serve(ln, mux)
}
