/*
 * Energipilot – broen mellem panelet og Python-kernen.
 *
 * 1. Indlæser Pyodide (Python i browseren/WebView) med numpy og scikit-learn.
 * 2. Lægger Energy Pilots Python-kode (www/py) ind i Pyodides filsystem.
 * 3. Starter energy_pilot.main() med den virtuelle Home Assistant (standalone/boot.py).
 * 4. Sender panelets kald til "api/..." direkte til web.py i Python – i stedet for over netværket.
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

  const PYODIDE_VERSIONS = ['v0.29.0', 'v0.28.3', 'v0.28.0', 'v0.27.7'];
  const MODE = (new URLSearchParams(location.search).get('mode')) || 'demo';
  // Udvikling: ?dev=1 springer Pyodide over og bruger tools/dev_server.py (samme kerne i almindelig Python).
  const DEV = new URLSearchParams(location.search).get('dev') === '1';
  const realFetch = window.fetch.bind(window);
  let resolveReady, rejectReady;
  const ready = new Promise((res, rej) => { resolveReady = res; rejectReady = rej; });
  let boot = null;

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

  // ---------- Pyodide ----------
  function loadScript(src) {
    return new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = () => res(src);
      s.onerror = () => rej(new Error('kunne ikke hente ' + src));
      document.head.appendChild(s);
    });
  }

  async function loadPyodideAnywhere() {
    // Den native app har Pyodide med i pakken (www/pyodide/); ellers hentes den fra jsDelivr.
    const bases = ['pyodide/'].concat(PYODIDE_VERSIONS.map((v) => 'https://cdn.jsdelivr.net/pyodide/' + v + '/full/'));
    let lastErr;
    for (const base of bases) {
      try {
        if (base === 'pyodide/') {
          const head = await realFetch('pyodide/pyodide.js', { method: 'HEAD' }).catch(() => null);
          if (!head || !head.ok) continue;
        }
        await loadScript(base + 'pyodide.js');
        return await window.loadPyodide({ indexURL: base });
      } catch (e) { lastErr = e; }
    }
    throw lastErr || new Error('Pyodide kunne ikke hentes');
  }

  async function persist(py, load) {
    try {
      await new Promise((res, rej) => py.FS.syncfs(!!load, (err) => (err ? rej(err) : res())));
    } catch (e) { /* ingen IndexedDB her: data holdes kun mens appen er åben */ }
  }

  async function start() {
    step('Henter Python…', 8);
    const py = await loadPyodideAnywhere();
    step('Henter læringsmodeller (scikit-learn)…', 35);
    await py.loadPackage(['numpy', 'scikit-learn']);
    try { await py.loadPackage(['tzdata']); } catch (e) { /* tidszoner findes måske allerede */ }

    step('Gør plads til dine data…', 60);
    try { py.FS.mkdir('/data'); } catch (e) { /* findes */ }
    try { py.FS.mount(py.FS.filesystems.IDBFS, {}, '/data'); } catch (e) { /* ingen IndexedDB */ }
    await persist(py, true);

    step('Lægger Energipilot ind…', 70);
    const bundle = await (await realFetch('py/bundle.json')).json();
    const files = Object.keys(bundle);
    const texts = files.map((f) => bundle[f]);
    files.forEach((f, i) => {
      const path = '/app/' + f;
      const dirs = path.split('/').slice(1, -1);
      let cur = '';
      for (const d of dirs) { cur += '/' + d; try { py.FS.mkdir(cur); } catch (e) { /* findes */ } }
      py.FS.writeFile(path, texts[i]);
    });

    step('Starter motoren…', 85);
    py.runPython("import sys\nsys.path.insert(0, '/app')\nfrom standalone import boot\n");
    boot = py.globals.get('boot');
    boot.start(MODE, JSON.stringify({ language: (navigator.language || 'da').slice(0, 2) }));
    window.EP_PY = py;
    window.EP_BOOT = boot;

    // Vent til den virtuelle Home Assistant er klar
    for (let i = 0; i < 300 && !boot.ready(); i++) await new Promise((r) => setTimeout(r, 100));
    step('Klar', 100);
    setInterval(() => { try { boot.save(); } catch (e) { /* */ } persist(py, false); }, 60000);
    document.addEventListener('visibilitychange', () => { if (document.hidden) { try { boot.save(); } catch (e) { /* */ } persist(py, false); } });
    resolveReady();
    setTimeout(() => overlay.remove(), 300);
    if (MODE === 'demo') {
      const tag = document.createElement('div');
      tag.id = 'ep-demo';
      tag.textContent = 'DEMO · simuleret Growatt + KEBA';
      document.body.appendChild(tag);
    }
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
    const raw = await boot.handle(method, api.path, JSON.stringify(query), body);
    const r = JSON.parse(raw);
    return new Response(r.body, { status: r.status, headers: { 'Content-Type': r.content_type || 'application/json' } });
  };

  if (DEV) {
    window.fetch = realFetch;
    if (document.body) overlay.remove(); else document.addEventListener('DOMContentLoaded', () => overlay.remove());
  } else {
    start().catch(fail);
  }
})();
