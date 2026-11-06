// Saga 2.5 - Chat-Panel (static/chatdock.js): Panel auf Seiten und Heute, "In Chat teilen" mit Vorschaukarte,
// schnell antworten (Hinweis und Glocke), Reiter "Marin | Chat", Seitenlink aus dem Panel ins Hauptfenster, Handy ohne Panel.
// SAGA_BASE, SAGA_USER/SAGA_PASS, SAGA_USER2/SAGA_PASS2 (zwei frische Konten; Chat-Schluessel richtet der Test ein).
// Der zweite Teil (schnell antworten) braucht eine bestehende Unterhaltung: A schreibt B zuerst an.
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const U1=process.env.SAGA_USER, U2=process.env.SAGA_USER2;
const fs=require('fs'); const E=process.env; const BASE=E.SAGA_BASE||'http://127.0.0.1:8099';
const RK=u=>require('os').tmpdir()+'/saga-browser-rk-'+BASE.replace(/[^a-z0-9]+/gi,'_')+'-'+u+'.txt';
async function login(br,u,opts={}){ const ctx=await br.newContext(Object.assign({viewport:{width:1440,height:880},locale:'de-DE'},opts));
  const p=await ctx.newPage(); p.on('pageerror',e=>console.log('[err '+u+']',e.message));
  await p.goto(BASE+'/login'); await p.fill('input[name=username]',u); await p.fill('input[name=password]',u===E.SAGA_USER2?E.SAGA_PASS2:E.SAGA_PASS);
  await Promise.all([p.waitForNavigation(),p.press('input[name=password]','Enter')]); return p; }
async function keys(p,u){ await p.waitForTimeout(1500);
  if (await p.$('.ct-setup-go')) { await p.click('.ct-setup-go'); await p.waitForSelector('#ct-rk'); const rk=(await p.textContent('#ct-rk')).trim(); fs.writeFileSync(RK(u),rk);
    await p.click('.hk-actions .btn-primary'); await p.fill('.ct-rk-last',rk.slice(-4)); await p.click('.ct-setup-done'); await p.waitForSelector('.ct-setup-done',{state:'detached',timeout:60000}); }
  else if (await p.$('.ct-rk-go')) { await p.click('.ct-rk-go'); await p.fill('.ct-rk-in',fs.readFileSync(RK(u),'utf8')); await p.click('.ct-rk-ok'); await p.waitForSelector('.ct-rk-ok',{state:'detached',timeout:60000}); }
  await p.waitForTimeout(600); }
async function send(p,text){ await p.fill('#ct-input',text); await p.press('#ct-input','Enter'); await p.waitForTimeout(700); }

const R=[]; const ok=(n,c,x)=>{R.push([n,!!c]); console.log((c?'OK  ':'FAIL')+' '+n+(c?'':' '+JSON.stringify(x||''))); };
(async()=>{ const br=await chromium.launch();
 const a=await login(br,U1,{colorScheme:'dark'}), b=await login(br,U2);
 await a.goto(BASE+'/chats'); await keys(a,U1); await b.goto(BASE+'/chats'); await keys(b,U2);
 await a.goto(BASE+'/chats#u'+U2); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200); await send(a,'Hallo!');
 await a.evaluate(()=>HK.api('POST','/api/notes',{title:'Projekt Saga 2.5',icon:'🚀'}));
 await a.goto(BASE+'/notes'); await a.waitForTimeout(2000);
 ok('Chat-Knopf unten rechts', await a.isVisible('.cd-btn'));
 await a.click('.cd-btn'); await a.waitForTimeout(2500);
 const fr = a.frameLocator('.cd-frame'); const f = a.frames().find(x=>/panel=1/.test(x.url()));
 ok('Panel offen, Chat-Ansicht geladen', await a.isVisible('#cd-panel') && !!f);
 // Schluessel im Panel (eigenes Konto, gleicher Browser -> schon da?)
 if (await f.$('.ct-rk-go')) { await f.click('.ct-rk-go'); await f.fill('.ct-rk-in',fs.readFileSync(RK(U1),'utf8')); await f.click('.ct-rk-ok'); await f.waitForSelector('.ct-rk-ok',{state:'detached',timeout:60000}); }
 await a.waitForTimeout(1000);
 ok('Panel: ohne Kopfzeile, Liste sichtbar', await f.isVisible('#ct-list') && !(await f.isVisible('.np-head')));
 ok('Panel: kein zweites Anruf-Skript', await f.evaluate(()=>!window.HKCalls));
 await f.click('#ct-list .ct-item-b'); await f.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);
 // Teilen ueber das Seitenmenue: erst eine Seite oeffnen
 await a.click('.cd-close'); await a.waitForTimeout(300);
 const pid = await a.evaluate(()=>HK.api('GET','/api/notes').then(d=>(Array.isArray(d)?d:d.notes).find(n=>n.title==='Projekt Saga 2.5').id));
 await a.evaluate(id=>HKPages.k.open(id), pid); await a.waitForTimeout(1500);
 await a.click('#pg-more'); await a.waitForTimeout(300);
 const item = a.locator('.menu .menu-item', {hasText:'In Chat teilen'});
 ok('Seitenmenue: "In Chat teilen"', await item.count()>0);
 await item.first().click(); await a.waitForTimeout(800);
 ok('Teilen-Hinweis im Panel', await f.isVisible('#ct-share'), await f.evaluate(()=>document.body.innerText.slice(0,200)));
 await f.click('#ct-list .ct-item-b'); await a.waitForTimeout(1500);
 const v = await f.inputValue('#ct-input');
 ok('Verweis in der Eingabe', v.includes('[[note:'+pid+']]'), v);
 await f.fill('#ct-input', v+'schau mal 🚀'); await f.press('#ct-input','Enter'); await a.waitForTimeout(2000);
 ok('Vorschaukarte unter der Nachricht', await f.isVisible('.ct-pcard'), await f.evaluate(()=>[...document.querySelectorAll('.ct-pcard')].map(x=>x.outerHTML.slice(0,200))));
 // Karte anklicken -> Seite im Hauptfenster
 await a.evaluate(()=>HKPages.k.open(HKPages.k.cur().id)); 
 // Schnell antworten: Panel zu, B schreibt
 await a.click('.cd-close'); await a.waitForTimeout(300);
 await b.goto(BASE+'/chats#u'+U1); await b.waitForSelector('#ct-chat:not([hidden])'); await b.waitForTimeout(1500);
 await send(b,'Cool, schau ich mir gleich an!');
 await a.waitForTimeout(3000);
 const toast = await a.evaluate(()=>{const t=document.querySelector('.hk-toast, .toast'); return t? t.innerText : null;});
 ok('Hinweis mit "Antworten"', toast && /Antworten/.test(toast), toast);
 ok('Ungelesen am Chat-Knopf', await a.isVisible('.cd-badge'), await a.textContent('.cd-badge'));
 if (toast) { await a.locator('.hk-toast button, .toast button', {hasText:'Antworten'}).first().click(); await a.waitForTimeout(1500);
   ok('Antworten oeffnet den Chat im Panel', await a.isVisible('#cd-panel') && await f.isVisible('#ct-chat:not([hidden])'));
   ok('... Eingabe hat den Fokus', await f.evaluate(()=>document.activeElement && document.activeElement.id==='ct-input'));
   await f.fill('#ct-input','Super, danke! 😊'); await f.press('#ct-input','Enter'); await a.waitForTimeout(1500);
   }
 // Glocke: Eintrag mit Antworten
 await a.click('.cd-close');
 await send(b,'Noch was: morgen 10 Uhr?'); await a.waitForTimeout(2500);
 const bell = await a.$('#hk-bell, .lv-bell'); if (bell) { await bell.click(); await a.waitForTimeout(800);
   ok('Glocke: "Antworten" an Chat-Eintrag', await a.isVisible('.lv-reply')); ok('Glocke: Liste ganz im Fenster', await a.evaluate(()=>{const r=document.querySelector('.lv-panel, #lv-panel, [class*=lv-pan]').getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth;}));
   await a.locator('.lv-reply').first().click(); await a.waitForTimeout(1500);
   ok('Glocke: Antworten oeffnet den Chat im Panel', await a.isVisible('#cd-panel') && await f.isVisible('#ct-chat:not([hidden])'));
   // Reiter Marin | Chat (KI-Status hier nachgestellt)
   await a.evaluate(()=>{ window.HKAI={state:()=>({persona:{name:'Marin'}}),open:()=>{}}; document.querySelector('.cd-close').click(); });
   await a.click('.cd-btn'); await a.waitForTimeout(800);
   ok('Reiter Marin | Chat im Panel', (await a.textContent('.cd-head')).includes('Marin'));
   await a.click('.cd-close'); }
 // Dashboard
 await a.goto(BASE+'/dashboard'); await a.waitForTimeout(2000);
 ok('Dashboard: Chat-Knopf', await a.isVisible('.cd-btn'));
 await a.click('.cd-btn'); await a.waitForTimeout(2500); // Panel: Seitenlink oeffnet im Hauptfenster
 const f2 = a.frames().find(x=>/panel=1/.test(x.url())); await f2.waitForSelector('#ct-list .ct-item-b'); await f2.click('#ct-list .ct-item-b'); await f2.waitForSelector('.ct-pcard',{timeout:8000}).catch(()=>{});
 if (await f2.$('.ct-pcard')) { await f2.click('.ct-pcard'); await a.waitForTimeout(2500); ok('Karte oeffnet die Seite im Hauptfenster', /\/notes#\d+/.test(a.url()), a.url()); }
 else ok('Karte im Dashboard-Panel', false);
 // Handy: kein Panel
 await a.setViewportSize({width:400,height:800}); await a.waitForTimeout(300);
 ok('Handy: Knopf aus', !(await a.isVisible('.cd-btn')));
 console.log(R.filter(x=>x[1]).length+'/'+R.length);
 await br.close(); process.exit(R.every(x=>x[1])?0:1); })();
