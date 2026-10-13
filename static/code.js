// Code-Block mit Sprache + Syntax-Farben (ersetzt @editorjs/code). Eigener kleiner Hervorheber:
// keine Fremdbibliothek, nur CSS-Klassen (die Seite erlaubt keine Inline-Styles).
(function () {
  'use strict';
  var LANGS = [['', T('Text')], ['bash', 'Bash / Shell'], ['yaml', 'YAML'], ['python', 'Python'], ['javascript', 'JavaScript'],
    ['typescript', 'TypeScript'], ['json', 'JSON'], ['haskell', 'Haskell'], ['sql', 'SQL'], ['dockerfile', 'Dockerfile'],
    ['ini', 'INI / TOML / conf'], ['nginx', 'Nginx'], ['xml', 'HTML / XML'], ['powershell', 'PowerShell'], ['go', 'Go'], ['rust', 'Rust'], ['css', 'CSS']];
  var ALIAS = { sh: 'bash', shell: 'bash', zsh: 'bash', 'plain text': '', plain: '', text: '', yml: 'yaml', py: 'python', js: 'javascript',
    ts: 'typescript', html: 'xml', toml: 'ini', conf: 'ini', docker: 'dockerfile', ps1: 'powershell', rs: 'rust', golang: 'go' };
  function norm(l) { l = String(l || '').toLowerCase().trim(); if (l in ALIAS) return ALIAS[l]; return LANGS.some(function (x) { return x[0] === l; }) ? l : ''; }

  function words(s) { return new RegExp('\\b(?:' + s.split(' ').join('|') + ')\\b', 'y'); }
  var STR2 = /"(?:\\.|[^"\\\n])*"?/y, STR1 = /'(?:\\.|[^'\\\n])*'?/y, NUM = /\b(?:0x[\da-f]+|\d+(?:\.\d+)?)\b/iy;
  var R = {
    bash: [['c', /#(?![{])[^\n]*/y], ['s', STR2], ['s', STR1], ['v', /\$\{[^}\n]*\}|\$[A-Za-z_]\w*|\$[0-9@#?$!*-]/y],
      ['k', words('if then else elif fi for in do done case esac while until function return local export readonly declare select time')],
      ['f', words('echo printf cd ls cat grep egrep sed awk find xargs sudo apt apt-get dnf yum pacman systemctl journalctl docker podman kubectl ssh scp rsync curl wget tar gzip chmod chown mkdir rm cp mv ln git pct qm make sort uniq head tail tee cut tr wc ip ping nano vim source exit set unset')],
      ['o', /(?:^|(?<=\s))--?[A-Za-z][\w-]*/y], ['n', NUM]],
    yaml: [['c', /#[^\n]*/y], ['p', /[\w.\-/]+(?=\s*:(?:\s|$))/y], ['s', STR2], ['s', STR1], ['v', /[&*][\w-]+/y],
      ['k', words('true false yes no null on off')], ['n', NUM]],
    python: [['c', /#[^\n]*/y], ['s', /(?:[rbfu]{0,2})(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$))/iy], ['s', STR2], ['s', STR1], ['v', /@\w+/y],
      ['k', words('def class return if elif else for while in import from as with try except finally raise lambda None True False and or not pass break continue yield async await global nonlocal assert del is')],
      ['f', words('print len range open str int float dict list set tuple isinstance enumerate zip map filter sorted sum min max any all super self')], ['n', NUM]],
    javascript: [['c', /\/\/[^\n]*|\/\*[\s\S]*?(?:\*\/|$)/y], ['s', /`(?:\\.|[^`\\])*`?/y], ['s', STR2], ['s', STR1],
      ['k', words('var let const function return if else for while do switch case break continue new this class extends import export from default try catch finally throw typeof instanceof in of async await null undefined true false yield')],
      ['f', words('console document window Math JSON Promise Object Array String Number fetch require module')], ['n', NUM]],
    json: [['p', /"(?:\\.|[^"\\\n])*"(?=\s*:)/y], ['s', STR2], ['k', words('true false null')], ['n', /-?\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b/iy]],
    haskell: [['c', /--[^\n]*|\{-[\s\S]*?(?:-\}|$)/y], ['s', STR2], ['s', /'(?:\\.|[^'\\\n])'/y],
      ['k', words('module import qualified as hiding where let in case of data type newtype class instance deriving do if then else infixl infixr forall')],
      ['t', /\b[A-Z][\w']*/y], ['n', NUM]],
    sql: [['c', /--[^\n]*|\/\*[\s\S]*?(?:\*\/|$)/y], ['s', STR1], ['s', STR2],
      ['k', new RegExp('\\b(?:select|from|where|and|or|not|insert|into|values|update|set|delete|create|table|index|drop|alter|add|join|left|right|inner|outer|on|group|by|order|asc|desc|limit|offset|having|as|distinct|null|is|in|like|primary|key|references|default|union|all|exists|case|when|then|else|end|begin|commit|rollback)\\b', 'iy')],
      ['n', NUM]],
    dockerfile: [['c', /#[^\n]*/y], ['k', /\b(?:FROM|RUN|CMD|COPY|ADD|ENV|EXPOSE|WORKDIR|ENTRYPOINT|ARG|VOLUME|USER|LABEL|HEALTHCHECK|SHELL|ONBUILD|STOPSIGNAL|AS)\b/y],
      ['s', STR2], ['s', STR1], ['v', /\$\{[^}\n]*\}|\$[A-Za-z_]\w*/y], ['n', NUM]],
    ini: [['c', /[#;][^\n]*/y], ['t', /\[[^\]\n]*\]/y], ['p', /[\w.\-]+(?=\s*=)/y], ['s', STR2], ['s', STR1], ['k', words('true false yes no on off')], ['n', NUM]],
    nginx: [['c', /#[^\n]*/y], ['s', STR2], ['s', STR1], ['v', /\$\w+/y],
      ['k', words('server location listen server_name root index return rewrite proxy_pass proxy_set_header include upstream http events ssl_certificate ssl_certificate_key add_header try_files if set client_max_body_size')], ['n', NUM]],
    xml: [['c', /<!--[\s\S]*?(?:-->|$)/y], ['k', /<\/?[\w:.-]+|\/?>/y], ['p', /[\w:.-]+(?==)/y], ['s', STR2], ['s', STR1]],
    powershell: [['c', /#[^\n]*/y], ['s', STR2], ['s', STR1], ['v', /\$[\w:]+/y], ['f', /\b[A-Z][a-z]+-[A-Z]\w+/y],
      ['k', new RegExp('\\b(?:function|param|if|else|elseif|foreach|for|while|switch|return|try|catch|finally|throw|in)\\b', 'iy')], ['o', /-[A-Za-z]\w*/y], ['n', NUM]],
    go: [['c', /\/\/[^\n]*|\/\*[\s\S]*?(?:\*\/|$)/y], ['s', /`[^`]*`?/y], ['s', STR2], ['s', STR1],
      ['k', words('package import func return if else for range switch case default go defer chan map struct interface type var const nil true false break continue select')], ['n', NUM]],
    rust: [['c', /\/\/[^\n]*|\/\*[\s\S]*?(?:\*\/|$)/y], ['s', STR2], ['k', words('fn let mut pub use mod struct enum impl trait match if else for while loop return self Self crate super as in ref move async await where true false')],
      ['t', /\b[A-Z]\w*/y], ['v', /\b\w+!/y], ['n', NUM]],
    css: [['c', /\/\*[\s\S]*?(?:\*\/|$)/y], ['p', /[\w-]+(?=\s*:)/y], ['s', STR2], ['s', STR1], ['v', /#[\da-f]{3,8}\b/iy], ['n', /-?\d+(?:\.\d+)?(?:px|rem|em|%|vh|vw|s|ms)?/y]]
  };
  R.typescript = R.javascript;
  function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function highlight(code, lang) {
    var rules = R[lang]; if (!rules) return esc(code);
    var out = '', i = 0, plain = '', n = code.length;
    while (i < n) {
      var hit = null;
      for (var r = 0; r < rules.length; r++) { var re = rules[r][1]; re.lastIndex = i; var m = re.exec(code); if (m && m[0].length) { hit = [rules[r][0], m[0]]; break; } }
      if (hit) { out += esc(plain) + '<span class="tk-' + hit[0] + '">' + esc(hit[1]) + '</span>'; plain = ''; i += hit[1].length; }
      else {                                   // bis zum naechsten Wortanfang ungefaerbt (Woerter nicht mitten drin treffen)
        var ch = code[i]; plain += ch; i++;
        if (/\w/.test(ch)) while (i < n && /\w/.test(code[i])) { plain += code[i]; i++; }
      }
    }
    return out + esc(plain);
  }

  // --- Editor.js-Werkzeug ---------------------------------------------------------------------
  function HKCode(o) {
    var d = o.data || {};
    this.api = o.api; this.block = o.block; this.ro = !!o.readOnly; this.code = d.code || ''; this.lang = norm(d.language);
  }
  HKCode.toolbox = { title: T('Code'), icon: '<svg width="17" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 7l-5 5 5 5M16 7l5 5-5 5"/></svg>' };
  HKCode.enableLineBreaks = true;
  HKCode.isReadOnlySupported = true;
  HKCode.conversionConfig = { export: 'code', import: 'code' };
  HKCode.pasteConfig = { tags: ['PRE'] };
  HKCode.sanitize = { code: true, language: false };
  HKCode.prototype.render = function () {
    var self = this, el = HK.el, w = el('div', 'hk-code'), head = el('div', 'hk-code-head');
    var sel = el('select', 'hk-code-lang'); sel.setAttribute('aria-label', T('Programmiersprache')); sel.disabled = this.ro;
    LANGS.forEach(function (x) { var o = el('option', null, x[1]); o.value = x[0]; sel.appendChild(o); });
    sel.value = this.lang;
    sel.addEventListener('change', function () { self.lang = sel.value; self.paint(); self.changed(); });
    var cp = el('button', 'hk-code-copy', T('Kopieren')); cp.type = 'button';
    cp.addEventListener('click', function () {
      if (navigator.clipboard) navigator.clipboard.writeText(self.ta.value).then(function () { HK.toast(T('Code kopiert')); });
    });
    head.appendChild(sel); head.appendChild(cp);
    var area = el('div', 'hk-code-area'), pre = el('pre', 'hk-code-pre');
    this.out = el('code'); pre.appendChild(this.out); pre.setAttribute('aria-hidden', 'true');
    var ta = el('textarea', 'hk-code-ta'); ta.spellcheck = false; ta.value = this.code; ta.placeholder = T('Code'); ta.readOnly = this.ro;
    ta.setAttribute('autocapitalize', 'off'); ta.setAttribute('autocomplete', 'off'); ta.setAttribute('aria-label', T('Code'));
    ta.addEventListener('input', function () { self.paint(); self.changed(); });
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Tab' && !e.metaKey && !e.ctrlKey) {               // Tab = 2 Leerzeichen (statt aus dem Block springen)
        e.preventDefault(); e.stopPropagation();
        document.execCommand('insertText', false, '  ');
      }
    });
    this.ta = ta; area.appendChild(pre); area.appendChild(ta);
    w.appendChild(head); w.appendChild(area); this.w = w; this.paint();
    return w;
  };
  HKCode.prototype.paint = function () {
    this.out.innerHTML = highlight(this.ta.value, this.lang) + '\n';      // Zeilenumbruch am Ende: Hoehe stimmt auch bei leerer letzter Zeile
    this.w.dataset.lang = this.lang;
  };
  HKCode.prototype.changed = function () { if (this.block && this.block.dispatchChange) this.block.dispatchChange(); };   // Textfeld-Aenderungen meldet Editor.js nicht selbst
  HKCode.prototype.onPaste = function (e) { var t = e.detail.data.textContent || ''; this.ta.value = t; this.paint(); this.changed(); };
  HKCode.prototype.save = function () { var d = { code: this.ta.value }; if (this.lang) d.language = this.lang; return d; };

  window.HKCode = HKCode;
  window.HKCodeHL = { highlight: highlight, norm: norm, langs: LANGS };
})();
