"""A simulated home for the demo: a Growatt SPH inverter with a battery and solar panels, a KEBA charger with a car,
and Energi Data Service prices. The entities are named exactly as the Home Assistant integrations name them
(growatt_local, keba, energidataservice), so Energy Pilot finds and controls them as it would in a real home.

The physics follow the inverter's own settings: work mode, stop levels, rates and time slots - so when Energy Pilot
takes control, the simulated battery does what a real SPH would do with those settings.
"""

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


SLOT_TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)] + ["23:59"]


class GrowattSPH(Device):
    """Growatt SPH as the growatt_local integration shows it (local Modbus)."""

    platform = "growatt_local"
    manufacturer = "Growatt"
    model = "SPH 6000TL3 BH-UP"

    def __init__(self, world: World, capacity_kwh: float = 10.0, max_kw: float = 5.0, soc: float = 45.0) -> None:
        super().__init__("growatt_demo", "Growatt")
        self.world, self.cap, self.max_kw, self.soc = world, capacity_kwh, max_kw, soc
        e = self.entity
        self.e_pv = e("sensor.growatt_pv_power", "PV power", "W", "power")
        self.e_batt = e("sensor.growatt_battery_power", "Battery power", "W", "power")
        self.e_soc = e("sensor.growatt_state_of_charge", "State of charge", "%", "battery")
        self.e_grid = e("sensor.growatt_grid_power", "Grid power", "W", "power")
        self.e_load = e("sensor.growatt_load_power", "Load power", "W", "power")
        self.e_capacity = e("sensor.growatt_battery_capacity", "Battery capacity", "kWh", "energy_storage", None)
        self.e_import = e("sensor.growatt_energy_from_grid", "Energy from grid", "kWh", "energy", "total_increasing")
        self.e_export = e("sensor.growatt_energy_to_grid", "Energy to grid", "kWh", "energy", "total_increasing")
        self.e_pv_total = e("sensor.growatt_pv_energy", "PV energy", "kWh", "energy", "total_increasing")
        self.e_bin = e("sensor.growatt_battery_charge_energy", "Battery charge energy", "kWh", "energy", "total_increasing")
        self.e_bout = e("sensor.growatt_battery_discharge_energy", "Battery discharge energy", "kWh", "energy", "total_increasing")
        # The controls (holding registers)
        modes = ["Load first", "Battery first", "Grid first"]
        self.controls: dict[str, Any] = {
            e("select.growatt_work_mode_priority", "Work mode priority", options=modes): "Load first",
            e("number.growatt_load_first_stop_discharge", "Load first stop discharge", "%", min=10, max=100, step=1): 10,
            e("number.growatt_load_first_stop_charge", "Load first stop charge", "%", min=10, max=100, step=1): 100,
            e("number.growatt_free_mode_discharge_rate", "Free mode discharge rate", "%", min=0, max=100, step=1): 100,
            e("number.growatt_free_mode_charge_rate", "Free mode charge rate", "%", min=0, max=100, step=1): 100,
            e("select.growatt_battery_first_grid_charge", "Battery first grid charge", options=["Enabled", "Disabled"]): "Disabled",
            e("switch.growatt_battery_first_slot_1_enabled", "Battery first slot 1 enabled"): "off",
            e("select.growatt_battery_first_slot_1_start", "Battery first slot 1 start", options=SLOT_TIMES): "00:00",
            e("select.growatt_battery_first_slot_1_end", "Battery first slot 1 end", options=SLOT_TIMES): "00:00",
            e("number.growatt_battery_first_charge_rate", "Battery first charge rate", "%", min=0, max=100, step=1): 100,
            e("number.growatt_battery_first_stop_charge", "Battery first stop charge", "%", min=10, max=100, step=1): 100,
            e("switch.growatt_grid_first_slot_1_enabled", "Grid first slot 1 enabled"): "off",
            e("select.growatt_grid_first_slot_1_start", "Grid first slot 1 start", options=SLOT_TIMES): "00:00",
            e("select.growatt_grid_first_slot_1_end", "Grid first slot 1 end", options=SLOT_TIMES): "00:00",
            e("number.growatt_grid_first_discharge_rate", "Grid first discharge rate", "%", min=0, max=100, step=1): 100,
            e("number.growatt_grid_first_stop_discharge", "Grid first stop discharge", "%", min=10, max=100, step=1): 10,
        }
        self.totals = {"imp": 1520.0, "exp": 830.0, "pv": 4120.0, "bin": 910.0, "bout": 840.0}
        self.car_kw = 0.0  # set by the charger: the house meter sees the car too

    def attach(self, ha: Any) -> None:
        super().attach(ha)
        for eid, v in self.controls.items():
            self.set(eid, v)

    async def write(self, entity_id: str, value: Any) -> None:
        if entity_id.startswith("number."):
            value = float(value)
            value = int(value) if value.is_integer() else value
        self.controls[entity_id] = value
        self.set(entity_id, value)

    def _c(self, short: str) -> Any:
        return self.controls[f"{short.split('.')[0]}.growatt_{short.split('.')[1]}"]

    def _in_slot(self, kind: str, t: datetime) -> bool:
        if self._c(f"switch.{kind}_first_slot_1_enabled") != "on":
            return False
        a, b = self._c(f"select.{kind}_first_slot_1_start"), self._c(f"select.{kind}_first_slot_1_end")
        now = f"{t.hour:02d}:{t.minute:02d}"
        return a <= now <= b if a <= b else now >= a or now <= b

    def battery_kw(self, t: datetime, pv: float, use: float) -> float:
        """What the battery does (+ charging) given the inverter's settings - the way an SPH behaves."""
        mode = self._c("select.work_mode_priority")
        surplus = pv - use
        lim = self.max_kw
        if mode == "Battery first" and self._in_slot("battery", t):
            stop = float(self._c("number.battery_first_stop_charge"))
            if self.soc >= stop:
                return max(0.0, min(surplus, lim)) if self.soc < 100 else 0.0
            rate = float(self._c("number.battery_first_charge_rate")) / 100 * lim
            grid_ok = self._c("select.battery_first_grid_charge") == "Enabled"
            return min(lim, rate if grid_ok else max(0.0, min(rate, surplus)))
        if mode == "Grid first" and self._in_slot("grid", t):
            stop = float(self._c("number.grid_first_stop_discharge"))
            if self.soc <= stop:
                return 0.0
            return -float(self._c("number.grid_first_discharge_rate")) / 100 * lim
        # Load first (self-use)
        if surplus >= 0:
            stop = float(self._c("number.load_first_stop_charge"))
            rate = float(self._c("number.free_mode_charge_rate")) / 100 * lim
            return min(surplus, rate) if self.soc < stop else 0.0
        stop = float(self._c("number.load_first_stop_discharge"))
        rate = float(self._c("number.free_mode_discharge_rate")) / 100 * lim
        return -min(-surplus, rate) if self.soc > stop else 0.0

    async def refresh(self, dt: float) -> None:
        t = datetime.now(TZ)
        pv = self.world.sun_kw(t)
        use = self.world.house_kw(t) + self.car_kw  # the inverter's CT sees the whole house, the car included
        b = self.battery_kw(t, pv, use)
        if self.soc >= 100 and b > 0 or self.soc <= 0 and b < 0:
            b = 0.0
        eff = 0.95
        h = dt / 3600
        self.soc = max(0.0, min(100.0, self.soc + (b * eff if b > 0 else b / eff) * h / self.cap * 100))
        grid = use - pv + b
        self.totals["imp"] += max(0.0, grid) * h
        self.totals["exp"] += max(0.0, -grid) * h
        self.totals["pv"] += pv * h
        self.totals["bin"] += max(0.0, b) * h
        self.totals["bout"] += max(0.0, -b) * h
        self.set(self.e_pv, round(pv * 1000))
        self.set(self.e_batt, round(b * 1000))
        self.set(self.e_soc, round(self.soc, 1))
        self.set(self.e_grid, round(grid * 1000))
        self.set(self.e_load, round(use * 1000))
        self.set(self.e_capacity, self.cap)
        for key, eid in (("imp", self.e_import), ("exp", self.e_export), ("pv", self.e_pv_total), ("bin", self.e_bin), ("bout", self.e_bout)):
            self.set(eid, round(self.totals[key], 3))

    def energy_prefs(self) -> list[dict[str, Any]]:
        return [
            {"type": "solar", "stat_energy_from": self.e_pv_total, "stat_rate": self.e_pv},
            {"type": "battery", "stat_energy_from": self.e_bout, "stat_energy_to": self.e_bin, "stat_rate": self.e_batt},
            {"type": "grid", "flow_from": [{"stat_energy_from": self.e_import, "entity_energy_price": None}],
             "flow_to": [{"stat_energy_to": self.e_export}], "stat_rate": self.e_grid},
        ]


class KebaP30(Device):
    """KEBA KeContact P30 as Home Assistant's keba integration shows it."""

    platform = "keba"
    manufacturer = "KEBA"
    model = "KeContact P30"

    def __init__(self, world: World, inverter: GrowattSPH | None = None, phases: int = 3) -> None:
        super().__init__("keba_demo", "KEBA")
        self.world, self.inverter, self.phases = world, inverter, phases
        e = self.entity
        self.e_power = e("sensor.keba_charging_power", "Charging power", "kW", "power")
        self.e_session = e("sensor.keba_session_energy", "Session energy", "kWh", "energy", "total_increasing")
        self.e_total = e("sensor.keba_total_energy", "Total energy", "kWh", "energy", "total_increasing")
        self.e_max = e("sensor.keba_max_current", "Max current", "A", "current")
        self.e_plug = e("binary_sensor.keba_plug", "Plug", device_class="plug")
        self.e_state = e("binary_sensor.keba_charging_state", "Charging state", device_class="power")
        self.e_enable = e("binary_sensor.keba_enable", "Enable")
        self.enabled = True
        self.current = 16.0
        self.session = 0.0
        self.total = 6230.0
        self.car_need = 0.0
        self.was_plugged = None

    def plugged(self, t: datetime) -> bool:
        """The car is home and plugged in from about 17:30 to 07:30 on weekdays, most of the weekend."""
        h = t.hour + t.minute / 60
        if t.weekday() >= 5:
            return not 11 <= h < 15
        return h >= 17.5 or h < 7.5

    async def refresh(self, dt: float) -> None:
        t = datetime.now(TZ)
        plug = self.plugged(t)
        if plug and not self.was_plugged:
            self.session = 0.0
            self.car_need = 14 + _day_rng(t, 3).random() * 16  # kWh the car wants tonight
        self.was_plugged = plug
        kw = 0.0
        if plug and self.enabled and self.session < self.car_need:
            kw = self.current * 230 * self.phases / 1000
        self.session += kw * dt / 3600
        self.total += kw * dt / 3600
        if self.inverter:
            self.inverter.car_kw = kw
        self.set(self.e_power, round(kw, 2))
        self.set(self.e_session, round(self.session, 2))
        self.set(self.e_total, round(self.total, 2))
        self.set(self.e_max, self.current)
        self.set(self.e_plug, "on" if plug else "off")
        self.set(self.e_state, "on" if kw > 0 else "off")
        self.set(self.e_enable, "on" if self.enabled else "off")

    def services(self) -> dict[str, list[str]]:
        return {"keba": ["set_current", "enable", "disable", "set_energy", "request_data", "set_failsafe", "start", "stop"]}

    async def handle_service(self, domain: str, service: str, data: dict[str, Any], ids: list[str]) -> bool:
        if domain != "keba":
            return False
        if service == "set_current":
            self.current = max(6.0, min(32.0, float(data.get("current") or 16)))
        elif service in ("enable", "start"):
            self.enabled = True
        elif service in ("disable", "stop"):
            self.enabled = False
        await self.refresh(0.0)
        return True


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


def make_demo() -> tuple[list[Device], World]:
    world = World()
    inv = GrowattSPH(world)
    return [inv, KebaP30(world, inv), EnergiDataService(world)], world


def seed_history(ha: Any, world: World, days: int = 21) -> None:
    """Three weeks of hourly history for the demo house, so learning has something to learn from from the start."""
    rec = ha.recorder
    if rec.hours.get("sensor.growatt_load_power"):
        return
    now = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    soc = 50.0
    for i in range(days * 24, 0, -1):
        t = now - timedelta(hours=i)
        pv = sum(world.sun_kw(t + timedelta(minutes=m)) for m in (0, 15, 30, 45)) / 4
        house = sum(world.house_kw(t + timedelta(minutes=m)) for m in (5, 20, 35, 50)) / 4
        car = 11.0 if (t.hour in (1, 2) and t.weekday() < 5) else 0.0
        use = house + car
        b = max(-5.0, min(5.0, pv - use))
        if soc >= 100 and b > 0 or soc <= 10 and b < 0:
            b = 0.0
        soc = max(10.0, min(100.0, soc + b / 10 * 100))
        grid = use - pv + b
        ts = t.timestamp()
        for e, v in (("sensor.growatt_pv_power", pv * 1000), ("sensor.growatt_load_power", use * 1000),
                     ("sensor.growatt_battery_power", b * 1000), ("sensor.growatt_grid_power", grid * 1000),
                     ("sensor.growatt_state_of_charge", soc), ("sensor.keba_charging_power", car)):
            rec.seed_hour(e, ts, v)
    rec.save()
