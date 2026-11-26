// MCP mit OAuth (2.6, Bugs-DB 2264) mit dem offiziellen MCP-Client (@modelcontextprotocol/sdk): Entdeckung (401 ->
// resource_metadata -> RFC 9728/8414), dynamische Registrierung, PKCE, Zustimmung (Anmeldung per Formular wie ein Browser),
// Token, initialize + tools/list + tools/call, Zugriffstoken widerrufen -> der Client holt per Refresh-Token selbst ein
// neues (Rotation) und ruft weiter auf.
// Braucht das SDK ausserhalb des Repos: OA_SDK=<Ordner mit node_modules/@modelcontextprotocol/sdk> (npm install
// @modelcontextprotocol/sdk). Konto: um-anna mit UMG_PW (tools/testumgebung/konten.py). Aufruf in der Testumgebung:
//   OA_SDK=/pfad UMG_SENV=... tools/testumgebung/umgebung.sh lauf server <slot> mcp_oauth_sdk.js
import { createRequire } from 'node:module';
import path from 'node:path';

const SDK = process.env.OA_SDK;
const BASE = (process.env.BASE_URL || 'http://127.0.0.1:8080').replace(/\/$/, '');
const USER = process.env.OA_USER || 'um-anna', PW = process.env.UMG_PW;
const res = [];
function ok(n, c, info) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c || info === undefined ? '' : ' -> ' + String(info).slice(0, 400))); }

const req = createRequire(path.join(SDK, 'package.json'));
const imp = (p) => import(req.resolve('@modelcontextprotocol/sdk/' + p));
const { Client } = await imp('client/index.js');
const { StreamableHTTPClientTransport } = await imp('client/streamableHttp.js');
const { UnauthorizedError } = await imp('client/auth.js');

let cookie = '';
function take(r) {
  const sc = r.headers.getSetCookie ? r.headers.getSetCookie() : [];
  for (const c of sc) if (c.startsWith('saga=')) cookie = c.split(';')[0];
}
async function get(u, init = {}) {
  const r = await fetch(new URL(u, BASE), { redirect: 'manual', ...init, headers: { ...(init.headers || {}), ...(cookie ? { Cookie: cookie } : {}) } });
  take(r);
  return r;
}
const form = (o) => new URLSearchParams(Object.entries(o).flatMap(([k, v]) => (Array.isArray(v) ? v.map((x) => [k, x]) : [[k, v]])));

// "Browser": Autorisierungs-Adresse -> Anmeldung -> Zustimmung -> Code an die redirect_uri
let gotCode = null, gotState = null, authUrl = null, seenScopes = [], confirmStep = false;
async function browser(url) {
  authUrl = url;
  let r = await get(url.toString());
  let loc = r.headers.get('location') || '';
  if (loc.startsWith('/login')) {
    let p = await (await get(loc)).text();
    const csrf = (p.match(/name="csrf" value="([^"]+)"/) || [])[1];
    r = await get('/login', { method: 'POST', body: form({ csrf, username: USER, password: PW }), headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
    loc = r.headers.get('location') || '';
  }
  let page = await (await get(loc)).text();
  let csrf = (page.match(/name="csrf" value="([^"]+)"/) || [])[1];
  const rid = (loc.match(/r=([\w-]+)/) || [])[1];
  // frische Bestaetigung wie beim Anlegen eines sga_-Tokens: erst Passwort, dann die Rechte
  confirmStep = page.includes('name="secret"') && !page.includes('name="scope" value="read:pages"');
  if (confirmStep) {
    r = await get('/oauth/authorize', { method: 'POST', body: form({ csrf, r: rid, action: 'confirm', secret: PW }),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', Origin: BASE, 'Sec-Fetch-Site': 'same-origin' } });
    page = await (await get(r.headers.get('location') || loc)).text();
    csrf = (page.match(/name="csrf" value="([^"]+)"/) || [])[1];
  }
  seenScopes = [...page.matchAll(/name="scope" value="([^"]+)" checked/g)].map((m) => m[1]);
  r = await get('/oauth/authorize', { method: 'POST', body: form({ csrf, r: rid, action: 'allow', scope: ['mcp', ...seenScopes] }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', Origin: BASE, 'Sec-Fetch-Site': 'same-origin' } });
  const back = new URL(r.headers.get('location') || 'http://x/');
  gotCode = back.searchParams.get('code'); gotState = back.searchParams.get('state');
}

const store = {};
const provider = {
  get redirectUrl() { return 'http://127.0.0.1:9/oauth/callback'; },
  get clientMetadata() {
    return { client_name: 'MCP SDK Test', redirect_uris: ['http://127.0.0.1:9/oauth/callback'], grant_types: ['authorization_code', 'refresh_token'],
      response_types: ['code'], token_endpoint_auth_method: 'none' };
  },
  state() { return store.state = 'sdk-' + Math.random().toString(36).slice(2); },
  clientInformation() { return store.client; },
  saveClientInformation(c) { store.client = c; },
  tokens() { return store.tokens; },
  saveTokens(t) { store.saves = (store.saves || 0) + 1; store.tokens = t; },
  redirectToAuthorization(u) { if (!store.redirect) store.redirect = browser(u); else store.again = String(u); },
  saveCodeVerifier(v) { store.verifier = v; },
  codeVerifier() { return store.verifier; },
};

const T0 = Date.now();
let client = new Client({ name: 'saga-oauth-sdk-test', version: '1.0.0' });
let transport = new StreamableHTTPClientTransport(new URL(BASE + '/mcp'), { authProvider: provider });
let unauthorized = false;
try { await client.connect(transport); } catch (e) { unauthorized = e instanceof UnauthorizedError || /nauthorized/.test(String(e)); if (!unauthorized) console.log('connect:', e); }
ok('SDK: erster Verbindungsversuch -> Autorisierung noetig', unauthorized);
ok('SDK: dynamisch registriert (client_id sgc_…)', /^sgc_/.test(store.client?.client_id || ''), JSON.stringify(store.client));
await store.redirect;
ok('SDK: Autorisierungs-Adresse mit PKCE S256, resource und state', authUrl && authUrl.searchParams.get('code_challenge_method') === 'S256'
  && authUrl.searchParams.get('resource') === BASE + '/mcp' && authUrl.searchParams.get('state') === store.state, authUrl);
ok('SDK-Ablauf: erst Bestaetigen (Passwort), dann Zustimmung', confirmStep);
ok('SDK: Zustimmung -> Code mit state zurueck', /^sgk_/.test(gotCode || '') && gotState === store.state, gotCode);
ok('SDK: Zustimmungsseite bietet nie tel', seenScopes.length > 0 && !seenScopes.some((s) => s.startsWith('tel')), seenScopes);
await transport.finishAuth(gotCode);
ok('SDK: Token gespeichert (Zugriff + Refresh)', /^sgo_/.test(store.tokens?.access_token || '') && /^sgr_/.test(store.tokens?.refresh_token || ''), JSON.stringify(store.tokens));
client = new Client({ name: 'saga-oauth-sdk-test', version: '1.0.0' });
transport = new StreamableHTTPClientTransport(new URL(BASE + '/mcp'), { authProvider: provider });
await client.connect(transport);
ok('SDK: initialize', client.getServerVersion()?.name === 'saga', JSON.stringify(client.getServerVersion()));
let tl = await client.listTools();
ok(`SDK: tools/list (${tl.tools.length} Werkzeuge)`, tl.tools.some((t) => t.name === 'search_pages') && tl.tools.some((t) => t.name === 'create_page'));
let r = await client.callTool({ name: 'create_page', arguments: { title: 'Per MCP-SDK mit OAuth', markdown: 'Hallo' } });
ok('SDK: tools/call create_page', r.isError === false, JSON.stringify(r).slice(0, 300));
r = await client.callTool({ name: 'search_pages', arguments: { query: 'MCP-SDK' } });
ok('SDK: tools/call search_pages findet die Seite', r.isError === false && JSON.stringify(r).includes('Per MCP-SDK mit OAuth'), JSON.stringify(r).slice(0, 300));
// Zugriffstoken widerrufen: der Client bekommt 401 und holt sich per Refresh-Token selbst ein neues
const oldAcc = store.tokens.access_token, oldRef = store.tokens.refresh_token, saves = store.saves;
const rv = await fetch(BASE + '/oauth/revoke', { method: 'POST', body: form({ token: oldAcc, client_id: store.client.client_id }),
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
ok('Widerruf des Zugriffstokens -> 200', rv.status === 200);
let again = null;
try { again = await client.listTools(); } catch (e) { console.log('nach Widerruf:', String(e).slice(0, 300)); }
ok('SDK: nach 401 automatisch per Refresh erneuert, tools/list geht weiter', again && again.tools.length === tl.tools.length
  && store.saves > saves && store.tokens.access_token !== oldAcc && store.tokens.refresh_token !== oldRef, JSON.stringify(store.tokens));
// alter Refresh-Token noch einmal -> Kette gesperrt; der Client kann ohne neue Zustimmung nicht weiter
const tr = await fetch(BASE + '/oauth/token', { method: 'POST', body: form({ grant_type: 'refresh_token', refresh_token: oldRef, client_id: store.client.client_id }),
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
ok('Alter Refresh-Token (Wiederverwendung) -> 400 invalid_grant', tr.status === 400 && (await tr.json()).error === 'invalid_grant');
let blocked = false;
let why = 'ging durch';
try { await client.listTools(); } catch (e) { blocked = true; why = String(e).slice(0, 200); }
ok('SDK: danach gesperrt (Refresh -> invalid_grant, neue Zustimmung noetig)', blocked && /InvalidGrant|invalid_grant/.test(why), why);
await client.close().catch(() => {});
console.log(`\nMCP-OAuth-SDK ${res.filter(Boolean).length}/${res.length} (${Math.round((Date.now() - T0) / 1000)} s)`, res.length && res.every(Boolean) ? '- ALLES GRUEN' : '- FEHLER');
process.exit(res.length && res.every(Boolean) ? 0 : 1);
