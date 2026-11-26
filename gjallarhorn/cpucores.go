package main

// 2.6 (Saga Bugs-DB 2450): wirksame Kerne des Knotens fuer Lastmeldung (cpu) und hello/pair (cores). Vorher
// runtime.GOMAXPROCS(0): mit go 1.24 die Kerne des Rechners (bzw. der Kernbindung beim Start) - eine CPU-Quote der
// cgroup (Docker --cpus, systemd CPUQuota=) zaehlte nicht. Ein Knoten mit 0,1 Kern meldete dann cpu 0,006 und "ok",
// waehrend Browser einfroren; Saga sah ihn nie als ausgelastet (kein Ueberlauf auf einen anderen Knoten).

import (
	"bufio"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

// cgroupRoot und procCgroup sind fuer Tests austauschbar.
var cgroupRoot, procCgroup = "/sys/fs/cgroup", "/proc/self/cgroup"

// effCores: min(GOMAXPROCS, kleinste CPU-Quote der eigenen cgroup und ihrer Eltern) - als Kommazahl (z. B. 0,5).
// cgroup v2 (cpu.max "quota period" bzw. "max period") und v1 (cpu.cfs_quota_us / cpu.cfs_period_us, -1 = ohne).
// Ohne lesbare Quote: GOMAXPROCS. Nie unter 0,01.
func effCores() float64 {
	n := float64(runtime.GOMAXPROCS(0))
	if q, ok := cgroupQuota(); ok && q < n {
		n = q
	}
	return math.Max(n, 0.01)
}

// reportCores: ganze Kerne fuer hello/pair (aufgerundet, mindestens 1) - Saga rechnet daraus die Kapazitaet.
func reportCores() int {
	return int(math.Max(1, math.Ceil(effCores()-1e-9)))
}

func cgroupQuota() (float64, bool) {
	best, found := math.Inf(1), false
	for _, d := range cgroupDirs() {
		if q, ok := readQuota(d); ok && q < best {
			best, found = q, true
		}
	}
	return best, found
}

// cgroupDirs: Ordner der eigenen cgroup und aller Eltern bis zur Wurzel (v2 "0::/pfad", v1 "n:cpu,cpuacct:/pfad").
// Im Container ist die eigene cgroup meist die Wurzel des Namensraums ("/") - dann nur cgroupRoot (v2) bzw.
// cgroupRoot/cpu (v1).
func cgroupDirs() []string {
	f, err := os.Open(procCgroup)
	if err != nil {
		return []string{cgroupRoot, filepath.Join(cgroupRoot, "cpu")}
	}
	defer f.Close()
	var out []string
	add := func(base, p string) {
		p = filepath.Clean("/" + p)
		for {
			out = append(out, filepath.Join(base, p))
			if p == "/" {
				return
			}
			p = filepath.Dir(p)
		}
	}
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		parts := strings.SplitN(sc.Text(), ":", 3)
		if len(parts) != 3 {
			continue
		}
		switch {
		case parts[0] == "0" && parts[1] == "":
			add(cgroupRoot, parts[2])
		case hasCtl(parts[1], "cpu"):
			for _, b := range []string{"cpu", "cpu,cpuacct", "cpuacct,cpu"} {
				if st, err := os.Stat(filepath.Join(cgroupRoot, b)); err == nil && st.IsDir() {
					add(filepath.Join(cgroupRoot, b), parts[2])
					break
				}
			}
		}
	}
	if len(out) == 0 {
		return []string{cgroupRoot, filepath.Join(cgroupRoot, "cpu")}
	}
	return out
}

func hasCtl(list, name string) bool {
	for _, c := range strings.Split(list, ",") {
		if c == name {
			return true
		}
	}
	return false
}

func readQuota(dir string) (float64, bool) {
	if b, err := os.ReadFile(filepath.Join(dir, "cpu.max")); err == nil {
		f := strings.Fields(string(b))
		if len(f) == 2 && f[0] != "max" {
			q, e1 := strconv.ParseFloat(f[0], 64)
			p, e2 := strconv.ParseFloat(f[1], 64)
			if e1 == nil && e2 == nil && q > 0 && p > 0 {
				return q / p, true
			}
		}
		return 0, false
	}
	qb, e1 := os.ReadFile(filepath.Join(dir, "cpu.cfs_quota_us"))
	pb, e2 := os.ReadFile(filepath.Join(dir, "cpu.cfs_period_us"))
	if e1 != nil || e2 != nil {
		return 0, false
	}
	q, e1 := strconv.ParseFloat(strings.TrimSpace(string(qb)), 64)
	p, e2 := strconv.ParseFloat(strings.TrimSpace(string(pb)), 64)
	if e1 != nil || e2 != nil || q <= 0 || p <= 0 {
		return 0, false
	}
	return q / p, true
}
