// Baut static/vendor/tiptap/ aus node_modules (vorher: npm ci --ignore-scripts).
// Aufruf ueber build.sh. Ausgabe deterministisch (gleiche Eingaben -> gleiche Bytes, siehe check-vendor-licenses.sh).
import * as esbuild from 'esbuild'
import fs from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const OUT = path.resolve(process.argv[2] || path.join(HERE, '../../static/vendor/tiptap'))
const NM = path.join(HERE, 'node_modules')
const lock = JSON.parse(fs.readFileSync(path.join(HERE, 'package-lock.json'), 'utf8'))
const pkgJson = (name) => JSON.parse(fs.readFileSync(path.join(NM, name, 'package.json'), 'utf8'))
const VERSION = pkgJson('@tiptap/core').version
const CORE_MAX_GZ = 180 * 1024

// Alle @tiptap/*-Pakete muessen dieselbe Version haben (ausser y-tiptap, eigener Versionszweig)
for (const [k, v] of Object.entries(lock.packages)) {
  const m = k.match(/^node_modules\/(@tiptap\/[^/]+)$/)
  if (m && m[1] !== '@tiptap/y-tiptap' && v.version !== VERSION) throw new Error(`${m[1]}@${v.version} != ${VERSION}`)
}

// Modulnamen im Live-Bundle, die auf window.SagaPM zeigen (nie eigene Kopie)
const PM_MODS = ['model', 'state', 'view', 'transform', 'commands', 'keymap', 'history', 'inputrules', 'schema-list', 'dropcursor', 'gapcursor']
const pmKey = (m) => (m === 'schema-list' ? 'schemaList' : m)
async function exportNames (spec) {
  const ns = await import(path.join(NM, spec === '@tiptap/core' ? '@tiptap/core/dist/index.js' : `prosemirror-${spec}/dist/index.js`))
  return Object.keys(ns).filter((n) => n !== 'default').sort()
}
const sagaPMGlobal = {
  name: 'sagapm-global',
  setup (b) {
    const re = new RegExp(`^(@tiptap/core|@tiptap/pm/(${PM_MODS.join('|')})|prosemirror-(${PM_MODS.join('|')}))$`)
    b.onResolve({ filter: re }, (a) => ({ path: a.path, namespace: 'sagapm' }))
    b.onResolve({ filter: /^(@tiptap\/pm\/|prosemirror-)/ }, (a) => { throw new Error(`Live-Bundle: ${a.path} nicht auf SagaPM abgebildet`) })
    b.onLoad({ filter: /.*/, namespace: 'sagapm' }, async (a) => {
      const mod = a.path === '@tiptap/core' ? '@tiptap/core' : a.path.replace(/^@tiptap\/pm\/|^prosemirror-/, '')
      const src = mod === '@tiptap/core' ? 'window.SagaPM.core' : `window.SagaPM.pm.${pmKey(mod)}`
      const names = await exportNames(mod)
      return {
        loader: 'js',
        contents: `if(!window.SagaPM)throw new Error("saga-y.js braucht saga-pm.js");const M=${src};\n` +
          names.map((n) => `export const ${n}=M.${n};`).join('\n')
      }
    })
  }
}
// Im Kern: y-tiptap/Collaboration (nur von DragHandle genutzt) durch Laufzeit-Weiterleitung ersetzen
const yShim = {
  name: 'y-shim',
  setup (b) {
    b.onResolve({ filter: /^(@tiptap\/y-tiptap|@tiptap\/extension-collaboration)$/ }, () => ({ path: path.join(HERE, 'y-shim.js') }))
    b.onResolve({ filter: /^(yjs|y-protocols|lib0)(\/|$)/ }, (a) => { throw new Error(`Kern-Bundle darf ${a.path} nicht enthalten`) })
  }
}

const common = {
  bundle: true, format: 'iife', minify: true, target: 'es2020', platform: 'browser', charset: 'utf8',
  legalComments: 'eof', metafile: true, write: false, absWorkingDir: HERE, logLevel: 'warning',
  define: { __SAGA_TIPTAP_VERSION__: JSON.stringify(VERSION), 'process.env.NODE_ENV': '"production"' }
}
const core = await esbuild.build({ ...common, entryPoints: ['entry.js'], globalName: 'SagaPM', plugins: [yShim] })
const live = await esbuild.build({ ...common, entryPoints: ['entry-y.js'], globalName: 'SagaY', plugins: [sagaPMGlobal] })

// CSS: dieselben Basis-Styles, die Tiptap sonst per injectCSS als <style> einhaengt
const coreSrc = fs.readFileSync(path.join(NM, '@tiptap/core/dist/index.js'), 'utf8')
const cssM = coreSrc.match(/\nconst style = `([\s\S]*?)`;\n/)
if (!cssM) throw new Error('Basis-CSS in @tiptap/core nicht gefunden - build.mjs an neue Version anpassen')
const css = `/* Tiptap/ProseMirror-Basis-Styles aus @tiptap/core@${VERSION} (sonst injectCSS) - erzeugt von tools/vendor-tiptap/build.sh, nicht von Hand aendern */\n${cssM[1]}\n`

// Gebuendelte Pakete aus den Metadaten (genau die, deren Code im Bundle steckt)
const used = new Map()
for (const [name, meta] of [['saga-pm.js', core.metafile], ['saga-y.js', live.metafile]]) {
  for (const inp of Object.keys(meta.inputs)) {
    const m = inp.match(/node_modules\/((?:@[^/]+\/)?[^/]+)\//)
    if (!m) continue
    if (!used.has(m[1])) used.set(m[1], new Set())
    used.get(m[1]).add(name)
  }
}
used.set('@tiptap/core', new Set([...(used.get('@tiptap/core') || []), 'saga-pm.css']))

fs.rmSync(OUT, { recursive: true, force: true })
fs.mkdirSync(path.join(OUT, 'licenses'), { recursive: true })
const jsOut = (r) => r.outputFiles[0].contents
fs.writeFileSync(path.join(OUT, 'saga-pm.js'), jsOut(core))
fs.writeFileSync(path.join(OUT, 'saga-y.js'), jsOut(live))
fs.writeFileSync(path.join(OUT, 'saga-pm.css'), css)

const lines = []
for (const name of [...used.keys()].sort()) {
  const ent = lock.packages[`node_modules/${name}`]
  if (!ent) throw new Error(`${name} nicht (flach) im package-lock`)
  const lic = pkgJson(name).license
  const files = fs.readdirSync(path.join(NM, name)).filter((f) => /^(licen[cs]e|copying)/i.test(f)).sort()
  if (!files.length) throw new Error(`${name}: keine Lizenzdatei`)
  const dest = name.replace(/^@/, '').replace('/', '__') + '.LICENSE'
  fs.writeFileSync(path.join(OUT, 'licenses', dest), files.map((f) => fs.readFileSync(path.join(NM, name, f), 'utf8')).join('\n'))
  lines.push(`${name}@${ent.version} (${lic}) -> ${[...used.get(name)].sort().join(', ')}`)
}
const gz = (buf) => zlib.gzipSync(buf, { level: 9 }).length
const sizes = [['saga-pm.js', jsOut(core)], ['saga-y.js', jsOut(live)], ['saga-pm.css', Buffer.from(css)]]
  .map(([n, b]) => `${n}: ${b.length} Bytes roh, ${gz(b)} Bytes gzip -9`)
fs.writeFileSync(path.join(OUT, 'VERSIONS.txt'),
  `Tiptap ${VERSION} + ProseMirror + Yjs - gebaut mit tools/vendor-tiptap/build.sh (esbuild ${pkgJson('esbuild').version}) aus\n` +
  `tools/vendor-tiptap/package-lock.json. Nicht von Hand aendern; Pruefung: tools/check-vendor-licenses.sh. Exportvertrag: README.\n` +
  `Paket@Version (Lizenz) -> enthalten in\n` + lines.join('\n') + '\n\nGroessen:\n' + sizes.join('\n') + '\n')
console.log(sizes.join('\n'))
if (gz(jsOut(core)) > CORE_MAX_GZ) { console.error(`FEHLER: saga-pm.js ueber ${CORE_MAX_GZ} Bytes gzip`); process.exit(3) }
