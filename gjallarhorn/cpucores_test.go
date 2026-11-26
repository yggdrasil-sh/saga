package main

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

// Saga Bugs-DB 2450: CPU-Quote der cgroup zaehlt fuer cpu (Lastmeldung) und cores (hello/pair).
func TestEffCoresQuote(t *testing.T) {
	keepRoot, keepProc := cgroupRoot, procCgroup
	defer func() { cgroupRoot, procCgroup = keepRoot, keepProc }()
	d := t.TempDir()
	w := func(p, s string) {
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(s), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	gm := float64(runtime.GOMAXPROCS(0))
	cgroupRoot, procCgroup = filepath.Join(d, "cg"), filepath.Join(d, "proc-cgroup")

	// v2, eigene cgroup ohne Quote, Elternteil 0,1 Kern
	w(procCgroup, "0::/system.slice/horn.service\n")
	w(filepath.Join(cgroupRoot, "system.slice/horn.service/cpu.max"), "max 100000\n")
	w(filepath.Join(cgroupRoot, "system.slice/cpu.max"), "10000 100000\n")
	if got := effCores(); got != 0.1 {
		t.Fatalf("v2 Eltern-Quote 0,1: %v", got)
	}
	if got := reportCores(); got != 1 {
		t.Fatalf("cores bei 0,1: %d", got)
	}
	// v2 1,5 Kerne in der eigenen cgroup (kleiner als die Eltern)
	w(filepath.Join(cgroupRoot, "system.slice/horn.service/cpu.max"), "150000 100000\n")
	w(filepath.Join(cgroupRoot, "system.slice/cpu.max"), "max 100000\n")
	if want := min(1.5, gm); effCores() != want || reportCores() != int(max(1, ceil(want))) {
		t.Fatalf("v2 1,5: %v %d", effCores(), reportCores())
	}
	// v2 Container: eigene cgroup ist die Wurzel
	w(procCgroup, "0::/\n")
	w(filepath.Join(cgroupRoot, "cpu.max"), "200000 100000\n")
	if want := min(2, gm); effCores() != want {
		t.Fatalf("v2 Wurzel 2: %v", effCores())
	}
	// ohne Quote: GOMAXPROCS
	w(filepath.Join(cgroupRoot, "cpu.max"), "max 100000\n")
	if effCores() != gm || reportCores() != int(gm) {
		t.Fatalf("ohne Quote: %v %d (GOMAXPROCS %v)", effCores(), reportCores(), gm)
	}
	// v1 mit cpu,cpuacct, Quote 0,5; -1 = ohne
	d1 := filepath.Join(d, "v1")
	cgroupRoot = d1
	w(procCgroup, "12:memory:/x\n4:cpu,cpuacct:/docker/abc\n")
	w(filepath.Join(d1, "cpu,cpuacct/docker/abc/cpu.cfs_quota_us"), "50000\n")
	w(filepath.Join(d1, "cpu,cpuacct/docker/abc/cpu.cfs_period_us"), "100000\n")
	w(filepath.Join(d1, "cpu,cpuacct/cpu.cfs_quota_us"), "-1\n")
	w(filepath.Join(d1, "cpu,cpuacct/cpu.cfs_period_us"), "100000\n")
	if got := effCores(); got != 0.5 {
		t.Fatalf("v1 0,5: %v", got)
	}
	// Unsinn bzw. keine Dateien: GOMAXPROCS
	w(filepath.Join(d1, "cpu,cpuacct/docker/abc/cpu.cfs_quota_us"), "kaputt\n")
	if effCores() != gm {
		t.Fatalf("v1 Unsinn: %v", effCores())
	}
	procCgroup = filepath.Join(d, "fehlt")
	cgroupRoot = filepath.Join(d, "leer")
	if effCores() != gm {
		t.Fatalf("ohne cgroup: %v", effCores())
	}
}

func ceil(f float64) float64 {
	i := float64(int(f))
	if f > i {
		return i + 1
	}
	return i
}
