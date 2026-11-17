/*
 * Energipilot – Python-kernen i en baggrundstråd (Web Worker).
 *
 * Pyodide og hele Energy Pilot kører her, så panelet ikke fryser, mens motoren planlægger og lærer.
 * Siden (bridge.js) sender "init" med Python-koden og taler derefter med kernen gennem beskeder:
 *   siden -> her:  {t:'call', id, kind:'api'|'info'|'save', args}      svar: {t:'res', id, ok, data|err}
 *   her -> siden:  {t:'req', id, kind:'udp'|'fetch', args}             svar: {t:'ans', id, ok, data|err}
 * UDP (KEBA) og kald til andre servere går gennem siden: kun den har appens native plugin og native HTTP.
 */
/* global importScripts, loadPyodide */
(function () {
  'use strict';
  let py = null;
  let boot = null;
  let seq = 0;
  const waiting = new Map();

  function progress(msg, pct) { self.postMessage({ t: 'progress', msg, pct }); }

  function askPage(kind, args, transfer) {
    return new Promise((resolve, reject) => {
      const id = ++seq;
      waiting.set(id, { resolve, reject });
      self.postMessage({ t: 'req', id, kind, args }, transfer || []);
    });
  }

  async function loadPy(bases) {
    let lastErr;
    for (const base of bases) {
      try {
        importScripts(base + 'pyodide.js');
        return await loadPyodide({ indexURL: base });
      } catch (e) { lastErr = e; }
    }
    throw lastErr || new Error('Pyodide kunne ikke hentes');
  }

  async function persist(load) {
    try {
      await new Promise((res, rej) => py.FS.syncfs(!!load, (err) => (err ? rej(err) : res())));
    } catch (e) { /* ingen IndexedDB her: data holdes kun mens appen er åben */ }
  }

  // Python's kald til andre servere (priser, vejr …) sendes gennem siden, som i appen bruger native HTTP.
  function routeFetchThroughPage() {
    const workerFetch = self.fetch.bind(self);
    self.fetch = async function (input, init) {
      const url = typeof input === 'string' ? input : (input && input.url) || String(input);
      let outside = false;
      try { outside = /^https?:$/.test(new URL(url, self.location.href).protocol) && new URL(url, self.location.href).origin !== self.location.origin; } catch (e) { /* */ }
      if (!outside) return workerFetch(input, init);
      init = init || {};
      let headers = {};
      try { headers = Object.fromEntries(new Headers(init.headers || {}).entries()); } catch (e) { /* */ }
      let body = init.body;
      if (body != null && typeof body !== 'string') body = await new Response(body).text();
      const r = await askPage('fetch', { url, method: init.method || 'GET', headers, body: body == null ? null : body });
      const noBody = r.status === 204 || r.status === 304 || r.status < 200;
      return new Response(noBody ? null : r.body, { status: r.status, headers: r.headers });
    };
  }

  async function init(m) {
    progress('Henter Python…', 8);
    py = await loadPy(m.pyodideBases);
    progress('Henter læringsmodeller (scikit-learn)…', 35);
    await py.loadPackage(['numpy', 'scikit-learn']);
    try { await py.loadPackage(['tzdata']); } catch (e) { /* tidszoner findes måske allerede */ }

    progress('Gør plads til dine data…', 60);
    try { py.FS.mkdir('/data'); } catch (e) { /* findes */ }
    try { py.FS.mount(py.FS.filesystems.IDBFS, {}, '/data'); } catch (e) { /* ingen IndexedDB */ }
    await persist(true);

    progress('Lægger Energipilot ind…', 70);
    const bundle = JSON.parse(m.bundle);
    for (const f of Object.keys(bundle)) {
      const path = '/app/' + f;
      let cur = '';
      for (const d of path.split('/').slice(1, -1)) { cur += '/' + d; try { py.FS.mkdir(cur); } catch (e) { /* findes */ } }
      py.FS.writeFile(path, bundle[f]);
    }

    self.EPNative = {
      udp: !!m.udp,
      udpRequest: (host, port, message, timeoutMs, localPort, expectReply) =>
        askPage('udp', { host, port, message, timeoutMs, localPort, expectReply }),
    };
    routeFetchThroughPage();

    progress('Starter motoren…', 85);
    py.runPython("import sys\nsys.path.insert(0, '/app')\nfrom standalone import boot\n");
    boot = py.globals.get('boot');
    boot.start(m.mode, m.config);
    for (let i = 0; i < 300 && !boot.ready(); i++) await new Promise((r) => setTimeout(r, 100));
    setInterval(save, 60000);
    self.postMessage({ t: 'ready' });
  }

  async function save() {
    try { boot.save(); } catch (e) { /* */ }
    await persist(false);
  }

  async function call(m) {
    const a = m.args || {};
    if (m.kind === 'api') return await boot.handle(a.method, a.path, a.query, a.body || '');
    if (m.kind === 'info') return boot.info();
    if (m.kind === 'save') { await save(); return null; }
    throw new Error('ukendt kald ' + m.kind);
  }

  self.onmessage = (e) => {
    const m = e.data || {};
    if (m.t === 'init') {
      init(m).catch((err) => self.postMessage({ t: 'fail', err: String(err && (err.stack || err.message) || err) }));
    } else if (m.t === 'call') {
      call(m).then((data) => self.postMessage({ t: 'res', id: m.id, ok: true, data }),
        (err) => self.postMessage({ t: 'res', id: m.id, ok: false, err: String(err && err.message || err) }));
    } else if (m.t === 'ans') {
      const w = waiting.get(m.id);
      if (!w) return;
      waiting.delete(m.id);
      if (m.ok) w.resolve(m.data); else w.reject(new Error(m.err));
    }
  };
})();
