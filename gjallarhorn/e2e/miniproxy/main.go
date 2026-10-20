// miniproxy: lokaler TCP-Durchreicher fuer Tests, der vor jede Verbindung
// einen PROXY-Protocol-v2-Kopf mit einer erfundenen Client-Adresse setzt
// (192.0.2.77, Dokumentationsnetz). Aufruf: miniproxy :18444 127.0.0.1:18443
package main

import (
	"io"
	"log"
	"net"
	"os"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
)

func main() {
	if len(os.Args) != 3 {
		log.Fatal("aufruf: miniproxy <lauschen> <ziel>")
	}
	ln, err := net.Listen("tcp", os.Args[1])
	if err != nil {
		log.Fatal(err)
	}
	for {
		c, err := ln.Accept()
		if err != nil {
			log.Fatal(err)
		}
		go func() {
			defer c.Close()
			u, err := net.Dial("tcp", os.Args[2])
			if err != nil {
				return
			}
			defer u.Close()
			src := &net.TCPAddr{IP: net.ParseIP("192.0.2.77"), Port: c.RemoteAddr().(*net.TCPAddr).Port}
			if _, err := u.Write(netx.BuildProxyV2(src, u.RemoteAddr().(*net.TCPAddr))); err != nil {
				return
			}
			go func() { _, _ = io.Copy(u, c); u.(*net.TCPConn).CloseWrite() }()
			_, _ = io.Copy(c, u)
		}()
	}
}
