package main

import "testing"

func TestGlobalIP(t *testing.T) {
	for a, want := range map[string]bool{
		"203.0.113.7": true, "46.225.102.31": true, "2a01:4f8::1": true,
		"100.64.12.34": false, "10.0.0.1": false, "192.168.0.5": false, "172.16.3.4": false,
		"127.0.0.1": false, "::1": false, "fd00::1": false, "fe80::1": false, "": false,
	} {
		if got := globalIP(a); got != want {
			t.Errorf("globalIP(%q) = %v, erwartet %v", a, got, want)
		}
	}
}

func TestPublicAddrsOverride(t *testing.T) {
	s := &server{cfg: &config{PublicIPs: []string{"203.0.113.7", "2a01:4f8::1", "198.51.100.1"}}}
	v4, v6, dropped := s.publicAddrs("http://127.0.0.1:1")
	if v4 != "203.0.113.7" || v6 != "2a01:4f8::1" || dropped != nil {
		t.Fatalf("HORN_PUBLIC_IP: v4=%q v6=%q dropped=%v", v4, v6, dropped)
	}
}
