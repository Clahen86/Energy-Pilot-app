const test = require('node:test');
const assert = require('node:assert');
require('../www/js/planner.js');
const EP = globalThis.EP;

const S = {
  tariffLow: 0.15, tariffMid: 0.45, tariffPeak: 1.05, elafgift: 0.008, otherFees: 0.16, vat: 0.25, sellFee: 0.05,
  pvEnabled: true, pvKwp: 8, pvPerformance: 0.8, battEnabled: true, battKwh: 10, battPowerKw: 5, battEff: 0.9,
  battMinSoc: 10, battMaxSoc: 100, allowExport: false, carEnabled: true, carNeedKwh: 20, carPowerKw: 11,
  carReadyTime: '07:00', chargeNow: false,
};

function plan(over = {}, now = new Date('2026-11-17T14:00:00')) {
  const s = Object.assign({}, S, over);
  const slots = EP.demoPrices(now, 2).filter((x) => x.t >= now);
  const radiation = slots.map((x) => EP.demoRadiation(x.t));
  return EP.makePlan({ slots, radiation, history: {}, settings: s, socPct: 40, now });
}

test('batteriet sparer penge og respekterer grænser', () => {
  const p = plan();
  assert.ok(p.saving > 0, 'skal spare noget');
  for (const v of p.soc) assert.ok(v >= 0 && v <= 100);
  for (const b of p.batt) assert.ok(Math.abs(b) <= S.battPowerKw + 1e-6);
});

test('bilen bliver ladet før klar-tidspunktet', () => {
  const p = plan();
  assert.ok(p.carUnmet < 0.01);
  p.car.forEach((kw, i) => { if (kw > 0) assert.ok(p.slots[i].t < p.carDeadline); });
  const kwh = p.car.reduce((a, v) => a + v * EP.SLOT_H, 0);
  assert.ok(Math.abs(kwh - S.carNeedKwh) < 0.01);
});

test('uden batteri er der ingen besparelse', () => {
  const p = plan({ battEnabled: false });
  assert.ok(Math.abs(p.saving) < 1e-9);
});

test('batteriet sælger ikke til nettet når det ikke er tilladt', () => {
  const p = plan();
  p.grid.forEach((g, i) => {
    const before = p.load[i] + p.car[i] - p.pv[i];
    if (p.batt[i] < 0) assert.ok(g >= Math.min(0, before) - 1e-6);
  });
});

test('timepriser udvides til kvarterer', () => {
  const t0 = new Date('2026-11-17T00:00:00Z');
  const q = EP.toQuarterSlots([{ t: t0, spot: 1 }, { t: new Date(t0.getTime() + 3600000), spot: 2 }]);
  assert.strictEqual(q.length, 5);
  assert.strictEqual(q[3].spot, 1);
});

test('morgendagen estimeres før kl. 13', () => {
  const now = new Date('2026-11-17T08:00:00');
  const ext = EP.extendWithEstimate(EP.demoPrices(now, 1), now);
  assert.strictEqual(ext.length, 192);
  assert.ok(ext[150].est);
});
