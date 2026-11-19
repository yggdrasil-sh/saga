"""OIDC-Testanbieter (Attrappe) fuer tests/sso_ldap.py und tests/avatare.py - verhaelt sich fuer Saga wie
mock-oauth2-server mit interactiveLogin (tools/stack-remote.sh): Issuer http://<host>:<port>/<name>, Formular mit
"username" (wird sub) und "claims" (JSON, kommt in ID-Token und userinfo), PKCE S256, RS256-Schluessel je Start.
Nur fuer Tests, nur auf 127.0.0.1 bzw. im Netz-Namensraum. Aufruf:
  python oidc_attrappe.py --host mockoidc --port 8080 --name default [--bind 127.0.0.1]"""
import argparse, base64, hashlib, html, json, secrets, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from authlib.jose import JsonWebKey, jwt

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="mockoidc")
ap.add_argument("--port", type=int, default=8080)
ap.add_argument("--name", default="default")
ap.add_argument("--bind", default="127.0.0.1")
A = ap.parse_args()
ISS = f"http://{A.host}:{A.port}/{A.name}"
KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True)
KID = secrets.token_hex(4)
PUB = dict(KEY.as_dict(is_private=False), kid=KID, use="sig", alg="RS256")
CODES, TOKENS, LOCK = {}, {}, threading.Lock()


def meta():
    p = ISS
    return {"issuer": p, "authorization_endpoint": p + "/authorize", "token_endpoint": p + "/token", "jwks_uri": p + "/jwks",
            "userinfo_endpoint": p + "/userinfo", "end_session_endpoint": p + "/endsession",
            "response_types_supported": ["code"], "subject_types_supported": ["public"], "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["plain", "S256"], "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
            "scopes_supported": ["openid", "profile", "email"]}


FORM = """<!doctype html><html><body><h1>Test-Anbieter</h1><form method="post">
<input name="username" placeholder="sub"><textarea name="claims">{}</textarea><button>Anmelden</button></form></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        print("oidc:", self.command, self.path.split("?")[0], fmt % a, flush=True)

    def send(self, code, body=b"", ctype="application/json", hdr=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (hdr or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def route(self):
        u = urllib.parse.urlparse(self.path)
        pre = "/" + A.name
        return (u.path[len(pre):] if u.path.startswith(pre) else None), dict(urllib.parse.parse_qsl(u.query))

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return dict(urllib.parse.parse_qsl(self.rfile.read(n).decode())) if n else {}

    def do_GET(self):
        p, q = self.route()
        if p == "/.well-known/openid-configuration":
            return self.send(200, meta())
        if p == "/jwks":
            return self.send(200, {"keys": [PUB]})
        if p == "/authorize":
            return self.send(200, FORM, "text/html; charset=utf-8")
        if p == "/userinfo":
            tok = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            with LOCK:
                c = TOKENS.get(tok)
            return self.send(200, c) if c else self.send(401, {"error": "invalid_token"})
        if p == "/endsession":
            back = q.get("post_logout_redirect_uri")
            return self.send(302, "", hdr={"Location": back}) if back else self.send(200, "abgemeldet", "text/plain")
        if self.path == "/healthz":
            return self.send(200, "ok", "text/plain")
        self.send(404, {"error": "not_found"})

    def do_POST(self):
        p, q = self.route()
        f = self.body()
        if p == "/authorize":
            if not q.get("redirect_uri") or q.get("response_type", "code") != "code":
                return self.send(400, {"error": "invalid_request"})
            try:
                claims = json.loads(f.get("claims") or "{}")
            except ValueError:
                return self.send(400, {"error": "claims"})
            code = secrets.token_urlsafe(24)
            with LOCK:
                CODES[code] = {"sub": f.get("username") or "user", "claims": claims, "q": q, "t": time.time()}
            back = q["redirect_uri"] + ("&" if "?" in q["redirect_uri"] else "?") + urllib.parse.urlencode(
                {"code": code, **({"state": q["state"]} if "state" in q else {})})
            return self.send(302, "", hdr={"Location": back})
        if p == "/token":
            cid = f.get("client_id")
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Basic "):
                cid = urllib.parse.unquote(base64.b64decode(auth[6:]).decode().split(":", 1)[0])
            with LOCK:
                c = CODES.pop(f.get("code") or "", None)
            if not c or f.get("grant_type") != "authorization_code" or time.time() - c["t"] > 120:
                return self.send(400, {"error": "invalid_grant"})
            oq = c["q"]
            if oq.get("redirect_uri") != f.get("redirect_uri") or (oq.get("client_id") and cid and oq["client_id"] != cid):
                return self.send(400, {"error": "invalid_grant", "error_description": "redirect_uri/client_id"})
            ch = oq.get("code_challenge")
            if ch:
                v = f.get("code_verifier") or ""
                got = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode() \
                    if oq.get("code_challenge_method", "plain") == "S256" else v
                if got != ch:
                    return self.send(400, {"error": "invalid_grant", "error_description": "pkce"})
            now = int(time.time())
            idc = {"iss": ISS, "sub": c["sub"], "aud": cid or oq.get("client_id"), "azp": cid or oq.get("client_id"), "iat": now,
                   "nbf": now, "exp": now + 3600, "jti": secrets.token_hex(8), "tid": A.name}
            if oq.get("nonce"):
                idc["nonce"] = oq["nonce"]
            idc.update(c["claims"])
            at = secrets.token_urlsafe(24)
            with LOCK:
                TOKENS[at] = dict(c["claims"], sub=c["sub"])
            idt = jwt.encode({"alg": "RS256", "kid": KID, "typ": "JWT"}, idc, KEY).decode()
            return self.send(200, {"access_token": at, "token_type": "Bearer", "expires_in": 3600, "id_token": idt,
                                   "scope": oq.get("scope", "openid")})
        self.send(404, {"error": "not_found"})


if __name__ == "__main__":
    s = ThreadingHTTPServer((A.bind, A.port), H)
    print(f"oidc-attrappe: {ISS} auf {A.bind}:{A.port}", flush=True)
    s.serve_forever()
