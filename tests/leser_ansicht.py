"""Bughunt W06/W10: Leser einer geteilten Seite sehen an Schaltflaechen kein Einrichten (kein Zahnrad, kein "+"), und
eigene Felder (Toggle-Titel) sind nicht bearbeitbar; Bearbeiter sehen beides. Wegwerf-Konten lv-*; raeumt auf.
Braucht Playwright (node) wie tests/browser_*.js."""
import base64, http.client, json, os, subprocess, sys, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res = []


def ok(n, c, info=""):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c else info, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Cookie": "saga=" + cookie, "X-Saga": "1", "Content-Type": "application/json"}
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse(); d = r.read()
    try:
        d = json.loads(d)
    except ValueError:
        pass
    c.close()
    return r.status, d


def cleanup():
    from saga.userdata import delete_user_data
    for u in ("lv-a", "lv-b"):
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'lv-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'lv-%' OR target LIKE 'lv-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'lv-%'")


JS = r"""
const PW = require('playwright');
(async () => {
  const [base, cookie, id] = process.argv.slice(2);
  const br = await PW.chromium.launch();
  const ctx = await br.newContext({ locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: cookie, domain: new URL(base).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  await p.goto(base + '/notes?t=' + Date.now() + '#' + id);
  await p.waitForSelector('.nbtn'); await p.waitForTimeout(700);
  const r = await p.evaluate(() => ({ cfg: document.querySelectorAll('.nbtn-cfg').length, add: document.querySelectorAll('.nbtn-add').length,
    btn: document.querySelectorAll('.nbtn').length, tgl: (document.querySelector('.tgl-title') || {}).contentEditable }));
  r.errs = errs; console.log(JSON.stringify(r)); await br.close();
})().catch((e) => { console.log(JSON.stringify({ fail: e.message })); process.exit(1); });
"""


def browse(cookie, nid):
    out = subprocess.run(["node", "-e", JS, "x", "http://%s:%s" % (B_.hostname, B_.port), cookie, str(nid)],
                         capture_output=True, text=True, timeout=120, env=dict(os.environ, NO_PROXY="*"))
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"fail": out.stdout[-300:] + out.stderr[-300:]}


def main():
    cleanup()
    prev = auth.settings(fresh=True).get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    auth.create_user("lv-a", "Leser-Pass-12345", "Anna Leser")
    auth.create_user("lv-b", "Leser-Pass-12345", "Bert Leser")
    A, Bk = ck("lv-a"), ck("lv-b")
    time.sleep(5.6)
    try:
        seite = req("POST", "/api/notes", A, {"title": "Knopfseite"})[1]["id"]
        req("PATCH", f"/api/notes/{seite}", A, {"format": "blocks", "body": json.dumps({"blocks": [
            {"type": "buttons", "data": {"buttons": [{"label": "Neu", "action": "", "target": None, "tpl": None, "title": ""}]}},
            {"type": "toggle", "data": {"title": "Klapp", "open": True, "blocks": []}}]})})
        req("PUT", f"/api/notes/{seite}/shares", A, {"type": "user", "target": "lv-b", "role": "view"})
        r = browse(Bk, seite)
        ok("Leser: Schaltflaeche sichtbar", r.get("btn") == 1, r)
        ok("Leser: kein Zahnrad, kein +", r.get("cfg") == 0 and r.get("add") == 0, r)
        ok("Leser: Toggle-Titel nicht bearbeitbar", r.get("tgl") == "false", r)
        ok("Leser: keine Seitenfehler", not r.get("errs"), r)
        req("PUT", f"/api/notes/{seite}/shares", A, {"type": "user", "target": "lv-b", "role": "edit"})
        r = browse(Bk, seite)
        ok("Bearbeiter: Zahnrad und + da", r.get("cfg") == 1 and r.get("add") == 1, r)
        ok("Bearbeiter: Toggle-Titel bearbeitbar", r.get("tgl") == "true", r)
        r = browse(A, seite)
        ok("Eigentuemer: Zahnrad und + da", r.get("cfg") == 1 and r.get("add") == 1, r)
    finally:
        cleanup()
        auth.save_setting("share_enabled", prev)
    print()
    print("LESER-ANSICHT %d/%d - %s" % (sum(res), len(res), "ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


main()
