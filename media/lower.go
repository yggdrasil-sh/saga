package main

import (
	"io"
	"net/http"
	"strings"
)

// Kopfzeilen, die net/http selbst auswertet (Laenge, Typ-Erkennung, Datum, Verbindung) - die bleiben in der
// kanonischen Schreibweise, sonst kaemen sie doppelt.
var keepCanonical = map[string]bool{"Content-Length": true, "Content-Type": true, "Content-Encoding": true,
	"Transfer-Encoding": true, "Date": true, "Connection": true, "Trailer": true}

// lowerKeys: uvicorn schickt Kopfzeilen klein geschrieben, net/http kanonisch ("location" -> "Location"). HTTP ist
// hier zwar unabhaengig von Gross-/Kleinschreibung, aber Clients mit woertlichem Vergleich sollen nichts merken.
func lowerKeys(h http.Header) {
	for k, v := range h {
		if keepCanonical[k] {
			continue
		}
		if l := strings.ToLower(k); l != k {
			delete(h, k)
			h[l] = append(h[l], v...)
		}
	}
}

// lowerWriter: lowerKeys vor dem Senden der Kopfzeilen. Flush/Hijack/Fristen ueber Unwrap (http.ResponseController),
// sendfile ueber ReadFrom.
type lowerWriter struct {
	http.ResponseWriter
	wrote bool
}

func (l *lowerWriter) Unwrap() http.ResponseWriter { return l.ResponseWriter }

func (l *lowerWriter) WriteHeader(code int) {
	lowerKeys(l.Header())
	if code >= 200 {
		l.wrote = true
	}
	l.ResponseWriter.WriteHeader(code)
}

func (l *lowerWriter) Write(b []byte) (int, error) {
	if !l.wrote {
		l.WriteHeader(http.StatusOK)
	}
	return l.ResponseWriter.Write(b)
}

func (l *lowerWriter) ReadFrom(r io.Reader) (int64, error) {
	if !l.wrote {
		l.WriteHeader(http.StatusOK)
	}
	if rf, ok := l.ResponseWriter.(io.ReaderFrom); ok {
		return rf.ReadFrom(r)
	}
	return io.Copy(l.ResponseWriter, r)
}
