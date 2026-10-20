package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"time"
)

// callPython: interne Anfrage an Saga (uvicorn) mit dem gemeinsamen Geheimnis. in = JSON-Koerper (nil: keiner),
// out = Ziel fuer die JSON-Antwort (nil: verwerfen). from = Client-Anfrage, deren Anmelde-Koepfe mitgehen (nil: keine).
// -> HTTP-Status und Rohkoerper der Antwort (hoechstens 1 MiB).
func (m *media) callPython(ctx context.Context, method, path string, in any, out any, from *http.Request) (int, []byte, error) {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	var body io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return 0, nil, err
		}
		body = bytes.NewReader(b)
	}
	q, err := http.NewRequestWithContext(ctx, method, "http://"+m.c.upstream+path, body)
	if err != nil {
		return 0, nil, err
	}
	if from != nil {
		q.Host = from.Host
		copyAuth(q.Header, from)
		setForwarded(q.Header, from, m.c.trusted)
	}
	if in != nil {
		q.Header.Set("Content-Type", "application/json")
	}
	q.Header.Set(keyHeader, m.c.key)
	resp, err := m.client.Do(q)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return resp.StatusCode, nil, err
	}
	if out != nil && resp.StatusCode == http.StatusOK {
		if err := json.Unmarshal(raw, out); err != nil {
			return resp.StatusCode, raw, err
		}
	}
	return resp.StatusCode, raw, nil
}

// writeJSON: kleine JSON-Antwort von saga-media selbst (Uploads).
func writeJSON(w http.ResponseWriter, status int, v any) {
	b, _ := json.Marshal(v)
	h := w.Header()
	h.Set("Content-Type", "application/json")
	h.Set("Cache-Control", "no-store")
	h.Set("Content-Length", itoa(int64(len(b))))
	w.WriteHeader(status)
	_, _ = w.Write(b)
}
