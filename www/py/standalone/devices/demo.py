"""The demo's world: sun, house and prices - and a price feed like Energi Data Service's integration.
The simulated inverter (behind SolarAssistant) and charger are in simulators.py and are reached through the app's
real drivers."""

from __future__ import annotations

import math
import random
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .base import Device

TZ = ZoneInfo("Europe/Copenhagen")


def _day_rng(d: datetime, salt: int) -> random.Random:
    return random.Random(d.year * 10000 + d.month * 100 + d.day + salt * 7919)


class World:
    """The weather, the house and the prices - shared by the simulated devices."""

    def __init__(self, pv_kwp: float = 8.0) -> None:
        self.pv_kwp = pv_kwp

    def sun_kw(self, t: datetime) -> float:
        """Solar power from the sun's height for Copenhagen and a cloudiness that changes over the day."""
        doy = t.timetuple().tm_yday
        decl = 23.44 * math.sin(math.radians(360 / 365 * (doy - 81)))
        lat = 55.68
        hour = t.hour + t.minute / 60 + (t.utcoffset() or timedelta()).total_seconds() / -3600 + 12.57 / 15  # solar time
        ha = math.radians(15 * (hour - 12))
        el = math.asin(math.sin(math.radians(lat)) * math.sin(math.radians(decl))
                       + math.cos(math.radians(lat)) * math.cos(math.radians(decl)) * math.cos(ha))
        if el <= 0:
            return 0.0
        rng = _day_rng(t, 1)
        base_cloud = 0.25 + rng.random() * 0.7
        wobble = 0.15 * math.sin(t.hour * 1.3 + rng.random() * 6)
        clear = max(0.0, base_cloud + wobble)
        return round(self.pv_kwp * 0.85 * math.sin(el) ** 1.2 * min(1.0, clear), 3)

    def house_kw(self, t: datetime) -> float:
        h = t.hour + t.minute / 60
        weekend = t.weekday() >= 5
        base = 0.32
        morning = (0.6 if weekend else 0.9) * math.exp(-((h - (9 if weekend else 7)) / 1.3) ** 2)
        evening = 1.5 * math.exp(-((h - 18.3) / 1.7) ** 2)
        day = (0.5 if weekend else 0.15) * math.exp(-((h - 13) / 3) ** 2)
        rng = random.Random(int(t.timestamp() // 300))
        spikes = 1.8 if rng.random() < 0.04 else 0.0  # kettle, oven …
        return round(base + morning + evening + day + spikes + rng.random() * 0.08, 3)

    def spot(self, t: datetime) -> float:
        """Spot price DKK/kWh for a November day in DK2, the same for a given quarter hour."""
        rng = _day_rng(t, 2)
        wind = 0.6 + rng.random() * 0.8
        h = t.hour + t.minute / 60
        p = 0.45 + 0.55 * math.exp(-((h - 8) / 1.5) ** 2) + 0.25 * math.exp(-((h - 12.5) / 3) ** 2) \
            + 1.1 * math.exp(-((h - 18) / 1.6) ** 2) - 0.2 * math.exp(-((h - 3) / 2) ** 2)
        q = random.Random(int(t.timestamp() // 900))
        return round(p / wind + (q.random() - 0.5) * 0.08, 4)

    def price(self, t: datetime) -> float:
        """What the household pays (spot + net tariff + fees, with VAT) - like Energi Data Service's total price."""
        h = t.hour
        tariff = 1.05 if 17 <= h < 21 else 0.15 if h < 6 else 0.45
        return round((self.spot(t) + tariff + 0.008 + 0.16) * 1.25, 4)


class EnergiDataService(Device):
    """Prices like the Energi Data Service integration: the total price now, today and (after 13:00) tomorrow."""

    platform = "energidataservice"
    manufacturer = "Energi Data Service"
    model = "Prices"

    def __init__(self, world: World) -> None:
        super().__init__("eds_demo", "Energi Data Service")
        self.world = world
        self.e = self.entity("sensor.energi_data_service", "Price", "kr/kWh", "monetary", None)
        self._key = ""

    async def refresh(self, dt: float) -> None:
        t = datetime.now(TZ)
        key = f"{t:%Y%m%d%H}{t.minute // 15}"
        if key == self._key:
            return
        self._key = key
        day0 = t.replace(hour=0, minute=0, second=0, microsecond=0)

        def day(d0: datetime) -> list[dict[str, Any]]:
            out = []
            for q in range(96):
                s = d0 + timedelta(minutes=15 * q)
                out.append({"hour": s.isoformat(), "price": self.world.price(s)})
            return out

        today = day(day0)
        tomorrow = day(day0 + timedelta(days=1)) if t.hour >= 13 else []
        self.set(self.e, self.world.price(t.replace(minute=t.minute // 15 * 15, second=0, microsecond=0)),
                 raw_today=today, raw_tomorrow=tomorrow, today_min=min(x["price"] for x in today),
                 today_max=max(x["price"] for x in today), tomorrow_valid=bool(tomorrow))


def seed_history(ha: Any, world: World, days: int = 21) -> None:
    """Three weeks of hourly history for the demo house, so learning has something to learn from from the start."""
    rec = ha.recorder
    if rec.hours.get("sensor.growatt_sph_load_power"):
        return
    now = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    soc = 50.0
    for i in range(days * 24, 0, -1):
        t = now - timedelta(hours=i)
        pv = sum(world.sun_kw(t + timedelta(minutes=m)) for m in (0, 15, 30, 45)) / 4
        house = sum(world.house_kw(t + timedelta(minutes=m)) for m in (5, 20, 35, 50)) / 4
        car = 3.7 if (t.hour in (1, 2) and t.weekday() < 5) else 0.0
        use = house + car
        b = max(-3.6, min(3.6, pv - use))
        if soc >= 100 and b > 0 or soc <= 10 and b < 0:
            b = 0.0
        soc = max(10.0, min(100.0, soc + b / 10 * 100))
        grid = use - pv + b
        ts = t.timestamp()
        for e, v in (("sensor.growatt_sph_pv_power", pv * 1000), ("sensor.growatt_sph_load_power", use * 1000),
                     ("sensor.growatt_sph_battery_power", b * 1000), ("sensor.growatt_sph_grid_power", grid * 1000),
                     ("sensor.growatt_sph_battery_state_of_charge", soc), ("sensor.keba_p30_charging_power", car)):
            rec.seed_hour(e, ts, v)
    rec.save()
