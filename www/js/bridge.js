/*
 * Energipilot – broen mellem panelet og Python-kernen.
 *
 * 1. Starter en baggrundstråd (js/worker.js) med Pyodide (Python), numpy og scikit-learn, så panelet ikke fryser.
 * 2. Giver den Energy Pilots Python-kode (www/py), som starter energy_pilot.main() med den virtuelle Home Assistant.
 * 3. Sender panelets kald til "api/..." til web.py i Python – i stedet for over netværket.
 * 4. Udfører UDP og kald til andre servere for kernen (appens native plugin og native HTTP).
 *
 * Panelet (index.html) er det samme som i Home Assistant-appen.
 */
(function () {
  'use strict';

  // Nogle visninger (fx en forhåndsvisning) giver ikke adgang til localStorage – panelet bruger det til små ting.
  try { window.localStorage.getItem('ep_probe'); } catch (e) {
    const mem = {};
    try {
      Object.defineProperty(window, 'localStorage', {
        configurable: true,
        value: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; }, clear: () => { for (const k in mem) delete mem[k]; } },
      });
    } catch (e2) { /* lad være */ }
  }

  const PYODIDE_VERSIONS = ['v314.0.8', 'v0.29.0', 'v0.28.3', 'v0.28.0', 'v0.27.7'];
  // Forbindelser gemmes på enheden (skærmen "Forbindelser", knappen 🔌 øverst til venstre).
  function loadConn() { try { return JSON.parse(localStorage.getItem('ep_app_conn') || '{}'); } catch (e) { return {}; } }
  function saveConn(c) { try { localStorage.setItem('ep_app_conn', JSON.stringify(c)); } catch (e) { /* */ } }
  const CONN = loadConn();
  const MODE = (new URLSearchParams(location.search).get('mode')) || CONN.mode || 'demo';
  const NATIVE = !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());

  // UDP (KEBA) går gennem appens eget native plugin; en browser kan ikke sende UDP.
  window.EPNative = {
    udp: NATIVE && !!(window.Capacitor.Plugins && window.Capacitor.Plugins.EpUdp),
    udpRequest: async function (host, port, message, timeoutMs, localPort, expectReply) {
      const r = await window.Capacitor.Plugins.EpUdp.request({ host, port, message, timeoutMs, localPort, expectReply });
      return r && r.reply != null ? r.reply : null;
    },
  };
  // Udvikling: ?dev=1 springer Pyodide over og bruger tools/dev_server.py (samme kerne i almindelig Python).
  const DEV = new URLSearchParams(location.search).get('dev') === '1';
  const realFetch = window.fetch.bind(window);
  let resolveReady, rejectReady;
  const ready = new Promise((res, rej) => { resolveReady = res; rejectReady = rej; });

  // ---------- Indlæsningsskærm ----------
  const overlay = document.createElement('div');
  overlay.id = 'ep-boot';
  overlay.innerHTML =
    '<div class="ep-boot-box"><div class="ep-boot-logo">⚡</div><div class="ep-boot-title">Energipilot</div>' +
    '<div class="ep-boot-msg" id="ep-boot-msg">Starter…</div><div class="ep-boot-bar"><div id="ep-boot-fill"></div></div>' +
    '<div class="ep-boot-sub">Første gang hentes Python og læringsmodellerne (ca. 30 MB). Derefter går det hurtigt.</div></div>';
  const style = document.createElement('style');
  style.textContent =
    '#ep-boot{position:fixed;inset:0;z-index:9999;background:#06080d;color:#f3f5f9;display:flex;align-items:center;justify-content:center;font:15px -apple-system,system-ui,sans-serif;padding:24px}' +
    '.ep-boot-box{max-width:320px;text-align:center}.ep-boot-logo{font-size:44px}.ep-boot-title{font-size:22px;font-weight:700;margin:6px 0 18px}' +
    '.ep-boot-msg{color:#c9cfdb;min-height:20px}.ep-boot-bar{height:6px;background:#131924;border-radius:4px;margin:14px 0;overflow:hidden}' +
    '#ep-boot-fill{height:100%;width:3%;background:#7c6cff;transition:width .4s}.ep-boot-sub{color:#5b6372;font-size:12px}' +
    '.ep-boot-err{color:#ff5d6c;white-space:pre-wrap;text-align:left;font-size:12px;margin-top:12px;max-height:40vh;overflow:auto}' +
    '#ep-demo{position:fixed;left:50%;transform:translateX(-50%);top:calc(env(safe-area-inset-top) + 4px);z-index:50;background:rgba(124,108,255,.18);color:#c8c0ff;' +
    'font:600 11px -apple-system,system-ui,sans-serif;padding:3px 10px;border-radius:999px;pointer-events:none;letter-spacing:.02em}';
  function mount() {
    document.head.appendChild(style);
    document.body.appendChild(overlay);
  }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);

  function step(msg, pct) {
    const m = document.getElementById('ep-boot-msg');
    const f = document.getElementById('ep-boot-fill');
    if (m) m.textContent = msg;
    if (f && pct != null) f.style.width = pct + '%';
  }

  function fail(err) {
    step('Kunne ikke starte', 100);
    const box = overlay.querySelector('.ep-boot-box');
    const pre = document.createElement('div');
    pre.className = 'ep-boot-err';
    pre.textContent = String(err && (err.stack || err.message) || err);
    box.appendChild(pre);
    rejectReady(err);
  }

  // ---------- Python-kernen i en baggrundstråd (js/worker.js) ----------
  let worker = null;
  let callSeq = 0;
  const calls = new Map();

  /** Et kald til kernen: 'api' (panelets kald), 'info' (status) eller 'save'. */
  function core(kind, args) {
    return new Promise((resolve, reject) => {
      if (!worker) { reject(new Error('kernen kører ikke')); return; }
      const id = ++callSeq;
      calls.set(id, { resolve, reject });
      worker.postMessage({ t: 'call', id, kind, args: args || {} });
    });
  }
  function saveCore() { if (worker) core('save').catch(() => {}); }

  async function pyodideBases() {
    // Den native app har Pyodide med i pakken (www/pyodide/); ellers hentes den fra jsDelivr.
    const bases = [];
    const head = await realFetch('pyodide/pyodide.js', { method: 'HEAD' }).catch(() => null);
    if (head && head.ok) bases.push(new URL('pyodide/', location.href).href);
    return bases.concat(PYODIDE_VERSIONS.map((v) => 'https://cdn.jsdelivr.net/pyodide/' + v + '/full/'));
  }

  async function makeWorker() {
    if (!window.EP_OTA) return new Worker('js/worker.js');
    // En version appen selv har hentet: baggrundstrådens kode ligger gemt sammen med den.
    let src = null;
    try { src = localStorage.getItem('ep_ota_worker'); } catch (e) { /* */ }
    if (!src) {
      const r = await realFetch(UPDATE_BASE + 'js/worker.js?v=' + window.EP_OTA.id, { cache: 'no-store' });
      if (!r.ok) throw new Error('worker.js');
      src = await r.text();
      try { localStorage.setItem('ep_ota_worker', src); } catch (e) { /* */ }
    }
    return new Worker(URL.createObjectURL(new Blob([src], { type: 'text/javascript' })));
  }

  // Det kernen beder siden om: UDP (appens native plugin) og kald til andre servere (native HTTP i appen).
  async function answer(kind, a) {
    if (kind === 'udp') return await window.EPNative.udpRequest(a.host, a.port, a.message, a.timeoutMs, a.localPort, a.expectReply);
    if (kind === 'fetch') {
      const init = { method: a.method, headers: a.headers };
      if (a.body != null) init.body = a.body;
      const r = await realFetch(a.url, init);
      const headers = {};
      r.headers.forEach((v, k) => { headers[k] = v; });
      return { status: r.status, headers, body: await r.arrayBuffer() };
    }
    throw new Error('ukendt ' + kind);
  }

  async function start() {
    step('Starter…', 4);
    let bundle = null;
    if (window.EP_OTA) {  // a newer version the app fetched itself (see "Automatisk opdatering" below)
      try { bundle = localStorage.getItem('ep_ota_bundle'); } catch (e) { bundle = null; }
    }
    if (!bundle) bundle = await (await realFetch('py/bundle.json')).text();
    try {
      worker = await makeWorker();
    } catch (e) {
      if (window.EP_OTA) { location.replace('app.html'); return; }  // back to the version the app came with
      throw e;
    }
    const cfg = Object.assign({ language: (navigator.language || 'da').slice(0, 2) }, MODE === 'home' ? CONN : {});
    await new Promise((resolve, reject) => {
      worker.onerror = (e) => reject(new Error(e.message || 'baggrundstråden kunne ikke starte'));
      worker.onmessage = (e) => {
        const m = e.data || {};
        if (m.t === 'progress') step(m.msg, m.pct);
        else if (m.t === 'ready') resolve();
        else if (m.t === 'fail') reject(new Error(m.err));
        else if (m.t === 'res') {
          const c = calls.get(m.id);
          if (!c) return;
          calls.delete(m.id);
          if (m.ok) c.resolve(m.data); else c.reject(new Error(m.err));
        } else if (m.t === 'req') {
          answer(m.kind, m.args).then(
            (data) => worker.postMessage({ t: 'ans', id: m.id, ok: true, data }, data && data.body instanceof ArrayBuffer ? [data.body] : []),
            (err) => worker.postMessage({ t: 'ans', id: m.id, ok: false, err: String(err && err.message || err) }));
        }
      };
      pyodideBases().then((bases) => worker.postMessage({ t: 'init', bundle, mode: MODE, config: JSON.stringify(cfg), udp: window.EPNative.udp, pyodideBases: bases }), reject);
    });
    bundle = null;
    step('Klar', 100);
    document.addEventListener('visibilitychange', () => { if (document.hidden) saveCore(); });
    resolveReady();
    setTimeout(() => overlay.remove(), 300);
    if (MODE === 'demo') {
      const tag = document.createElement('div');
      tag.id = 'ep-demo';
      tag.textContent = 'DEMO · simuleret SolarAssistant + KEBA';
      document.body.appendChild(tag);
    }
    addConnButton();
    setTimeout(checkUpdate, 20000);
    setInterval(checkUpdate, 3 * 3600 * 1000);
  }

  // ---------- Forbindelser (kun i appen – ikke en del af panelet fra Home Assistant) ----------
  const esc = (t) => String(t == null ? '' : t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function addConnButton() {
    const css = document.createElement('style');
    css.textContent =
      '#ep-conn-btn{position:fixed;right:12px;bottom:calc(env(safe-area-inset-bottom) + 112px);z-index:60;width:34px;height:34px;border-radius:50%;border:1px solid rgba(255,255,255,.12);' +
      'background:rgba(13,17,24,.85);color:#f3f5f9;font-size:16px;line-height:32px;text-align:center;cursor:pointer;backdrop-filter:blur(6px)}' +
      '#ep-conn{position:fixed;inset:0;z-index:9998;background:rgba(0,0,0,.55);display:flex;align-items:flex-end;justify-content:center}' +
      '#ep-conn .box{background:#0d1118;color:#f3f5f9;width:100%;max-width:560px;max-height:92vh;overflow:auto;border-radius:18px 18px 0 0;' +
      'padding:18px 18px calc(18px + env(safe-area-inset-bottom));font:14px -apple-system,system-ui,sans-serif}' +
      '#ep-conn h2{margin:0 0 4px;font-size:19px}#ep-conn h3{margin:18px 0 6px;font-size:14px;color:#a99dff}' +
      '#ep-conn label{display:block;margin:8px 0;color:#8b93a3;font-size:12px}#ep-conn input,#ep-conn select{display:block;width:100%;box-sizing:border-box;margin-top:4px;' +
      'background:#131924;color:#f3f5f9;border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:10px;font-size:15px}' +
      '#ep-conn .row{display:flex;gap:8px;margin-top:14px}#ep-conn button{flex:1;padding:12px;border-radius:12px;border:1px solid rgba(255,255,255,.12);' +
      'background:#131924;color:#f3f5f9;font-weight:600;font-size:15px}#ep-conn button.pri{background:#7c6cff;border-color:#7c6cff}' +
      '#ep-conn .muted{color:#8b93a3;font-size:12px}#ep-conn .ok{color:#3ddc97}#ep-conn .bad{color:#ff5d6c}#ep-conn .warn{color:#f5b544}' +
      '#ep-conn table{width:100%;border-collapse:collapse;font-size:12px}#ep-conn td{padding:4px 2px;border-top:1px solid rgba(255,255,255,.06);vertical-align:top;word-break:break-all}' +
      '#ep-conn details{margin-top:8px}#ep-conn summary{cursor:pointer;color:#c9cfdb}';
    document.head.appendChild(css);
    const b = document.createElement('button');
    b.id = 'ep-conn-btn';
    b.type = 'button';
    b.title = 'Forbindelser';
    b.textContent = '🔌';
    b.onclick = openConn;
    document.body.appendChild(b);
  }

  function openConn() {
    const c = loadConn();
    const sa = c.solarassistant || {};
    const kb = c.keba || {};
    const wrap = document.createElement('div');
    wrap.id = 'ep-conn';
    const httpsNote = (!NATIVE && location.protocol === 'https:')
      ? '<p class="warn">Du kører i en browser. Forbindelse til SolarAssistant og KEBA på dit hjemmenetværk kræver Android-appen.</p>' : '';
    wrap.innerHTML =
      '<div class="box"><h2>Forbindelser</h2><div class="muted">Hvor Energipilot henter data og styrer fra. Gemmes kun på denne enhed.</div>' + httpsNote +
      '<label>Kør som<select id="c-mode"><option value="demo">Demo – simuleret hus</option><option value="home">Mit hjem – mine egne enheder</option></select></label>' +
      '<h3>Inverter via SolarAssistant</h3>' +
      '<label>Adresse (IP)<input id="c-sa-host" placeholder="192.168.1.20" value="' + esc(sa.host) + '"></label>' +
      '<label>Lokal adgangskode (SolarAssistant → Configuration)<input id="c-sa-pw" type="password" value="' + esc(sa.password) + '"></label>' +
      '<label>Navn som i Home Assistant<input id="c-sa-name" placeholder="Growatt SPH" value="' + esc(sa.name || 'Growatt SPH') + '"></label>' +
      '<label>Model<input id="c-sa-model" placeholder="SPH 3600" value="' + esc(sa.model || 'SPH 3600') + '"></label>' +
      '<h3>Elbil-lader (KEBA)</h3>' +
      '<label>Adresse (IP)<input id="c-kb-host" placeholder="192.168.1.30" value="' + esc(kb.host) + '"></label>' +
      '<div class="row"><button type="button" id="c-cancel">Luk</button><button type="button" class="pri" id="c-save">Gem og genstart</button></div>' +
      '<h3>Status</h3><div id="c-status" class="muted">Henter…</div></div>';
    document.body.appendChild(wrap);
    wrap.querySelector('#c-mode').value = MODE;
    wrap.onclick = (e) => { if (e.target === wrap) close(); };
    wrap.querySelector('#c-cancel').onclick = close;
    wrap.querySelector('#c-save').onclick = () => {
      const v = (id) => wrap.querySelector(id).value.trim();
      const n = loadConn();
      n.mode = v('#c-mode');
      n.solarassistant = Object.assign({}, n.solarassistant || {}, { host: v('#c-sa-host'), password: wrap.querySelector('#c-sa-pw').value,
        name: v('#c-sa-name') || 'Growatt SPH', model: v('#c-sa-model') || 'SPH 3600' });
      n.keba = Object.assign({}, n.keba || {}, { host: v('#c-kb-host') });
      saveConn(n);
      const u = new URL(location.href);
      u.searchParams.delete('mode');
      saveCore();
      setTimeout(() => location.replace(u.toString()), 400);
    };
    let timer = null;
    function close() { clearInterval(timer); wrap.remove(); }
    async function status() {
      let info;
      try { info = worker ? JSON.parse(await core('info')) : await (await realFetch('/__info')).json(); } catch (e) { return; }
      const el = wrap.querySelector('#c-status');
      if (!el) return;
      let h = '';
      for (const d of info.devices || []) {
        const ok = d.status === 'forbundet' || (d.online && !d.status);
        h += '<div><b>' + esc(d.name) + '</b> · <span class="' + (ok ? 'ok' : 'bad') + '">' + esc(d.status || (d.online ? 'ok' : 'offline')) + '</span>' +
          (d.host ? ' <span class="muted">(' + esc(d.host) + ')</span>' : '') + '</div>';
        if (d.missing && d.missing.length) {
          h += '<div class="warn">Indstillinger SolarAssistant ikke har meldt: ' + d.missing.map(esc).join(', ') + '</div>';
        }
        if (d.topics && d.topics.length) {
          h += '<details><summary>' + d.topics.length + ' emner fra SolarAssistant</summary><table>' +
            d.topics.map((t) => '<tr><td>' + esc(t.topic) + '</td><td>' + esc(t.value) + ' ' + esc(t.unit || '') + '</td><td class="muted">' + esc(t.entity || '') + '</td></tr>').join('') +
            '</table></details>';
        }
      }
      if (!(info.devices || []).length) h += '<div class="warn">Ingen enheder. Udfyld adresserne ovenfor og vælg "Mit hjem".</div>';
      const sv = (info.services || []).slice(-8).reverse();
      if (sv.length) {
        h += '<details><summary>Seneste kommandoer</summary><table>' + sv.map((s) => '<tr><td>' + esc(s.t.slice(11, 19)) + '</td><td>' + esc(s.domain + '.' + s.service) +
          '</td><td class="muted">' + esc((s.entity_id || []).join(', ') + ' ' + JSON.stringify(s.data || {})) + '</td></tr>').join('') + '</table></details>';
      }
      h += '<details><summary>Log</summary><table>' + (info.log || []).slice(-25).reverse().map((l) => '<tr><td>' + esc(l) + '</td></tr>').join('') + '</table></details>';
      el.innerHTML = h;
    }
    status();
    timer = setInterval(status, 3000);
  }

  // ---------- Automatisk opdatering ----------
  // The app fetches the newest panel and core from GitHub Pages and stores them on the phone; the starter page
  // (index.html) runs them next time. Only a change to the Android part itself needs a new APK - then it says so.
  const UPDATE_BASE = 'https://clahen86.github.io/Energy-Pilot-app/www/';
  const RELEASE_URL = 'https://github.com/Clahen86/Energy-Pilot-app/releases/tag/android-latest';

  async function runningVersion() {
    if (window.EP_OTA) return window.EP_OTA;
    try { return await (await realFetch('version.json', { cache: 'no-store' })).json(); } catch (e) { return null; }
  }

  async function checkUpdate() {
    if (DEV || /github\.io$/.test(location.hostname) || location.protocol === 'file:') return;
    try {
      const cur = await runningVersion();
      const r = await realFetch(UPDATE_BASE + 'version.json?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const nv = await r.json();
      if (!cur || !nv || nv.id === cur.id || !(nv.built > cur.built)) return;
      if (nv.native !== cur.native) {
        updateBanner('Ny Android-app (Energipilot ' + nv.core + ') er klar', 'Hent den', () => { window.open(RELEASE_URL, '_system'); });
        return;
      }
      const stored = JSON.parse(localStorage.getItem('ep_ota_meta') || 'null');
      if (!stored || stored.id !== nv.id) {
        const get = async (f) => { const x = await realFetch(UPDATE_BASE + f + '?v=' + nv.id, { cache: 'no-store' }); if (!x.ok) throw new Error(f); return x.text(); };
        const [html, bridge, bundle, workerSrc] = await Promise.all([get('app.html'), get('js/bridge.js'), get('py/bundle.json'), get('js/worker.js')]);
        JSON.parse(bundle);  // must be whole
        if (html.indexOf('<script src="js/bridge.js"></script>') < 0) return;
        const page = html.replace('<script src="js/bridge.js"></script>', () => '<script>' + bridge.replace(/<\/script/gi, '<\\/script') + '</script>');
        try {
          localStorage.setItem('ep_ota_html', page);
          localStorage.setItem('ep_ota_bundle', bundle);
          localStorage.setItem('ep_ota_worker', workerSrc);
          localStorage.setItem('ep_ota_meta', JSON.stringify(nv));
        } catch (e) {
          ['ep_ota_html', 'ep_ota_bundle', 'ep_ota_worker', 'ep_ota_meta'].forEach((k) => { try { localStorage.removeItem(k); } catch (e2) { /* */ } });
          return;
        }
      }
      updateBanner('Energipilot ' + nv.core + ' er hentet', 'Genstart', () => {
        saveCore();
        setTimeout(() => location.replace('index.html'), 400);
      });
    } catch (e) { /* offline or GitHub unreachable: try again later */ }
  }

  function updateBanner(text, button, action) {
    if (document.getElementById('ep-update')) return;
    const b = document.createElement('div');
    b.id = 'ep-update';
    b.style.cssText = 'position:fixed;left:12px;right:12px;bottom:calc(env(safe-area-inset-bottom) + 160px);z-index:70;background:#131924;' +
      'color:#f3f5f9;border:1px solid rgba(124,108,255,.5);border-radius:14px;padding:12px 14px;display:flex;align-items:center;gap:10px;' +
      'font:14px -apple-system,system-ui,sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.4)';
    b.innerHTML = '<div style="flex:1">' + esc(text) + '</div>';
    const go = document.createElement('button');
    go.textContent = button;
    go.style.cssText = 'background:#7c6cff;color:#fff;border:0;border-radius:10px;padding:9px 12px;font-weight:600';
    go.onclick = action;
    const x = document.createElement('button');
    x.textContent = '✕';
    x.style.cssText = 'background:none;border:0;color:#8b93a3;font-size:16px;padding:4px';
    x.onclick = () => b.remove();
    b.appendChild(go);
    b.appendChild(x);
    document.body.appendChild(b);
  }

  // ---------- Panelets API-kald går til Python ----------
  function apiPath(input) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const m = url.match(/^(?:\.\/|\/)?(api\/[^?#]*)(\?[^#]*)?/);
    if (!m) {
      // En absolut adresse på samme side (fx https://…/api/x)
      try {
        const u = new URL(url, location.href);
        if (u.origin === location.origin) {
          const i = u.pathname.indexOf('/api/');
          if (i >= 0) return { path: u.pathname.slice(i), query: u.search };
        }
      } catch (e) { /* */ }
      return null;
    }
    return { path: '/' + m[1], query: m[2] || '' };
  }

  window.fetch = async function (input, init) {
    const api = apiPath(input);
    if (!api) return realFetch(input, init);
    await ready;
    const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const query = {};
    new URLSearchParams(api.query).forEach((v, k) => { query[k] = v; });
    let body = (init && init.body) || '';
    if (body && typeof body !== 'string') body = await new Response(body).text();
    const raw = await core('api', { method, path: api.path, query: JSON.stringify(query), body });
    const r = JSON.parse(raw);
    return new Response(r.body, { status: r.status, headers: { 'Content-Type': r.content_type || 'application/json' } });
  };

  if (DEV) {
    window.fetch = realFetch;
    const devReady = () => { overlay.remove(); addConnButton(); };
    if (document.body) devReady(); else document.addEventListener('DOMContentLoaded', devReady);
  } else {
    start().catch(fail);
  }
})();
