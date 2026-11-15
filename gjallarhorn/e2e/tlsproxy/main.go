// tlsproxy: kleiner TLS-beendender HTTP-Proxy als NPM-Ersatz dort, wo kein nginx laeuft (Mac-Pruefstand, Spike
// ws-apps): TLS mit selbst erzeugtem Zertifikat (127.0.0.1/localhost), dann HTTP/1.1 an das Ziel, WebSocket-Upgrade
// wie NPM mit "Websockets Support", X-Forwarded-For/X-Real-IP. Nur fuer Tests.
//
//	tlsproxy -listen 127.0.0.1:8501 -to http://127.0.0.1:8502
package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"flag"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"time"
)

func selfCert() tls.Certificate {
	k, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	t := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "saga.example.org"}, NotBefore: time.Now().Add(-time.Hour),
		NotAfter: time.Now().Add(30 * 24 * time.Hour), DNSNames: []string{"saga.example.org", "localhost"}, IPAddresses: []net.IP{net.IPv4(127, 0, 0, 1)},
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, _ := x509.CreateCertificate(rand.Reader, t, t, &k.PublicKey, k)
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: k}
}

func main() {
	lis := flag.String("listen", "127.0.0.1:8501", "TLS-Port")
	to := flag.String("to", "http://127.0.0.1:8502", "Ziel")
	h2 := flag.Bool("h2", false, "HTTP/2 vorn anbieten (wie NPM \"HTTP/2 Support\")")
	flag.Parse()
	u, err := url.Parse(*to)
	if err != nil {
		log.Fatal(err)
	}
	p := &httputil.ReverseProxy{Rewrite: func(pr *httputil.ProxyRequest) {
		pr.SetURL(u)
		pr.Out.Host = pr.In.Host
		pr.SetXForwarded()
		h, _, _ := net.SplitHostPort(pr.In.RemoteAddr)
		pr.Out.Header.Set("X-Real-IP", h)
	}}
	np := []string{"http/1.1"}
	if *h2 {
		np = []string{"h2", "http/1.1"}
	}
	srv := &http.Server{Addr: *lis, Handler: p, TLSConfig: &tls.Config{Certificates: []tls.Certificate{selfCert()}, NextProtos: np}}
	log.Printf("tlsproxy %s -> %s", *lis, *to)
	log.Fatal(srv.ListenAndServeTLS("", ""))
}
