"""Web-Messung mit Playwright (headless Chromium): Startseite, Seitenbaum, grosse Datenbank oeffnen/filtern/umschalten,
Suche, neuer Eintrag, Speicher des Tabs. Anmeldung per Sitzungs-Cookie aus gen.py (kein Formular).

    docker run --rm --network host -v $PWD/tools/perf:/w -v $PWD/perf-out:/o mcr.microsoft.com/playwright/python:v1.48.0-noble \
      sh -c "pip install -q --break-system-packages playwright==1.48.0; python /w/web.py --base http://127.0.0.1:8170 --info /o/L.info.json --out /o/L.web.json"
"""
import argparse
import json
import time
import urllib.parse

from playwright.sync_api import sync_playwright

LONGTASKS = """window.__lt = {n: 0, ms: 0, max: 0};
try { new PerformanceObserver(function (l) { l.getEntries().forEach(function (e) { __lt.n++; __lt.ms += e.duration; __lt.max = Math.max(__lt.max, e.duration); }); })
  .observe({entryTypes: ['longtask']}); } catch (e) {}"""
R = []
OUT = {"path": None, "stufe": None}


def rec(name, ms, **kw):
    d = {"name": name, "ms": round(ms, 1), **kw}
    R.append(d)
    print(f"  {name:<52} {d['ms']:>9} ms  {json.dumps(kw) if kw else ''}", flush=True)
    if OUT["path"]:                                    # nach jedem Schritt sichern (Zeitueberschreitung spaeter: Teilergebnis bleibt)
        json.dump({"stufe": OUT["stufe"], "results": R}, open(OUT["path"], "w"), indent=1)


def metrics(cdp):
    m = {x["name"]: x["value"] for x in cdp.send("Performance.getMetrics")["metrics"]}
    return {"heap_mb": round(m.get("JSHeapUsedSize", 0) / 1048576, 1), "nodes": int(m.get("Nodes", 0)),
            "script_s": round(m.get("ScriptDuration", 0), 2), "layout_s": round(m.get("LayoutDuration", 0), 2)}


def lt(p):
    v = p.evaluate("window.__lt || {}")
    p.evaluate("window.__lt = {n: 0, ms: 0, max: 0}")
    return {"longtasks": v.get("n", 0), "longtask_ms": round(v.get("ms", 0)), "longtask_max": round(v.get("max", 0))}


def timed(p, action, cond, timeout=300_000):
    t0 = time.perf_counter()
    action()
    p.wait_for_function(cond, timeout=timeout, polling=50)
    return (time.perf_counter() - t0) * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--info", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--db-timeout", type=int, default=600, help="Sekunden je Datenbank-Schritt, danach abbrechen")
    ap.add_argument("--ohne-db", action="store_true", help="Datenbank-Schritte auslassen (nur Baum/Suche/Seite)")
    a = ap.parse_args()
    info = json.load(open(a.info))
    OUT.update(path=a.out, stufe=info.get("stufe"))
    u = urllib.parse.urlparse(a.base)
    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--enable-precise-memory-info"])
        # bypass_csp: wait_for_function wertet Zeichenketten aus - Saga erlaubt (richtig) kein 'unsafe-eval'
        ctx = br.new_context(viewport={"width": 1440, "height": 900}, locale="de-DE", bypass_csp=True)
        ctx.add_cookies([{"name": "saga", "value": info["cookie_a"], "domain": u.hostname, "path": "/"}])
        ctx.add_init_script(LONGTASKS)
        p = ctx.new_page()
        cdp = ctx.new_cdp_session(p)
        cdp.send("Performance.enable")
        reqs = []
        p.on("requestfinished", lambda r: reqs.append((r.url, (r.sizes() or {}).get("responseBodySize", 0))))
        # Startseite
        t0 = time.perf_counter()
        p.goto(a.base + "/", wait_until="load")
        rec("Startseite (load)", (time.perf_counter() - t0) * 1000, **lt(p))
        # Seitenbaum
        reqs.clear()
        t0 = time.perf_counter()
        p.goto(a.base + "/notes", wait_until="commit")
        p.wait_for_selector("#pg-tree li.pg-node", timeout=300_000)
        t_tree = (time.perf_counter() - t0) * 1000
        try:
            p.wait_for_load_state("networkidle", timeout=120_000)
        except Exception:                             # Live-Verbindung haelt das Netz ggf. dauerhaft offen
            pass
        rec("Seiten: bis Baum sichtbar", t_tree, **lt(p))
        rec("Seiten: bis Netz ruhig", (time.perf_counter() - t0) * 1000, requests=len(reqs), kb=round(sum(s for _, s in reqs) / 1024),
            **metrics(cdp))
        # Ast aufklappen (renderTree komplett neu)
        tw = p.locator("#pg-tree .pg-twisty:not(.leaf)").first
        rec("Baum: Ast aufklappen (renderTree)", timed(p, lambda: tw.click(), "() => document.querySelector('#pg-tree .pg-twisty.open')"), **lt(p))
        # Suche im Baum
        rec("Suche: 'projekt' bis Treffer", timed(p, lambda: p.fill("#pg-search", "projekt"),
                                                   "() => { var r = document.getElementById('pg-results'); return r && !r.hidden && r.children.length > 0; }"), **lt(p))
        rec("Suche: ohne Treffer bis Hinweis", timed(p, lambda: p.fill("#pg-search", "zzkeintrefferzz"),
                                                      "() => !!document.querySelector('#pg-results .pg-noresult')"), **lt(p))
        p.fill("#pg-search", "")
        # Baum neu laden wie nach jeder Aktion/notes.tree (loadList; ab 2.2 per Abgleich): unveraendert und nach Umbenennen
        js_reload = "() => { var t = performance.now(); return window.HKPages.k.reloadList().then(function () { return performance.now() - t; }); }"
        js_rename = ("(id) => fetch('/api/notes/' + id, {method: 'PATCH', credentials: 'same-origin', headers: {'X-Saga': '1', "
                     "'Content-Type': 'application/json'}, body: JSON.stringify({title: 'perf ' + Date.now()})}).then(function () {"
                     " var t = performance.now(); return window.HKPages.k.reloadList().then(function () { return performance.now() - t; }); })")
        for label, js, arg in (("unveraendert", js_reload, None), ("nach Umbenennen", js_rename, info["page_normal"])):
            ms = sorted(p.evaluate(js, arg) if arg else p.evaluate(js) for _ in range(5))
            rec(f"Baum neu laden ({label}, Median von 5)", ms[2], **lt(p))
        try:
            if not a.ohne_db:
                db_steps(p, cdp, info, reqs, a.db_timeout * 1000)
        except Exception as ex:                       # Zeitueberschreitung: festhalten, mit frischem Tab weitermachen
            rec(f"DB-Schritte abgebrochen ({type(ex).__name__})", 0, error=str(ex).splitlines()[0][:200])
            p.close()
            p = ctx.new_page()
            cdp = ctx.new_cdp_session(p)
            cdp.send("Performance.enable")
            p.goto(a.base + "/notes", wait_until="commit")
            p.wait_for_selector("#pg-tree li.pg-node", timeout=300_000)
        # normale Seite oeffnen (aus dem Baum-Zustand heraus)
        t0 = time.perf_counter()
        p.evaluate(f"location.hash = '#{info['page_normal']}'")
        p.wait_for_selector("#pg-editor .ce-block", timeout=120_000)
        rec("Seite oeffnen (Editor da)", (time.perf_counter() - t0) * 1000, **lt(p), **metrics(cdp))
        js = p.evaluate("performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null")
        rec("Speicher Tab (Ende)", 0, js_heap_mb=js, **metrics(cdp))
        br.close()


# Zeilen der Tabelle: virtualisiert (2.2) steht die Gesamtzahl in table.db-table[data-rows], sonst alle <tr> im DOM
ROWS = "(function () { var t = document.querySelector('table.db-table'); if (!t) return 0; return t.dataset.rows != null ? +t.dataset.rows : t.querySelectorAll('tr.db-row').length; })()"


def db_steps(p, cdp, info, reqs, to):
    """Grosse Datenbank: oeffnen (erste Zeile sichtbar / Tabelle fertig), Suchfeld, Ansichten, ans Ende scrollen, '+ Neu'"""
    db_rows = info["db_big_rows"]
    if True:
        reqs.clear()
        t0 = time.perf_counter()
        p.evaluate(f"location.hash = '#{info['db_big']}'")
        p.wait_for_function("() => !!document.querySelector('tr.db-row')", timeout=to, polling=50)
        t_first = (time.perf_counter() - t0) * 1000
        p.wait_for_function(f"() => {ROWS} >= {db_rows}", timeout=to, polling=100)
        rec(f"DB oeffnen ({db_rows} Zeilen) bis alle Zeilen da", (time.perf_counter() - t0) * 1000,
            requests=len(reqs), kb=round(sum(s for _, s in reqs) / 1024), erste_zeile_ms=round(t_first), **lt(p), **metrics(cdp))
        # bedienbar: ein Klick in den Namen der ersten Zeile kommt an (Fokus) - misst blockierende Arbeit danach
        rec("DB: danach bedienbar (Fokus im Namen)", timed(p, lambda: p.locator("tr.db-row .db-title-in").first.click(),
            "() => document.activeElement && document.activeElement.classList.contains('db-title-in')", to), **lt(p))
        # Suchfeld der Datenbank (clientseitiges Filtern, 200 ms Verzoegerung eingeschlossen)
        p.locator("input.db-search").first.click()
        rec("DB: Suchfeld 'server' bis gefiltert", timed(p, lambda: p.keyboard.type("server"),
            f"() => {ROWS} < {db_rows}", to), **lt(p))
        p.locator("input.db-search").first.fill("")
        p.wait_for_function(f"() => {ROWS} >= {db_rows}", timeout=to, polling=100)
        lt(p)
        # ans Ende scrollen: letzte Zeile sichtbar (virtualisiert: wird erst dann eingesetzt)
        rec("DB: ans Ende scrollen bis letzte Zeile da", timed(p, lambda: p.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)"),
            "() => { var r = document.querySelectorAll('tr.db-row'), l = r[r.length - 1], f = document.querySelector('.db-table tfoot');"
            " return l && f && f.getBoundingClientRect().top - l.getBoundingClientRect().bottom < 5; }", to), **lt(p), **metrics(cdp))
        p.evaluate("window.scrollTo(0, 0)")
        # Ansicht "Offen" (Filter + Sortierung), Board, zurueck
        for label, cond in (("Offen", f"() => {{ var t = document.querySelector('.db-tab.on'); return t && t.textContent.indexOf('Offen') >= 0 && {ROWS} > 0; }}"),
                            ("Board", "() => !!document.querySelector('.board-col, .db-board, .board')"),
                            ("Kalender", "() => { var t = document.querySelector('.db-tab.on'); return t && t.textContent.indexOf('Kalender') >= 0; }"),
                            ("Tabelle", f"() => {ROWS} >= {db_rows}")):
            tab = p.locator(".db-tab", has_text=label).first
            t0 = time.perf_counter()
            ms = timed(p, lambda: tab.click(), cond, to)
            extra = {}
            if label == "Board":                      # 2.2: Karten kommen in Haeppchen - auch "alle Karten da" messen
                p.wait_for_function(f"() => document.querySelectorAll('.board-card').length >= {db_rows}", timeout=to, polling=50)
                extra["alle_karten_ms"] = round((time.perf_counter() - t0) * 1000)
            rec(f"DB: Ansicht '{label}'", ms, **extra, **lt(p))
        # Neuer Eintrag (+ Neu): POST + loadList + Zeile im DOM
        n0 = p.evaluate(ROWS)
        reqs.clear()
        rec("DB: '+ Neu' bis neue Zeile", timed(p, lambda: p.locator("button.db-new-row").first.click(),
                                                f"() => {ROWS} > {n0}", to),
            requests=len(reqs), kb=round(sum(s for _, s in reqs) / 1024), **lt(p))
        p.wait_for_timeout(1500)


if __name__ == "__main__":
    main()
