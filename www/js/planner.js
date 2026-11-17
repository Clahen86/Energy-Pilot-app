/*
 * Energipilot – planlægger
 * Ren logik uden afhængigheder: priser, prognoser og optimering af batteri og bil
 * i kvarterstimer (15 min). Kører både i browseren og i Node (til tests).
 */
(function (root) {
  const EP = root.EP = root.EP || {};
  const SLOT_H = 0.25; // 15 minutter
  EP.SLOT_H = SLOT_H;

  // ---------- Priser ----------

  /** Nettarif efter tidspunkt (DKK/kWh ekskl. moms). Lav 0-6, spids 17-21, ellers mellem. */
  EP.tariffFor = function (date, s) {
    const h = date.getHours();
    if (h >= 17 && h < 21) return s.tariffPeak;
    if (h < 6) return s.tariffLow;
    return s.tariffMid;
  };

  /** Pris for at købe 1 kWh i et kvarter, inkl. afgifter og moms (DKK/kWh). */
  EP.buyPrice = function (slot, s) {
    return (slot.spot + EP.tariffFor(slot.t, s) + s.elafgift + s.otherFees) * (1 + s.vat);
  };

  /** Pris for at sælge 1 kWh (DKK/kWh). */
  EP.sellPrice = function (slot, s) {
    return slot.spot - s.sellFee;
  };

  // ---------- Hjælpere ----------

  EP.zeros = (n) => new Array(n).fill(0);

  /** Simpel deterministisk tilfældighed, så demodata er ens for samme dato. */
  EP.seeded = function (seed) {
    let x = seed >>> 0 || 1;
    return function () {
      x ^= x << 13; x >>>= 0;
      x ^= x >>> 17;
      x ^= x << 5; x >>>= 0;
      return x / 4294967296;
    };
  };

  EP.startOfDay = function (d) {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    return x;
  };

  /** Udvider prisrækker med 60- eller 30-minutters opløsning til 15-minutters slots. */
  EP.toQuarterSlots = function (records) {
    // records: [{t: Date, spot: DKK/kWh}] sorteret
    const out = [];
    for (let i = 0; i < records.length; i++) {
      const cur = records[i];
      const next = records[i + 1];
      let mins = next ? Math.round((next.t - cur.t) / 60000) : 15;
      if (mins <= 0 || mins > 60) mins = 15;
      for (let m = 0; m < mins; m += 15) {
        out.push({ t: new Date(cur.t.getTime() + m * 60000), spot: cur.spot });
      }
    }
    return out;
  };

  /**
   * Før morgendagens priser er offentliggjort (ca. kl. 13): forlæng med et estimat
   * (samme kvarter dagen før), så bil og batteri kan planlægges hen over natten.
   * Estimerede kvarterer markeres med est: true og erstattes, når de rigtige priser kommer.
   */
  EP.extendWithEstimate = function (slots, now) {
    if (!slots.length) return slots;
    const end = EP.startOfDay(now).getTime() + 2 * 86400000;
    const out = slots.slice();
    let last = out[out.length - 1].t.getTime();
    while (last + 15 * 60000 < end) {
      const t = last + 15 * 60000;
      const src = out.find((x) => x.t.getTime() === t - 86400000);
      if (!src) break;
      out.push({ t: new Date(t), spot: src.spot, est: true });
      last = t;
    }
    return out;
  };

  // ---------- Prognoser ----------

  /** Standardprofil for et hus (kW) hvis der endnu ikke er lært noget. */
  EP.defaultLoadKw = function (date) {
    const h = date.getHours() + date.getMinutes() / 60;
    const base = 0.35;
    const morning = 0.9 * Math.exp(-Math.pow((h - 7.5) / 1.2, 2));
    const evening = 1.6 * Math.exp(-Math.pow((h - 18.5) / 1.8, 2));
    const day = 0.25 * Math.exp(-Math.pow((h - 13) / 3, 2));
    return base + morning + evening + day;
  };

  /**
   * Forbrugsprognose: gennemsnit pr. ugedag og time fra historikken (lærer fra dag ét),
   * blandet med standardprofilen indtil der er nok målinger.
   */
  EP.loadForecast = function (slots, history) {
    return slots.map((sl) => {
      const key = sl.t.getDay() + '-' + sl.t.getHours();
      const h = history && history[key];
      const def = EP.defaultLoadKw(sl.t);
      if (!h || !h.n) return def;
      const w = Math.min(1, h.n / 8); // fuld tillid efter 8 målinger
      return w * h.avg + (1 - w) * def;
    });
  };

  /** Opdaterer historikken med én måling (kW) – glidende gennemsnit. */
  EP.learnLoad = function (history, date, kw) {
    const key = date.getDay() + '-' + date.getHours();
    const h = history[key] || { avg: kw, n: 0 };
    const alpha = Math.max(0.05, 1 / (h.n + 1));
    h.avg = h.avg + alpha * (kw - h.avg);
    h.n = Math.min(h.n + 1, 1000);
    history[key] = h;
    return history;
  };

  /** Solprognose (kW) ud fra indstråling (W/m²) og anlæggets størrelse. */
  EP.pvFromRadiation = function (radWm2, s) {
    return Math.max(0, (radWm2 / 1000) * s.pvKwp * s.pvPerformance * (s.pvLearnedFactor || 1));
  };

  // ---------- Demodata ----------

  /** Realistiske spotpriser for DK i november (DKK/kWh), deterministisk pr. dato. */
  EP.demoPrices = function (fromDate, days) {
    const out = [];
    const start = EP.startOfDay(fromDate);
    for (let d = 0; d < days; d++) {
      const day = new Date(start.getTime() + d * 86400000);
      const rnd = EP.seeded(day.getFullYear() * 1000 + day.getMonth() * 40 + day.getDate());
      const wind = 0.6 + rnd() * 0.8; // blæsende dage er billigere
      for (let q = 0; q < 96; q++) {
        const t = new Date(day.getTime() + q * 15 * 60000);
        const h = q / 4;
        let p = 0.45;
        p += 0.55 * Math.exp(-Math.pow((h - 8) / 1.5, 2));
        p += 0.25 * Math.exp(-Math.pow((h - 12.5) / 3, 2));
        p += 1.1 * Math.exp(-Math.pow((h - 18) / 1.6, 2));
        p -= 0.2 * Math.exp(-Math.pow((h - 3) / 2, 2));
        p = p / wind + (rnd() - 0.5) * 0.08;
        out.push({ t, spot: Math.round(p * 1000) / 1000 });
      }
    }
    return out;
  };

  /** Demo-indstråling for november i Danmark (W/m²). */
  EP.demoRadiation = function (date) {
    const h = date.getHours() + date.getMinutes() / 60;
    const day = EP.startOfDay(date);
    const rnd = EP.seeded(day.getDate() * 7919 + day.getMonth());
    const cloud = 0.35 + rnd() * 0.6;
    if (h < 8 || h > 16) return 0;
    return Math.max(0, 260 * Math.sin(((h - 8) / 8) * Math.PI) * cloud);
  };

  // ---------- Optimering ----------

  /** Første tidspunkt efter "from" hvor klokken er readyTime ("HH:MM"). */
  EP.nextReadyTime = function (from, readyTime) {
    const [hh, mm] = String(readyTime || '07:00').split(':').map(Number);
    const r = new Date(from);
    r.setHours(hh, mm || 0, 0, 0);
    if (r <= from) r.setDate(r.getDate() + 1);
    return r;
  };

  /**
   * Bil: vælger de billigste kvarterer før klar-tidspunktet. Solcelleoverskud tæller
   * som salgsprisen (det man ellers ville få for strømmen).
   */
  EP.planCar = function (slots, load, pv, s, now) {
    const n = slots.length;
    const car = EP.zeros(n);
    if (!s.carEnabled || !(s.carNeedKwh > 0) || !n) return { car, deadline: null, unmet: 0 };
    const perSlot = s.carPowerKw * SLOT_H;
    let need = s.carNeedKwh;

    // "Lad nu": fuld fart fra nu af, indtil behovet er dækket
    if (s.chargeNow) {
      for (let i = 0; i < n && need > 0; i++) {
        const e = Math.min(perSlot, need);
        car[i] = e / SLOT_H;
        need -= e;
      }
      return { car, deadline: null, unmet: Math.max(0, need) };
    }

    const deadline = EP.nextReadyTime(now || slots[0].t, s.carReadyTime);
    const cands = [];
    for (let i = 0; i < n; i++) {
      if (slots[i].t >= deadline) break;
      const surplus = Math.max(0, pv[i] - load[i]);
      const frac = Math.min(1, surplus / s.carPowerKw);
      const cost = frac * EP.sellPrice(slots[i], s) + (1 - frac) * EP.buyPrice(slots[i], s);
      cands.push({ i, cost });
    }
    cands.sort((a, b) => a.cost - b.cost || a.i - b.i);
    for (const c of cands) {
      if (need <= 0) break;
      const e = Math.min(perSlot, need);
      car[c.i] = e / SLOT_H;
      need -= e;
    }
    return { car, deadline, unmet: Math.max(0, need) };
  };

  /** Omkostning for ét kvarter givet nettet energi (kWh, + = køb). */
  function slotCost(gridKwh, slot, s) {
    return gridKwh > 0 ? gridKwh * EP.buyPrice(slot, s) : gridKwh * EP.sellPrice(slot, s);
  }

  /**
   * Batteri: dynamisk programmering over ladetilstand (SOC). Finder den billigste
   * rækkefølge af opladning/afladning for alle kendte kvarterer.
   */
  EP.planBattery = function (slots, load, pv, car, s, socStartPct) {
    const n = slots.length;
    const cap = s.battKwh;
    const res = {
      batt: EP.zeros(n), soc: EP.zeros(n + 1), grid: EP.zeros(n), action: new Array(n).fill('Normal'),
    };
    const net = slots.map((_, i) => load[i] + car[i] - pv[i]); // kW før batteri
    if (!s.battEnabled || !(cap > 0)) {
      for (let i = 0; i < n; i++) res.grid[i] = net[i];
      res.soc.fill(0);
      return res;
    }

    const L = 60; // antal SOC-trin
    const step = cap / L;
    const se = Math.sqrt(s.battEff); // effektivitet hver vej
    const minL = Math.ceil((s.battMinSoc / 100) * L);
    const maxL = Math.floor((s.battMaxSoc / 100) * L);
    const upMax = Math.floor((s.battPowerKw * SLOT_H * se) / step + 1e-9);
    const downMax = Math.floor((s.battPowerKw * SLOT_H / se) / step + 1e-9);
    let l0 = Math.round((socStartPct / 100) * L);
    l0 = Math.min(L, Math.max(0, l0));

    // Værdi af strøm, der er tilbage i batteriet til sidst (så den ikke tømmes for sjov)
    const buys = slots.map((sl) => EP.buyPrice(sl, s)).sort((a, b) => a - b);
    const median = buys.length ? buys[Math.floor(buys.length / 2)] : 0;
    const endValue = median * se * 0.8;

    const INF = 1e18;
    let V = new Float64Array(L + 1);
    for (let l = 0; l <= L; l++) V[l] = -l * step * endValue;
    const choice = new Array(n);

    for (let t = n - 1; t >= 0; t--) {
      const nv = new Float64Array(L + 1).fill(INF);
      const ch = new Int16Array(L + 1);
      const baseKwh = net[t] * SLOT_H;
      for (let l = 0; l <= L; l++) {
        const lo = Math.max(Math.min(l, minL), l - downMax);
        const hi = Math.min(Math.max(l, maxL), l + upMax);
        for (let m = lo; m <= hi; m++) {
          const d = (m - l) * step; // ændring i lagret energi
          let g = baseKwh + (d > 0 ? d / se : d * se);
          // Aflad ikke til nettet, medmindre det er tilladt
          if (d < 0 && !s.allowExport && g < Math.min(0, baseKwh) - 1e-9) continue;
          const c = slotCost(g, slots[t], s) + V[m];
          if (c < nv[l]) { nv[l] = c; ch[l] = m; }
        }
      }
      V = nv;
      choice[t] = ch;
    }

    let l = l0;
    res.soc[0] = (l / L) * 100;
    for (let t = 0; t < n; t++) {
      const m = choice[t][l];
      const d = (m - l) * step;
      const battKw = (d > 0 ? d / se : d * se) / SLOT_H; // + = lader (fra husets side)
      res.batt[t] = battKw;
      res.grid[t] = net[t] + battKw;
      if (battKw > 0.05) res.action[t] = net[t] < 0 && res.grid[t] <= 0.05 ? 'Lad fra sol' : 'Lad fra net';
      else if (battKw < -0.05) res.action[t] = 'Aflad';
      else if (net[t] > 0.05 && l > minL) res.action[t] = 'Hold';
      else res.action[t] = 'Normal';
      l = m;
      res.soc[t + 1] = (l / L) * 100;
    }
    return res;
  };

  /**
   * Hele planen: prognose → bil → batteri → omkostninger og besparelse.
   */
  EP.makePlan = function (input) {
    const { slots, radiation, history, settings: s, socPct, now } = input;
    const load = EP.loadForecast(slots, history);
    const pv = slots.map((sl, i) => (s.pvEnabled ? EP.pvFromRadiation(radiation[i] || 0, s) : 0));
    const carPlan = EP.planCar(slots, load, pv, s, now);
    const bat = EP.planBattery(slots, load, pv, carPlan.car, s, socPct);

    let cost = 0, baseCost = 0, chargedKwh = 0, chargedCost = 0;
    const buy = [], sell = [];
    for (let i = 0; i < slots.length; i++) {
      buy.push(EP.buyPrice(slots[i], s));
      sell.push(EP.sellPrice(slots[i], s));
      const g = bat.grid[i] * SLOT_H;
      const g0 = (load[i] + carPlan.car[i] - pv[i]) * SLOT_H;
      cost += slotCost(g, slots[i], s);
      baseCost += slotCost(g0, slots[i], s);
      if (bat.batt[i] > 0) {
        const e = bat.batt[i] * SLOT_H;
        const fromGrid = Math.min(e, Math.max(0, bat.grid[i]) * SLOT_H);
        chargedKwh += e;
        chargedCost += fromGrid * buy[i] + (e - fromGrid) * sell[i];
      }
    }
    return {
      slots, load, pv, buy, sell,
      car: carPlan.car, carDeadline: carPlan.deadline, carUnmet: carPlan.unmet,
      batt: bat.batt, soc: bat.soc, grid: bat.grid, action: bat.action,
      cost, baseCost, saving: baseCost - cost,
      storedCostPerKwh: chargedKwh > 0 ? chargedCost / chargedKwh : 0,
    };
  };

  /** Summerer en del af planen (fx kun i dag). */
  EP.summarize = function (plan, from, to, s) {
    let cost = 0, base = 0;
    for (let i = 0; i < plan.slots.length; i++) {
      const t = plan.slots[i].t;
      if (t < from || t >= to) continue;
      cost += slotCost(plan.grid[i] * SLOT_H, plan.slots[i], s);
      base += slotCost((plan.load[i] + plan.car[i] - plan.pv[i]) * SLOT_H, plan.slots[i], s);
    }
    return { cost, base, saving: base - cost };
  };

  if (typeof module !== 'undefined') module.exports = EP;
})(typeof window !== 'undefined' ? window : globalThis);
