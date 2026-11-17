/*
 * Energipilot – offentlige datakilder
 * Spotpriser: Energi Data Service (DayAheadPrices, kvarterspriser).
 * Vejr og sol: Open-Meteo.
 * Hvis hentning ikke er mulig (fx i en forhåndsvisning uden netadgang), bruges demodata.
 */
(function (root) {
  const EP = root.EP = root.EP || {};

  async function getJson(url) {
    // I den native app (Capacitor) bruges native HTTP, så CORS ikke er et problem.
    const cap = root.Capacitor;
    if (cap && cap.isNativePlatform && cap.isNativePlatform() && cap.Plugins && cap.Plugins.CapacitorHttp) {
      const r = await cap.Plugins.CapacitorHttp.get({ url });
      if (r.status >= 400) throw new Error('HTTP ' + r.status);
      return typeof r.data === 'string' ? JSON.parse(r.data) : r.data;
    }
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = setTimeout(() => ctrl && ctrl.abort(), 12000);
    try {
      const r = await fetch(url, ctrl ? { signal: ctrl.signal } : undefined);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } finally {
      clearTimeout(timer);
    }
  }
  EP.getJson = getJson;

  /** Spotpriser for i dag og i morgen (i morgen kommer ca. kl. 13). */
  EP.fetchPrices = async function (area) {
    const filter = encodeURIComponent(JSON.stringify({ PriceArea: [area] }));
    const url = 'https://api.energidataservice.dk/dataset/DayAheadPrices' +
      '?start=StartOfDay&end=StartOfDay%2BP2D&filter=' + filter +
      '&columns=TimeUTC,DayAheadPriceDKK&sort=TimeUTC%20asc&limit=500';
    const data = await getJson(url);
    const recs = (data.records || [])
      .filter((r) => r.DayAheadPriceDKK != null)
      .map((r) => ({
        t: new Date(String(r.TimeUTC).endsWith('Z') ? r.TimeUTC : r.TimeUTC + 'Z'),
        spot: r.DayAheadPriceDKK / 1000, // DKK/MWh → DKK/kWh
      }));
    if (recs.length < 8) throw new Error('Ingen priser modtaget');
    return { slots: EP.toQuarterSlots(recs), source: 'Energi Data Service' };
  };

  /** Indstråling pr. kvarter de næste to døgn. */
  EP.fetchRadiation = async function (lat, lon) {
    const url = 'https://api.open-meteo.com/v1/forecast?latitude=' + lat + '&longitude=' + lon +
      '&minutely_15=shortwave_radiation,temperature_2m&forecast_days=3&timezone=auto';
    const data = await getJson(url);
    const m = data.minutely_15;
    if (!m || !m.time) throw new Error('Ingen vejrdata');
    const map = new Map();
    m.time.forEach((t, i) => map.set(new Date(t).getTime(), m.shortwave_radiation[i] || 0));
    return { map, source: 'Open-Meteo' };
  };

  /** Henter alt, og falder tilbage til demodata hvis det ikke kan lade sig gøre. */
  EP.loadMarketData = async function (s, now) {
    let prices, rad, demo = false, errors = [];
    try {
      prices = await EP.fetchPrices(s.priceArea);
    } catch (e) {
      errors.push('Priser: ' + e.message);
      prices = { slots: EP.demoPrices(now, now.getHours() >= 13 ? 2 : 1), source: 'Demodata' };
      demo = true;
    }
    try {
      rad = await EP.fetchRadiation(s.lat, s.lon);
    } catch (e) {
      errors.push('Vejr: ' + e.message);
      rad = { map: null, source: 'Demodata' };
      demo = true;
    }
    // Kun kvarterer fra det igangværende og frem
    const cur = new Date(now);
    cur.setMinutes(Math.floor(cur.getMinutes() / 15) * 15, 0, 0);
    const slots = EP.extendWithEstimate(prices.slots, now).filter((sl) => sl.t >= cur);
    const radiation = slots.map((sl) => (rad.map ? (rad.map.get(sl.t.getTime()) || 0) : EP.demoRadiation(sl.t)));
    return { slots, radiation, demo, errors, priceSource: prices.source, weatherSource: rad.source };
  };
})(typeof window !== 'undefined' ? window : globalThis);
