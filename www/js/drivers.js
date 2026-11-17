/*
 * Energipilot – drivere
 *
 * Planlæggeren kender kun fælles begreber ("lad batteriet med X kW", "lad bilen nu").
 * Hver inverter og lader har sin egen driver, der oversætter til producentens API.
 * Nye mærker tilføjes ved at registrere en driver her – resten af appen ændres ikke.
 *
 * Fælles interface:
 *   inverter: getLive() → { pvW, loadW, battW, gridW, soc }      (battW + = lader)
 *             applyPlan(plan)  – sender tidsplan/kommando til enheden
 *   lader:    getLive() → { carW, connected, sessionKwh }
 *             applyPlan(plan), chargeNow(on)
 */
(function (root) {
  const EP = root.EP = root.EP || {};

  const NOT_READY = 'Driveren er ikke færdig endnu. Brug demo-tilstand indtil videre.';

  /** Fælles katalog over understøttede enheder. */
  EP.DRIVERS = {
    inverter: [
      { id: 'demo', name: 'Demo (simuleret hus)', status: 'klar', transport: 'simuleret' },
      { id: 'growatt', name: 'Growatt SPH / MIN / MOD', status: 'under udvikling', transport: 'cloud (Growatt OpenAPI)' },
      { id: 'foxess', name: 'Fox ESS', status: 'planlagt', transport: 'cloud (Fox ESS OpenAPI)' },
      { id: 'givenergy', name: 'GivEnergy', status: 'planlagt', transport: 'cloud (GivEnergy API)' },
      { id: 'deye', name: 'Deye', status: 'planlagt', transport: 'cloud (DeyeCloud)' },
      { id: 'sungrow', name: 'Sungrow', status: 'planlagt', transport: 'cloud (iSolarCloud)' },
      { id: 'huawei', name: 'Huawei', status: 'planlagt', transport: 'cloud / lokal Modbus' },
      { id: 'solax', name: 'SolaX', status: 'planlagt', transport: 'lokal Modbus TCP' },
      { id: 'solaredge', name: 'SolarEdge', status: 'planlagt', transport: 'lokal Modbus TCP' },
    ],
    charger: [
      { id: 'demo', name: 'Demo (simuleret elbil)', status: 'klar', transport: 'simuleret' },
      { id: 'keba', name: 'KEBA KeContact', status: 'under udvikling', transport: 'lokal UDP (port 7090)' },
      { id: 'easee', name: 'Easee', status: 'planlagt', transport: 'cloud' },
      { id: 'zaptec', name: 'Zaptec', status: 'planlagt', transport: 'cloud' },
      { id: 'goe', name: 'go-e', status: 'planlagt', transport: 'lokal HTTP' },
      { id: 'wallbox', name: 'Wallbox', status: 'planlagt', transport: 'cloud' },
      { id: 'none', name: 'Ingen lader', status: 'klar', transport: '–' },
    ],
  };

  // ---------- Demo: et simuleret hus, der følger planen ----------

  class DemoInverter {
    constructor(state) { this.state = state; }
    async getLive(ctx) {
      const st = this.state;
      const i = ctx.slotIndex;
      const p = ctx.plan;
      const noise = () => 1 + (Math.random() - 0.5) * 0.15;
      const loadKw = p && p.load[i] != null ? p.load[i] * noise() : 0.6;
      const pvKw = p && p.pv[i] != null ? p.pv[i] * noise() : 0;
      let battKw = 0;
      if (ctx.mode === 'aktiv' && p && p.batt[i] != null) battKw = p.batt[i];
      else {
        // Skyggetilstand: batteriet kører "selvforbrug" som de fleste invertere gør som standard
        const netKw = loadKw - pvKw;
        battKw = Math.max(-ctx.s.battPowerKw, Math.min(ctx.s.battPowerKw, -netKw));
      }
      // Opdatér simuleret SOC
      const dtH = ctx.dtSec / 3600;
      const cap = ctx.s.battKwh || 1;
      const se = Math.sqrt(ctx.s.battEff);
      let soc = st.soc;
      const dE = battKw > 0 ? battKw * dtH * se : battKw * dtH / se;
      soc = soc + (dE / cap) * 100;
      if (soc > ctx.s.battMaxSoc && battKw > 0) { soc = ctx.s.battMaxSoc; battKw = 0; }
      if (soc < ctx.s.battMinSoc && battKw < 0) { soc = ctx.s.battMinSoc; battKw = 0; }
      st.soc = Math.max(0, Math.min(100, soc));
      const carKw = ctx.carKw || 0;
      return {
        pvW: pvKw * 1000, loadW: loadKw * 1000, battW: battKw * 1000,
        gridW: (loadKw + carKw - pvKw + battKw) * 1000, soc: st.soc,
      };
    }
    async applyPlan() { return { ok: true, note: 'Demo: planen er "sendt" til det simulerede batteri.' }; }
  }

  class DemoCharger {
    constructor(state) { this.state = state; }
    async getLive(ctx) {
      const i = ctx.slotIndex;
      const p = ctx.plan;
      let carKw = 0;
      if (ctx.mode === 'aktiv' || ctx.s.chargeNow) carKw = p && p.car[i] ? p.car[i] : 0;
      this.state.sessionKwh = (this.state.sessionKwh || 0) + carKw * ctx.dtSec / 3600;
      return { carW: carKw * 1000, connected: true, sessionKwh: this.state.sessionKwh };
    }
    async applyPlan() { return { ok: true, note: 'Demo: ladeplanen er "sendt" til den simulerede lader.' }; }
    async chargeNow() { return { ok: true }; }
  }

  // ---------- Rigtige drivere (skeletter) ----------

  /**
   * Growatt: logger ind på Growatts OpenAPI med brugerens token og sætter
   * tidsrum for opladning ("battery first") og afladning ("grid first").
   * SPH har tre tidsrum til hver, så planen komprimeres til de tre vigtigste blokke.
   */
  class GrowattDriver {
    constructor(cfg) { this.cfg = cfg; }
    async getLive() { throw new Error(NOT_READY); }
    async applyPlan(plan) {
      const blocks = EP.compressToBlocks(plan, 3);
      throw new Error(NOT_READY + ' (ville sende ' + blocks.charge.length + ' lade- og ' +
        blocks.discharge.length + ' afladeblokke)');
    }
  }

  /**
   * KEBA: lokal UDP på port 7090. "report 2/3" giver status og effekt,
   * "curr <mA>" sætter strøm, "ena 0|1" slår ladning til/fra.
   * Kræver den native app (UDP er ikke muligt i en browser) og samme wifi som laderen.
   */
  class KebaDriver {
    constructor(cfg) { this.cfg = cfg; }
    async getLive() { throw new Error(NOT_READY); }
    async applyPlan() { throw new Error(NOT_READY); }
    async chargeNow() { throw new Error(NOT_READY); }
  }

  class NotImplemented {
    async getLive() { throw new Error(NOT_READY); }
    async applyPlan() { throw new Error(NOT_READY); }
    async chargeNow() { throw new Error(NOT_READY); }
  }

  class NoCharger {
    async getLive() { return { carW: 0, connected: false, sessionKwh: 0 }; }
    async applyPlan() { return { ok: true }; }
    async chargeNow() { return { ok: true }; }
  }

  EP.createInverter = function (id, state, cfg) {
    if (id === 'demo') return new DemoInverter(state);
    if (id === 'growatt') return new GrowattDriver(cfg);
    return new NotImplemented();
  };

  EP.createCharger = function (id, state, cfg) {
    if (id === 'demo') return new DemoCharger(state);
    if (id === 'keba') return new KebaDriver(cfg);
    if (id === 'none') return new NoCharger();
    return new NotImplemented();
  };

  /**
   * Komprimerer en kvartersplan til højst N sammenhængende lade- og afladeblokke
   * (til invertere, der kun kan gemme et fast antal tidsrum). De største blokke vinder.
   */
  EP.compressToBlocks = function (plan, maxBlocks) {
    const blocks = { charge: [], discharge: [] };
    let cur = null;
    plan.action.forEach((a, i) => {
      const kind = a.startsWith('Lad') ? 'charge' : a === 'Aflad' ? 'discharge' : null;
      if (cur && cur.kind === kind) { cur.end = i + 1; cur.energy += Math.abs(plan.batt[i]); return; }
      if (cur && cur.kind) blocks[cur.kind].push(cur);
      cur = kind ? { kind, start: i, end: i + 1, energy: Math.abs(plan.batt[i]) } : null;
    });
    if (cur && cur.kind) blocks[cur.kind].push(cur);
    const top = (arr) => arr.sort((a, b) => b.energy - a.energy).slice(0, maxBlocks).sort((a, b) => a.start - b.start)
      .map((b) => ({ from: plan.slots[b.start].t, to: new Date(plan.slots[b.end - 1].t.getTime() + 15 * 60000) }));
    return { charge: top(blocks.charge), discharge: top(blocks.discharge) };
  };
})(typeof window !== 'undefined' ? window : globalThis);
