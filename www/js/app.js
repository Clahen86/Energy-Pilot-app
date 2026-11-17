/*
 * Energipilot – app (brugerflade og styring)
 */
(function () {
  const EP = window.EP;
  const $ = (id) => document.getElementById(id);

  // ---------- Lager (kun på denne enhed) ----------
  const store = {
    get(key, def) {
      try { const v = localStorage.getItem('ep.' + key); return v ? JSON.parse(v) : def; } catch (e) { return def; }
    },
    set(key, val) {
      try { localStorage.setItem('ep.' + key, JSON.stringify(val)); } catch (e) { /* ikke tilgængelig */ }
    },
  };

  const DEFAULTS = {
    inverter: 'demo', charger: 'demo', mode: 'skygge',
    priceArea: 'DK2', lat: 55.68, lon: 12.57,
    tariffLow: 0.15, tariffMid: 0.45, tariffPeak: 1.05,
    elafgift: 0.008, otherFees: 0.16, vat: 0.25, sellFee: 0.05,
    pvEnabled: true, pvKwp: 8, pvPerformance: 0.8, pvLearnedFactor: 1,
    battEnabled: true, battKwh: 10, battPowerKw: 5, battEff: 0.9, battMinSoc: 10, battMaxSoc: 100, allowExport: false,
    carEnabled: true, carNeedKwh: 20, carPowerKw: 11, carReadyTime: '07:00', chargeNow: false,
    growattToken: '', kebaIp: '',
  };

  const state = {
    s: Object.assign({}, DEFAULTS, store.get('settings', {})),
    history: store.get('history', {}),
    savings: store.get('savings', {}),
    sim: store.get('sim', { soc: 45 }),
    chargerState: {},
    market: null,
    plan: null,
    live: null,
    lastTick: Date.now(),
    loadAcc: { key: null, sum: 0, n: 0 },
    selected: null,
  };
  state.s.chargeNow = false;

  let inverter, charger;
  function makeDrivers() {
    inverter = EP.createInverter(state.s.inverter, state.sim, state.s);
    charger = EP.createCharger(state.s.charger, state.chargerState, state.s);
  }

  // ---------- Formattering ----------
  const kr = (v) => (v == null || isNaN(v) ? '–' : v.toLocaleString('da-DK', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const kw = (w) => (w == null || isNaN(w) ? '–' : (Math.abs(w) >= 1000 ? (w / 1000).toLocaleString('da-DK', { maximumFractionDigits: 1 }) + ' kW' : Math.round(w) + ' W'));
  const hm = (d) => d.toLocaleTimeString('da-DK', { hour: '2-digit', minute: '2-digit' });
  const dayName = (d) => {
    const today = EP.startOfDay(new Date());
    const diff = Math.round((EP.startOfDay(d) - today) / 86400000);
    return diff === 0 ? 'i dag' : diff === 1 ? 'i morgen' : d.toLocaleDateString('da-DK', { weekday: 'short' });
  };
  const dateKey = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');

  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, 3500);
  }

  // ---------- Data og plan ----------
  async function loadMarket() {
    const now = new Date();
    state.market = await EP.loadMarketData(state.s, now);
    state.market.loadedAt = now;
    const n = $('notice');
    if (state.market.demo) {
      n.hidden = false;
      n.textContent = 'Demodata: kunne ikke hente live priser/vejr her. Når appen kører på din telefon, hentes de fra ' +
        'Energi Data Service og Open-Meteo.';
    } else {
      n.hidden = true;
    }
    replan();
  }

  function currentSlotIndex() {
    if (!state.plan) return 0;
    const now = Date.now();
    const sl = state.plan.slots;
    for (let i = sl.length - 1; i >= 0; i--) if (sl[i].t.getTime() <= now) return i;
    return 0;
  }

  function replan() {
    if (!state.market) return;
    const now = new Date();
    const cur = new Date(now);
    cur.setMinutes(Math.floor(cur.getMinutes() / 15) * 15, 0, 0);
    const keep = state.market.slots.map((sl, i) => [sl, state.market.radiation[i]]).filter(([sl]) => sl.t >= cur);
    const slots = keep.map((x) => x[0]);
    const radiation = keep.map((x) => x[1]);
    if (!slots.length) { loadMarket(); return; }
    const socPct = state.live && state.live.soc != null ? state.live.soc : state.sim.soc;
    state.plan = EP.makePlan({ slots, radiation, history: state.history, settings: state.s, socPct, now });
    state.plan.madeAt = now;
    if (state.s.mode === 'aktiv') sendPlan();
    render();
  }

  async function sendPlan() {
    try {
      await inverter.applyPlan(state.plan);
      if (state.s.carEnabled) await charger.applyPlan(state.plan);
    } catch (e) {
      state.s.mode = 'skygge';
      saveSettings();
      toast('Styring slået fra: ' + e.message);
      renderMode();
    }
  }

  // ---------- Live ----------
  async function tick() {
    const now = Date.now();
    const dtSec = Math.min(300, Math.max(1, (now - state.lastTick) / 1000));
    state.lastTick = now;
    if (!state.plan) return;
    const i = currentSlotIndex();
    if (i > 0) { replan(); return; } // nyt kvarter → planlæg igen
    const ctx = { plan: state.plan, slotIndex: 0, mode: state.s.mode, s: state.s, dtSec };
    try {
      const c = state.s.carEnabled ? await charger.getLive(ctx) : { carW: 0 };
      ctx.carKw = (c.carW || 0) / 1000;
      const inv = await inverter.getLive(ctx);
      state.live = Object.assign({}, inv, { carW: c.carW || 0, at: new Date() });
      store.set('sim', state.sim);
      learn(state.live);
      accrueSavings(dtSec);
    } catch (e) {
      state.live = null;
      $('liveAge').textContent = '· ' + e.message;
    }
    renderLive();
  }

  /** Lærer husets forbrug (uden bil) – gennemsnit pr. time gemmes som historik. */
  function learn(live) {
    const d = new Date();
    const key = d.getDay() + '-' + d.getHours();
    const acc = state.loadAcc;
    if (acc.key && acc.key !== key && acc.n > 0) {
      // Timen er slut: gem gennemsnittet for den ugedag og time
      const kwAvg = acc.sum / acc.n;
      const h = state.history[acc.key] || { avg: kwAvg, n: 0 };
      const alpha = Math.max(0.05, 1 / (h.n + 1));
      h.avg += alpha * (kwAvg - h.avg);
      h.n = Math.min(h.n + 1, 1000);
      state.history[acc.key] = h;
      store.set('history', state.history);
      acc.sum = 0; acc.n = 0;
    }
    acc.key = key;
    acc.sum += Math.max(0, live.loadW) / 1000;
    acc.n += 1;
  }

  /** Registrerer besparelsen for det kvarter, der er i gang. */
  function accrueSavings(dtSec) {
    const p = state.plan;
    if (!p) return;
    const sl = p.slots[0];
    const g = p.grid[0] * EP.SLOT_H, g0 = (p.load[0] + p.car[0] - p.pv[0]) * EP.SLOT_H;
    const c = g > 0 ? g * p.buy[0] : g * p.sell[0];
    const c0 = g0 > 0 ? g0 * p.buy[0] : g0 * p.sell[0];
    const share = dtSec / (EP.SLOT_H * 3600);
    const k = dateKey(sl.t);
    state.savings[k] = (state.savings[k] || 0) + (c0 - c) * share;
    store.set('savings', state.savings);
  }

  /** I demo-tilstand: simulerer de sidste 14 dage, så besparelsesgrafen har indhold. */
  function seedDemoHistory() {
    if (state.s.inverter !== 'demo' || Object.keys(state.savings).length > 1) return;
    const today = EP.startOfDay(new Date());
    for (let d = 14; d >= 1; d--) {
      const day = new Date(today.getTime() - d * 86400000);
      const slots = EP.demoPrices(day, 1);
      const rad = slots.map((sl) => EP.demoRadiation(sl.t));
      const s = Object.assign({}, state.s, { carReadyTime: '23:59', chargeNow: false });
      const p = EP.makePlan({ slots, radiation: rad, history: state.history, settings: s, socPct: 30, now: day });
      state.savings[dateKey(day)] = Math.max(0, p.saving);
    }
    state.savings._demo = true;
    store.set('savings', state.savings);
  }

  // ---------- Visning ----------
  function render() {
    renderMode();
    renderNow();
    renderLive();
    renderPlanChart();
    renderBlocks();
    renderSavings();
  }

  function renderMode() {
    const active = state.s.mode === 'aktiv';
    const b = $('modeBadge');
    b.textContent = active ? 'Styrer' : 'Skygge';
    b.className = 'badge ' + (active ? 'active' : 'shadow');
    $('btnMode').textContent = active ? 'Tilbage til skygge' : 'Slå styring til';
    $('btnChargeNow').classList.toggle('on', !!state.s.chargeNow);
    $('btnChargeNow').textContent = state.s.chargeNow ? '⏹ Stop "lad nu"' : '⚡ Lad bilen nu';
  }

  function renderNow() {
    const p = state.plan;
    if (!p) return;
    const a = p.action[0];
    const texts = {
      'Lad fra net': ['Lader batteriet', 'Billig strøm fra nettet'],
      'Lad fra sol': ['Lader batteriet', 'Med overskud fra solcellerne'],
      'Aflad': ['Bruger batteriet', 'Strømmen er dyr nu'],
      'Hold': ['Gemmer batteriet', 'Til en dyrere periode senere'],
      'Normal': ['Normal drift', 'Ingen grund til at bruge batteriet'],
    };
    let [t1, t2] = texts[a] || ['–', ''];
    if (p.car[0] > 0) { t1 += ' + lader bilen'; }
    if (state.s.mode !== 'aktiv') t2 += ' · skyggetilstand (styrer ikke)';
    $('nowAction').textContent = t1;
    $('nowSub').textContent = t2;
    $('nowPrice').textContent = kr(p.buy[0]);

    // Bil klar
    if (state.s.carEnabled) {
      const e = p.car.reduce((x, v) => x + v * EP.SLOT_H, 0);
      if (state.s.chargeNow) {
        let last = -1; p.car.forEach((v, i) => { if (v > 0) last = i; });
        $('carReady').textContent = last >= 0 ? hm(new Date(p.slots[last].t.getTime() + 15 * 60000)) : '–';
        $('carReadySub').textContent = 'Lader nu med fuld fart';
      } else if (p.carDeadline) {
        $('carReady').textContent = hm(p.carDeadline);
        $('carReadySub').textContent = p.carUnmet > 0.1
          ? 'Mangler ' + p.carUnmet.toFixed(0) + ' kWh – priser for i morgen kommer ca. kl. 13'
          : e.toFixed(0) + ' kWh planlagt ' + dayName(p.carDeadline) +
            (p.slots.some((sl, i) => sl.est && p.car[i] > 0) ? ' (foreløbig – endelige priser ca. kl. 13)' : '');
      }
    } else {
      $('carReady').textContent = '–';
      $('carReadySub').textContent = 'Ingen elbil';
    }

    // Sparet i dag
    const now = new Date();
    const end = EP.startOfDay(now); end.setDate(end.getDate() + 1);
    const rest = EP.summarize(p, now, end, state.s);
    const got = state.savings[dateKey(now)] || 0;
    $('savedToday').textContent = kr(got) + ' kr';
    $('savedTodaySub').textContent = 'Forventet i alt ' + kr(got + rest.saving) + ' kr';
  }

  function renderLive() {
    const l = state.live;
    if (!l) return;
    $('liveSun').textContent = state.s.pvEnabled ? kw(l.pvW) : '–';
    $('liveHouse').textContent = kw(l.loadW);
    $('liveGrid').textContent = kw(Math.abs(l.gridW));
    $('liveGridLbl').textContent = l.gridW < -20 ? 'Sælger' : 'Køber';
    $('liveBatt').textContent = state.s.battEnabled ? Math.round(l.soc) + ' %' : '–';
    $('liveBattLbl').textContent = !state.s.battEnabled ? 'Batteri' : l.battW > 20 ? 'Lader ' + kw(l.battW) : l.battW < -20 ? 'Afgiver ' + kw(-l.battW) : 'Hviler';
    $('socFill').style.width = (state.s.battEnabled ? l.soc : 0) + '%';
    $('liveCar').textContent = state.s.carEnabled ? kw(l.carW) : '–';
    $('liveAge').textContent = '· opdateret ' + hm(l.at) + (state.s.inverter === 'demo' ? ' (simuleret)' : '');
  }

  function colorFor(a) {
    return { 'Lad fra net': '#4ea8ff', 'Lad fra sol': '#ffd166', 'Aflad': '#3ecf8e', 'Hold': '#6b7c8d', 'Normal': '#3a4a5a' }[a] || '#3a4a5a';
  }

  function setupCanvas(cv) {
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth || 320;
    if (!cv.dataset.h) cv.dataset.h = cv.getAttribute('height');
    const h = parseInt(cv.dataset.h, 10);
    cv.width = w * dpr; cv.height = h * dpr;
    cv.style.height = h + 'px';
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    return { g, w, h };
  }

  function renderPlanChart() {
    const p = state.plan;
    const cv = $('planChart');
    if (!p || !cv.clientWidth) return;
    const { g, w, h } = setupCanvas(cv);
    const n = p.slots.length;
    const padL = 34, padR = 30, padT = 10, padB = 34;
    const cw = w - padL - padR, ch = h - padT - padB;
    const maxP = Math.max(1, Math.ceil(Math.max(...p.buy) * 2) / 2);
    const bw = cw / n;
    const x = (i) => padL + i * bw;
    const yP = (v) => padT + ch - (v / maxP) * ch;
    const yS = (v) => padT + ch - (v / 100) * ch;

    g.font = '10px -apple-system, sans-serif';
    g.fillStyle = '#8a9bab';
    g.strokeStyle = '#2a3846';
    g.lineWidth = 1;
    for (let v = 0; v <= maxP + 1e-9; v += maxP / 4) {
      g.beginPath(); g.moveTo(padL, yP(v)); g.lineTo(w - padR, yP(v)); g.stroke();
      g.textAlign = 'right'; g.fillText(v.toFixed(1), padL - 4, yP(v) + 3);
    }
    g.textAlign = 'left';
    g.fillText('kr', 2, padT + 8);
    g.textAlign = 'right';
    [0, 50, 100].forEach((v) => g.fillText(v + '%', w - 2, yS(v) + 3));

    for (let i = 0; i < n; i++) {
      g.fillStyle = colorFor(p.action[i]);
      if (state.selected === i) g.fillStyle = '#ffffff';
      g.globalAlpha = p.slots[i].est ? 0.4 : 1;
      g.fillRect(x(i) + 0.5, yP(p.buy[i]), Math.max(1, bw - 1), yP(0) - yP(p.buy[i]));
      g.globalAlpha = 1;
      if (p.car[i] > 0) { g.fillStyle = '#b98cff'; g.fillRect(x(i), padT + ch + 3, Math.max(1, bw), 5); }
    }

    // SOC-linje
    if (state.s.battEnabled) {
      g.strokeStyle = '#ffffff'; g.lineWidth = 1.6; g.beginPath();
      for (let i = 0; i <= n; i++) { const xx = padL + i * bw; i ? g.lineTo(xx, yS(p.soc[i])) : g.moveTo(xx, yS(p.soc[i])); }
      g.stroke();
    }

    // Tidsakse og dagsskift
    g.fillStyle = '#8a9bab'; g.textAlign = 'center'; g.lineWidth = 1;
    for (let i = 0; i < n; i++) {
      const t = p.slots[i].t;
      if (t.getMinutes() === 0 && t.getHours() % 3 === 0) g.fillText(String(t.getHours()).padStart(2, '0'), x(i), h - 14);
      if (t.getMinutes() === 0 && t.getHours() === 0 && i > 0) {
        g.strokeStyle = '#8a9bab'; g.setLineDash([3, 3]);
        g.beginPath(); g.moveTo(x(i), padT); g.lineTo(x(i), padT + ch); g.stroke(); g.setLineDash([]);
        g.fillText(dayName(t), x(i) + 22, padT + 10);
      }
    }
    g.fillStyle = '#f5b83d'; g.fillText('nu', x(0) + 8, h - 2);
    $('planRange').textContent = '· ' + hm(p.slots[0].t) + ' ' + dayName(p.slots[0].t) + ' – ' +
      hm(new Date(p.slots[n - 1].t.getTime() + 15 * 60000)) + ' ' + dayName(p.slots[n - 1].t) +
      ' · ' + state.market.priceSource;
    cv._geom = { padL, bw, n };
  }

  function showSlot(i) {
    const p = state.plan;
    if (!p || i == null || i < 0 || i >= p.slots.length) return;
    state.selected = i;
    const t = p.slots[i].t;
    const parts = [
      '<b>' + dayName(t) + ' ' + hm(t) + '–' + hm(new Date(t.getTime() + 15 * 60000)) + '</b>',
      'Købspris ' + kr(p.buy[i]) + ' kr · spot ' + kr(p.slots[i].spot) + ' kr' + (p.slots[i].est ? ' (estimat)' : ''),
      'Plan: ' + p.action[i] + (Math.abs(p.batt[i]) > 0.05 ? ' (' + p.batt[i].toFixed(1) + ' kW)' : '') + (state.s.battEnabled ? ' · batteri ' + Math.round(p.soc[i + 1]) + ' %' : ''),
      'Forbrug ' + p.load[i].toFixed(1) + ' kW · sol ' + p.pv[i].toFixed(1) + ' kW' + (p.car[i] > 0 ? ' · bil ' + p.car[i].toFixed(1) + ' kW' : ''),
    ];
    $('slotInfo').innerHTML = parts.join('<br>');
    renderPlanChart();
  }

  function renderBlocks() {
    const p = state.plan;
    if (!p) return;
    const ul = $('nextBlocks');
    ul.innerHTML = '';
    const items = [];
    let cur = null;
    const kindOf = (i) => (p.action[i] === 'Normal' || p.action[i] === 'Hold' ? null : p.action[i]);
    for (let i = 0; i < p.slots.length; i++) {
      const k = kindOf(i);
      if (cur && cur.k === k) { cur.end = i; continue; }
      if (cur && cur.k) items.push(cur);
      cur = { k, start: i, end: i };
    }
    if (cur && cur.k) items.push(cur);
    // Bilens ladeblokke
    let cb = null;
    for (let i = 0; i < p.slots.length; i++) {
      if (p.car[i] > 0) { if (cb && cb.end === i - 1) cb.end = i; else { if (cb) items.push(cb); cb = { k: 'Bil lader', start: i, end: i }; } }
    }
    if (cb) items.push(cb);
    items.sort((a, b) => a.start - b.start);
    const cls = { 'Lad fra net': 'charge', 'Lad fra sol': 'sun', 'Aflad': 'dis', 'Bil lader': 'car' };
    items.slice(0, 10).forEach((b) => {
      const li = document.createElement('li');
      const from = p.slots[b.start].t, to = new Date(p.slots[b.end].t.getTime() + 15 * 60000);
      li.innerHTML = '<span>' + dayName(from) + ' ' + hm(from) + '–' + hm(to) + '</span><span class="tag ' + cls[b.k] + '">' + b.k + '</span>';
      ul.appendChild(li);
    });
    if (!items.length) ul.innerHTML = '<li class="muted">Ingen skift planlagt.</li>';
  }

  function renderSavings() {
    const p = state.plan;
    if (!p) return;
    const now = new Date();
    const end = EP.startOfDay(now); end.setDate(end.getDate() + 1);
    const end2 = new Date(end.getTime() + 86400000);
    const rest = EP.summarize(p, now, end, state.s);
    const got = state.savings[dateKey(now)] || 0;
    $('sToday').textContent = kr(got + rest.saving) + ' kr';
    const tm = EP.summarize(p, end, end2, state.s);
    const hasTomorrow = p.slots.some((sl) => sl.t >= end);
    $('sTomorrow').textContent = hasTomorrow ? kr(tm.saving) + ' kr' : '–';
    const tmEst = p.slots.some((sl) => sl.t >= end && sl.est);
    $('sTomorrowSub').textContent = !hasTomorrow ? 'priser kommer ca. kl. 13' : tmEst ? 'estimat – endelige priser ca. kl. 13' : 'sparet vs. uden batteri';
    const mk = dateKey(now).slice(0, 7);
    const month = Object.entries(state.savings).filter(([k]) => k.startsWith(mk)).reduce((a, [, v]) => a + v, 0);
    $('sMonth').textContent = kr(month) + ' kr';
    $('sStored').textContent = p.storedCostPerKwh ? kr(p.storedCostPerKwh) : '–';

    const cv = $('saveChart');
    if (!cv.clientWidth) return;
    const { g, w, h } = setupCanvas(cv);
    const days = [];
    for (let d = 13; d >= 0; d--) { const x = new Date(now.getTime() - d * 86400000); days.push([x, state.savings[dateKey(x)] || 0]); }
    const max = Math.max(1, ...days.map((d) => d[1]));
    const padB = 20, padT = 30, bw = w / days.length;
    g.font = '10px -apple-system, sans-serif'; g.textAlign = 'center';
    days.forEach(([d, v], i) => {
      const bh = (v / max) * (h - padB - padT);
      g.fillStyle = i === days.length - 1 ? '#f5b83d' : '#3ecf8e';
      g.fillRect(i * bw + 3, h - padB - bh, bw - 6, bh);
      g.fillStyle = '#8a9bab';
      g.fillText(String(d.getDate()), i * bw + bw / 2, h - 6);
      if (v > 0) g.fillText(v.toFixed(0), i * bw + bw / 2, h - padB - bh - 3);
    });
    if (state.savings._demo) { g.textAlign = 'left'; g.fillStyle = '#8a9bab'; g.fillText('kr · simulerede dage (demo)', 2, 10); }
  }

  // ---------- Indstillinger ----------
  function fillSelect(sel, list) {
    sel.innerHTML = '';
    list.forEach((d) => {
      const o = document.createElement('option');
      o.value = d.id;
      o.textContent = d.name + (d.status === 'klar' ? '' : ' (' + d.status + ')');
      sel.appendChild(o);
    });
  }

  function fillForm() {
    const f = $('settingsForm');
    Object.keys(DEFAULTS).forEach((k) => {
      const el = f.elements[k];
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!state.s[k]; else el.value = state.s[k];
    });
    $('quickReady').value = state.s.carReadyTime;
    $('quickNeed').value = state.s.carNeedKwh;
    driverNote();
  }

  function driverNote() {
    const f = $('settingsForm');
    const inv = EP.DRIVERS.inverter.find((d) => d.id === f.elements.inverter.value);
    const chg = EP.DRIVERS.charger.find((d) => d.id === f.elements.charger.value);
    $('driverNote').innerHTML = 'Inverter: ' + inv.transport + ' · ' + inv.status + '<br>Lader: ' + chg.transport + ' · ' + chg.status;
  }

  function readForm() {
    const f = $('settingsForm');
    const s = Object.assign({}, state.s);
    Object.keys(DEFAULTS).forEach((k) => {
      const el = f.elements[k];
      if (!el) return;
      if (el.type === 'checkbox') s[k] = el.checked;
      else if (el.type === 'number') { const v = parseFloat(el.value); s[k] = isNaN(v) ? DEFAULTS[k] : v; }
      else s[k] = el.value;
    });
    return s;
  }

  function saveSettings() {
    const copy = Object.assign({}, state.s, { chargeNow: false });
    store.set('settings', copy);
  }

  // ---------- Hændelser ----------
  function bind() {
    document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => {
      document.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('active', x === b));
      document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + b.dataset.view));
      window.scrollTo(0, 0);
      render();
    }));

    const toggleMode = async () => {
      if (state.s.mode === 'aktiv') {
        state.s.mode = 'skygge';
        toast('Tilbage i skyggetilstand – appen styrer ikke noget.');
      } else {
        state.s.mode = 'aktiv';
        toast(state.s.inverter === 'demo' ? 'Styring slået til (demo).' : 'Sender planen til dine enheder…');
        await sendPlan();
      }
      saveSettings();
      render();
    };
    $('btnMode').addEventListener('click', toggleMode);
    $('modeBadge').addEventListener('click', toggleMode);

    $('btnChargeNow').addEventListener('click', async () => {
      state.s.chargeNow = !state.s.chargeNow;
      try { await charger.chargeNow(state.s.chargeNow); } catch (e) { toast(e.message); }
      replan();
    });

    $('quickReady').addEventListener('change', (e) => { state.s.carReadyTime = e.target.value || '07:00'; saveSettings(); replan(); });
    $('quickNeed').addEventListener('change', (e) => { state.s.carNeedKwh = parseFloat(e.target.value) || 0; saveSettings(); replan(); });

    const f = $('settingsForm');
    f.elements.inverter.addEventListener('change', driverNote);
    f.elements.charger.addEventListener('change', driverNote);
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const prevArea = state.s.priceArea, prevLat = state.s.lat, prevLon = state.s.lon;
      state.s = readForm();
      saveSettings();
      makeDrivers();
      toast('Gemt');
      if (prevArea !== state.s.priceArea || prevLat !== state.s.lat || prevLon !== state.s.lon) await loadMarket();
      else replan();
    });
    $('btnReset').addEventListener('click', () => {
      state.s = Object.assign({}, DEFAULTS);
      state.savings = {}; state.history = {}; state.sim = { soc: 45 };
      store.set('savings', {}); store.set('history', {}); store.set('sim', state.sim);
      saveSettings(); makeDrivers(); fillForm(); seedDemoHistory(); loadMarket();
      toast('Nulstillet');
    });

    const cv = $('planChart');
    cv.addEventListener('click', (e) => {
      const gm = cv._geom;
      if (!gm) return;
      const r = cv.getBoundingClientRect();
      showSlot(Math.floor((e.clientX - r.left - gm.padL) / gm.bw));
    });
    window.addEventListener('resize', () => { renderPlanChart(); renderSavings(); });
  }

  // ---------- Start ----------
  function start() {
    fillSelect($('selInverter'), EP.DRIVERS.inverter);
    fillSelect($('selCharger'), EP.DRIVERS.charger);
    fillForm();
    makeDrivers();
    bind();
    seedDemoHistory();
    $('aboutBox').innerHTML = 'Energipilot · selvstændig app (uden Home Assistant).<br>' +
      'Priser: Energi Data Service · Vejr: Open-Meteo. Data gemmes kun på denne enhed.';
    loadMarket().then(tick);
    setInterval(tick, 10000);
    setInterval(() => {
      const m = state.market;
      if (!m) return;
      const age = Date.now() - m.loadedAt.getTime();
      const now = new Date();
      // Hent igen hver time, og lige efter kl. 13 når morgendagens priser kommer
      if (age > 3600000 || (now.getHours() === 13 && now.getMinutes() >= 15 && m.loadedAt.getHours() < 13)) loadMarket();
    }, 60000);
  }

  start();
})();
