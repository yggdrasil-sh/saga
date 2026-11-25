"""Vertrag Push-Relais / Apple-Mitteilungen Ende-zu-Ende (Bugs-DB 2262-1, docs/PUSH-RELAY.md): Testvektoren
tests/vectors/push_e2e.json gegen die unabhaengige Umsetzung (tools/push_e2e_vectors.py) UND gegen den Server
(saga.push.encrypt mit pad_to, pad_size, e2e_kid, e2e_collapse) sowie saga.fedsig mit eigenem Tag (saga-relay/1, Standard
unveraendert saga-fed/1, kein Tag gilt fuer das andere). Ohne Server, ohne Datenbankzugriff.

  PYTHONPATH=tests/pgshim:. python tests/push_relais_vertrag.py"""
import base64
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import push_e2e_vectors as V  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from saga import fedsig, push  # noqa: E402

res = []


def ok(n, c):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


with open(V.PATH, encoding="utf-8") as f:
    D = json.load(f)

# --- Vektoren: unabhaengig ------------------------------------------------------------------------------------------------
bad = V.check(D)
for b in bad[:10]:
    print("  " + b)
ok(f"tools/push_e2e_vectors.py check: {len(D['cases'])} Faelle, {len(D['bad'])} Gegenfaelle", not bad)
ok("Format saga-push-e2e/1, Stufen 512/1024/2048, E2E_MAX 2400",
   D["format"] == "saga-push-e2e/1" and D["params"]["pad_steps"] == [512, 1024, 2048] and D["params"]["e2e_max"] == 2400)

# --- Server-Umsetzung gegen die Vektoren ------------------------------------------------------------------------------
ok("push.PAD_STEPS/PAD_ROUND/E2E_MAX wie im Vertrag",
   list(push.PAD_STEPS) == D["params"]["pad_steps"] and push.PAD_ROUND == D["params"]["pad_round"] and push.E2E_MAX == D["params"]["e2e_max"])
ok("push.pad_size = Tabelle pad", all(push.pad_size(n) == p for n, p in D["pad"]))
K = D["keys"]
ok(f"push.e2e_kid = keys.k ({K['k']})", push.e2e_kid(K["ua_pub"]) == K["k"])
ok("push.e2e_collapse = Tabelle collapse", all(push.e2e_collapse(K["auth"], x["tag"]) == x["collapse_id"] for x in D["collapse"]))
cs = {x["tag"]: x["collapse_id"] for x in D["collapse"]}
ok("collapse-id: job-1 != job-2, Aktualisierung != Mitteilung, ohne Klartext-tag",
   cs["job-1"] != cs["job-2"] and cs["chat-12"] != cs["uchat-12"] and all("chat" not in v and "job" not in v for v in cs.values())
   and all(len(v) == 23 and v[0] == "c" for v in cs.values()))
r = D["rfc8291"]
as_rfc = ec.derive_private_key(int.from_bytes(b64d(r["as_priv"]), "big"), ec.SECP256R1())
ok("RFC 8291 Anhang A: push.encrypt ohne pad_to unveraendert",
   push.b64u(push.encrypt(r["plain"].encode(), r["ua_pub"], r["auth"], salt=b64d(r["salt"]), as_key=as_rfc)) == r["body"] == V.RFC8291_BODY)
same = 0
for c in D["cases"]:
    ak = ec.derive_private_key(int.from_bytes(b64d(c["as_priv"]), "big"), ec.SECP256R1())
    plain = json.dumps(c["msg"], ensure_ascii=False, separators=(",", ":")).encode()
    body = push.encrypt(plain, K["ua_pub"], K["auth"], salt=b64d(c["salt"]), as_key=ak, pad_to=push.pad_size(len(plain)))
    same += plain.decode() == c["plain"] and push.b64u(body) == c["apns_body"]["saga"]["c"]
ok(f"push.encrypt(pad_to=pad_size) = Chiffrat der Vektoren ({same}/{len(D['cases'])})", same == len(D["cases"]))
ua = ec.derive_private_key(int.from_bytes(b64d(K["ua_priv"]), "big"), ec.SECP256R1())
rnd = push.encrypt(b'{"kind":"test"}', K["ua_pub"], K["auth"], pad_to=1024)
pt, rec = V.decrypt(rnd, ua, b64d(K["auth"]))
ok("zufaelliges Salz, pad_to 1024 -> App-Seite entschluesselt, Datensatz 1024 Byte", pt == b'{"kind":"test"}' and rec == 1024 and len(rnd) == 86 + 1024 + 16)
pt0, rec0 = V.decrypt(push.encrypt(b"abc", K["ua_pub"], K["auth"]), ua, b64d(K["auth"]))
ok("ohne pad_to: Datensatz = Inhalt + 0x02 (wie bisher)", pt0 == b"abc" and rec0 == 4)
ok("pad_to zu klein -> ValueError", raises(lambda: push.encrypt(b"x" * 600, K["ua_pub"], K["auth"], pad_to=512)))
ok("pad_to genau Inhalt + 1 geht", V.decrypt(push.encrypt(b"x" * 511, K["ua_pub"], K["auth"], pad_to=512), ua, b64d(K["auth"]))[1] == 512)
ok("pad_to ueber rs - 16 (4080) -> ValueError", raises(lambda: push.encrypt(b"x", K["ua_pub"], K["auth"], pad_to=4081)))
ok("pad_to kein int (True, '512') -> ValueError",
   raises(lambda: push.encrypt(b"x", K["ua_pub"], K["auth"], pad_to=True)) and raises(lambda: push.encrypt(b"x", K["ua_pub"], K["auth"], pad_to="512")))
ok("pad_size(E2E_MAX) = 2560, Chiffrat <= 2662 Byte", push.pad_size(push.E2E_MAX) == 2560 and 86 + 2560 + 16 == 2662)

# --- 2262-5: Planung und APNs-Koerper des Servers gegen die Vektoren ---------------------------------------------------
pl = bo = 0
for c in D["cases"]:
    voip = c["subscription"] == "apns-voip"
    pl += push._apns_plan(c["msg"], voip) == (c["push_type"], c["priority"], c["ttl"], c["collapse_tag"], c["rate"])
    ak = ec.derive_private_key(int.from_bytes(b64d(c["as_priv"]), "big"), ec.SECP256R1())
    bo += push._apns_e2e_body(K["ua_pub"], K["auth"], c["msg"], c["push_type"], salt=b64d(c["salt"]), as_key=ak) == c["apns_body"]
ok(f"push._apns_plan = Vektoren (push-type, priority, ttl, collapse-tag, rate) ({pl}/{len(D['cases'])})", pl == len(D["cases"]))
ok(f"push._apns_e2e_body = APNs-Koerper der Vektoren ({bo}/{len(D['cases'])})", bo == len(D["cases"]))
ok("Waechter: voip nur fuer call/call.group (job.done, chat.message, test -> ValueError)",
   all(raises(lambda k=k: push._apns_plan({"kind": k, "tag": "x"}, True)) for k in ("job.done", "job.ask", "chat.message", "test", "call.missed")))
big = {"title": "Saga", "body": "x" * 2400, "kind": "chat.message", "tag": "t"}
ok("innerer Text ueber E2E_MAX -> OverflowError (Server: fail 'zu gross')",
   raises(lambda: push._apns_e2e_body(K["ua_pub"], K["auth"], big, "alert"), OverflowError))
gr = next(c for c in D["cases"] if c["name"] == "gross_2560")
ok("Grenzfall 2391 Byte -> 2560, APNs-Koerper < 4096", gr["pad_to"] == 2560 and len(json.dumps(gr["apns_body"], separators=(",", ":"))) < 4096)

# --- fedsig: eigener Tag -----------------------------------------------------------------------------------------------
priv, pub = fedsig.new_key()
body = b'{"v":1}'
look = lambda kid: pub  # noqa: E731
H, nonce = fedsig.sign_request(priv, "s_abcdefghijklmnop", "POST", "relay.example.org", "/v1/push", "", body, tag=fedsig.RELAY_TAG)
hl = {k.lower(): v for k, v in H.items()}
ok("Relais-Signatur: tag=\"saga-relay/1\" im Signature-Input, Saga-Actor '-'",
   ';tag="saga-relay/1"' in H["Signature-Input"] and H["Saga-Actor"] == "-" and fedsig.RELAY_TAG == "saga-relay/1")
ok("Relais prueft mit tag=RELAY_TAG -> keyid/nonce",
   fedsig.verify_request(look, "POST", "relay.example.org", "/v1/push", "", hl, body, tag=fedsig.RELAY_TAG)[:2] == ("s_abcdefghijklmnop", nonce))
ok("Relais-Signatur gilt NICHT in der Foederation (Standard-Tag)",
   raises(lambda: fedsig.verify_request(look, "POST", "relay.example.org", "/v1/push", "", hl, body), fedsig.SigError))
Hf, _ = fedsig.sign_request(priv, "https://saga.example.org", "POST", "relay.example.org", "/v1/push", "", body)
hfl = {k.lower(): v for k, v in Hf.items()}
ok("Standard unveraendert: tag=\"saga-fed/1\", Foederation prueft weiter",
   ';tag="saga-fed/1"' in Hf["Signature-Input"] and fedsig.verify_request(look, "POST", "relay.example.org", "/v1/push", "", hfl, body)[0] == "https://saga.example.org")
ok("Foederations-Signatur gilt NICHT am Relais",
   raises(lambda: fedsig.verify_request(look, "POST", "relay.example.org", "/v1/push", "", hfl, body, tag=fedsig.RELAY_TAG), fedsig.SigError))
ok("anderer Pfad / anderer Koerper / andere authority -> SigError",
   raises(lambda: fedsig.verify_request(look, "POST", "relay.example.org", "/v1/servers", "", hl, body, tag=fedsig.RELAY_TAG), fedsig.SigError)
   and raises(lambda: fedsig.verify_request(look, "POST", "relay.example.org", "/v1/push", "", hl, b'{"v":2}', tag=fedsig.RELAY_TAG), fedsig.SigError)
   and raises(lambda: fedsig.verify_request(look, "POST", "relay.example.net", "/v1/push", "", hl, body, tag=fedsig.RELAY_TAG), fedsig.SigError))
ok("Zeitfenster: 301 s alt -> SigError",
   raises(lambda: fedsig.verify_request(look, "POST", "relay.example.org", "/v1/push", "",
                                        {k.lower(): v for k, v in fedsig.sign_request(priv, "reg", "POST", "relay.example.org", "/v1/push", "", body,
                                                                                      now=1798761600, tag=fedsig.RELAY_TAG)[0].items()},
                                        body, now=1798761901, tag=fedsig.RELAY_TAG), fedsig.SigError))
Hr = fedsig.sign_response(priv, "k", 200, b"{}", "n" * 20, tag="saga-relay-resp/1")
ok("Antworten: tag waehlbar, Standard saga-fed-resp/1 nimmt sie nicht",
   fedsig.verify_response(pub, "k", 200, {k.lower(): v for k, v in Hr.items()}, b"{}", "n" * 20, tag="saga-relay-resp/1") is None
   and raises(lambda: fedsig.verify_response(pub, "k", 200, {k.lower(): v for k, v in Hr.items()}, b"{}", "n" * 20), fedsig.SigError))
ok("ungueltiger tag (Anfuehrungszeichen, leer, Grossbuchstaben) -> ValueError",
   raises(lambda: fedsig.sign_request(priv, "k", "GET", "a", "/", "", b"", tag='x"y')) and raises(lambda: fedsig.sign_request(priv, "k", "GET", "a", "/", "", b"", tag=""))
   and raises(lambda: fedsig.sign_request(priv, "k", "GET", "a", "/", "", b"", tag="Saga")))
# Besitznachweis beim Schluesselwechsel
ro = D["rotate"]
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(fedsig.load_public(ro["new_pub"])).verify(fedsig.unb64u(ro["proof"]), ro["signed"].encode())
    good = ro["signed"] == "saga-relay-rotate/1|" + ro["server_id"] + "|" + ro["new_pub"]
    k2 = Ed25519PrivateKey.from_private_bytes(fedsig.unb64u(ro["new_priv_seed"]))
    good = good and fedsig.b64u(k2.sign(ro["signed"].encode())) == ro["proof"] and fedsig.b64u(fedsig.public_of(fedsig.unb64u(ro["new_priv_seed"]))) == ro["new_pub"]
except Exception as ex:  # noqa: BLE001
    print("  ", type(ex).__name__, ex)
    good = False
ok("rotate: Nachweis saga-relay-rotate/1|<server_id>|<new_pub> mit dem neuen Schluessel", good)

# --- Vertragstext ------------------------------------------------------------------------------------------------------
with open(os.path.join(ROOT, "docs", "PUSH-RELAY.md"), encoding="utf-8") as f:
    doc = f.read()
ok("docs/PUSH-RELAY.md nennt Tag, Formate, Grenzen und Vektoren",
   all(x in doc for x in ("saga-relay/1", "saga-push-e2e/1", "E2E_MAX = 2400", "saga-collapse/1|", "saga-relay-rotate/1|",
                          "SAGA_PUSH_TITLE", "SAGA_PUSH_BODY", "SAGA_PUSH_CALL", "tests/vectors/push_e2e.json", "pushrelay1")))
names = set(re.findall(r"\b(?:[a-z0-9-]+\.)+(?:org|net|com|de|io|sh)\b", doc.lower()))
ok(f"Beispiele nur mit Platzhaltern ({sorted(names)})",
   "relay.example.org" in names and "org.example.saga" in doc and all(".example." in "." + n + "." for n in names))

n_ok = sum(res)
print(f"\n{n_ok}/{len(res)} OK", flush=True)
print("ALLES GRUEN" if n_ok == len(res) else "FEHLER", flush=True)
sys.exit(0 if n_ok == len(res) else 1)
