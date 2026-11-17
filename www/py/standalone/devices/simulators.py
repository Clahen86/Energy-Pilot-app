"""Simulated devices that speak the real protocols: a SolarAssistant (WebSocket API) in front of a Growatt SPH with
a battery and solar panels, and a KEBA P30 (UDP). The demo runs the app's real drivers against these, so what is
shown in the demo is the same code path as in a real home - only the other end is simulated.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from .demo import TZ, World, _day_rng
from .solarassistant import SETTINGS, TOTALS


class SphPhysics:
    """A Growatt SPH's behaviour from its own settings (work mode, stop levels, rates, time slots)."""

    def __init__(self, world: World, capacity_kwh: float = 10.0, max_kw: float = 3.6, soc: float = 45.0) -> None:
        self.world, self.cap, self.max_kw, self.soc = world, capacity_kwh, max_kw, soc
        self.s: dict[str, Any] = {
            "work_mode_priority": "Load first", "load_first_stop_discharge": 10, "load_first_stop_charge": 100,
            "free_mode_discharge_rate": 100, "free_mode_charge_rate": 100, "battery_first_grid_charge": "Disabled",
            "battery_first_slot_1_enabled": "Disabled", "battery_first_slot_1_start": "00:00", "battery_first_slot_1_end": "00:00",
            "battery_first_charge_rate": 100, "battery_first_stop_charge": 100, "grid_first_slot_1_enabled": "Disabled",
            "grid_first_slot_1_start": "00:00", "grid_first_slot_1_end": "00:00", "grid_first_discharge_rate": 100,
            "grid_first_stop_discharge": 10, "export_limiter": "Disabled", "export_power_rate": 100,
        }
        self.car_kw = 0.0
        self.e = {"pv": 0.0, "load": 0.0, "bin": 0.0, "bout": 0.0, "gin": 0.0, "gout": 0.0}
        self.now = {"pv": 0.0, "load": 0.0, "batt": 0.0, "grid": 0.0}

    def _f(self, k: str) -> float:
        return float(self.s[k])

    def _slot(self, kind: str, t: datetime) -> bool:
        if str(self.s[f"{kind}_first_slot_1_enabled"]) != "Enabled":
            return False
        a, b = self.s[f"{kind}_first_slot_1_start"], self.s[f"{kind}_first_slot_1_end"]
        n = f"{t.hour:02d}:{t.minute:02d}"
        return a <= n <= b if a <= b else n >= a or n <= b

    def battery_kw(self, t: datetime, pv: float, use: float) -> float:
        mode, surplus, lim = self.s["work_mode_priority"], pv - use, self.max_kw
        if mode == "Battery first" and self._slot("battery", t):
            if self.soc >= self._f("battery_first_stop_charge"):
                return max(0.0, min(surplus, lim)) if self.soc < 100 else 0.0
            rate = self._f("battery_first_charge_rate") / 100 * lim
            return min(lim, rate if self.s["battery_first_grid_charge"] == "Enabled" else max(0.0, min(rate, surplus)))
        if mode == "Grid first" and self._slot("grid", t):
            return 0.0 if self.soc <= self._f("grid_first_stop_discharge") else -self._f("grid_first_discharge_rate") / 100 * lim
        if surplus >= 0:
            return min(surplus, self._f("free_mode_charge_rate") / 100 * lim) if self.soc < self._f("load_first_stop_charge") else 0.0
        if self.soc <= self._f("load_first_stop_discharge"):
            return 0.0
        return -min(-surplus, self._f("free_mode_discharge_rate") / 100 * lim)

    def step(self, dt: float) -> None:
        t = datetime.now(TZ)
        pv = self.world.sun_kw(t)
        use = self.world.house_kw(t) + self.car_kw
        b = self.battery_kw(t, pv, use)
        if self.soc >= 100 and b > 0 or self.soc <= 0 and b < 0:
            b = 0.0
        h = dt / 3600
        self.soc = max(0.0, min(100.0, self.soc + (b * 0.95 if b > 0 else b / 0.95) * h / self.cap * 100))
        grid = use - pv + b
        self.now = {"pv": pv, "load": use, "batt": b, "grid": grid}
        for k, v in (("pv", pv), ("load", use), ("bin", max(0.0, b)), ("bout", max(0.0, -b)), ("gin", max(0.0, grid)), ("gout", max(0.0, -grid))):
            self.e[k] += v * h

    def metrics(self) -> dict[str, Any]:
        n, e = self.now, self.e
        return {
            "total/pv_power": round(n["pv"] * 1000), "total/load_power": round(n["load"] * 1000),
            "total/grid_power": round(n["grid"] * 1000), "total/battery_power": round(n["batt"] * 1000),
            "total/battery_state_of_charge": round(self.soc), "total/pv_energy": round(e["pv"], 2),
            "total/load_energy": round(e["load"], 2), "total/battery_energy_in": round(e["bin"], 2),
            "total/battery_energy_out": round(e["bout"], 2), "total/grid_energy_in": round(e["gin"], 2),
            "total/grid_energy_out": round(e["gout"], 2), "inverter_1/temperature": 38.5, "battery_1/voltage": 51.8,
            **{f"inverter_1/{k}": v for k, v in self.s.items()},
        }


class FakeSolarAssistant:
    """A SolarAssistant on the other end of the WebSocket (same messages as the real API)."""

    PASSWORD = "demo"

    def __init__(self, sph: SphPhysics) -> None:
        self.sph = sph
        self.q: asyncio.Queue[str | None] = asyncio.Queue()
        self.open = False
        self._task: asyncio.Task | None = None

    async def connect(self, url: str, timeout: float = 10.0) -> None:
        if f"password={self.PASSWORD}" not in url:
            from ..native import NetError
            raise NetError("forkert adgangskode")
        self.open = True

    def _put(self, event: str, payload: dict[str, Any]) -> None:
        self.q.put_nowait(json.dumps({"topic": "metrics", "event": event, "payload": payload}))

    async def send(self, text: str) -> None:
        msg = json.loads(text)
        ev, p = msg.get("event"), msg.get("payload") or {}
        if ev == "join":
            defs = [{"topic": t, "device": "Totals", "group": "Status", "name": k.replace("_", " ").capitalize(), "unit": u}
                    for t, (k, u, _dc) in TOTALS.items()]
            defs += [{"topic": f"inverter_1/{k}", "device": "Inverter 1", "group": "Settings", "name": k.replace("_", " ").capitalize(),
                      "unit": "%" if d == "number" else ""} for k, (d, _s) in SETTINGS["growatt_sph"].items()]
            defs += [{"topic": "inverter_1/temperature", "device": "Inverter 1", "group": "Status", "name": "Temperature", "unit": "°C"},
                     {"topic": "battery_1/voltage", "device": "Battery 1", "group": "Status", "name": "Voltage", "unit": "V"}]
            self._put("definition", {"definitions": defs})
            self._push()
            self._task = asyncio.ensure_future(self._loop())
        elif ev == "set":
            topic, value = p.get("topic", ""), p.get("value")
            key = topic.split("/", 1)[-1]
            await asyncio.sleep(0.2)  # the inverter takes a moment
            if key in self.sph.s:
                cur = self.sph.s[key]
                self.sph.s[key] = type(cur)(float(value)) if isinstance(cur, (int, float)) else str(value)
                self._put("set_result", {"topic": topic, "result": "ok"})
                self._push()
            else:
                self._put("set_result", {"topic": topic, "result": "error", "message": "unknown setting"})

    def _push(self) -> None:
        self._put("data", {"metrics": [{"topic": t, "value": v} for t, v in self.sph.metrics().items()]})

    async def _loop(self) -> None:
        last = time.time()
        while self.open:
            await asyncio.sleep(3)
            now = time.time()
            self.sph.step(now - last)
            last = now
            self._push()

    async def recv(self) -> str | None:
        return await self.q.get()

    async def close(self) -> None:
        self.open = False
        if self._task:
            self._task.cancel()


class FakeKebaP30:
    """A KEBA P30 answering UDP commands; a car that is home in the evening and night."""

    def __init__(self, world: World, sph: SphPhysics | None = None, phases: int = 1) -> None:
        self.world, self.sph, self.phases = world, sph, phases
        self.enabled, self.curr_ma, self.session, self.total = 1, 16000, 0.0, 6230.0
        self.need, self.was, self.last = 0.0, None, time.time()

    def plugged(self, t: datetime) -> bool:
        h = t.hour + t.minute / 60
        return (not 11 <= h < 15) if t.weekday() >= 5 else (h >= 17.5 or h < 7.5)

    def _tick(self) -> float:
        now = time.time()
        dt, self.last = now - self.last, now
        t = datetime.now(TZ)
        plug = self.plugged(t)
        if plug and not self.was:
            self.session, self.need = 0.0, 12 + _day_rng(t, 3).random() * 14
        self.was = plug
        kw = self.curr_ma / 1000 * 230 * self.phases / 1000 if plug and self.enabled and self.session < self.need else 0.0
        self.session += kw * dt / 3600
        self.total += kw * dt / 3600
        if self.sph:
            self.sph.car_kw = kw
        return kw

    async def udp(self, host: str, port: int, msg: str, timeout: float, local_port: int, expect: bool) -> str:
        await asyncio.sleep(0.02)
        kw = self._tick()
        plug = self.plugged(datetime.now(TZ))
        cmd = msg.split()
        if msg == "report 1":
            return json.dumps({"ID": "1", "Product": "KC-P30-EC240422-E00", "Serial": "12345678", "Firmware": "P30 v 3.10.16"})
        if msg == "report 2":
            return json.dumps({"ID": "2", "State": 3 if kw > 0 else (2 if plug else 1), "Error1": 0, "Error2": 0,
                               "Plug": 7 if plug else 3, "Enable sys": self.enabled, "Max curr": self.curr_ma,
                               "Curr user": self.curr_ma, "Curr HW": 16000})
        if msg == "report 3":
            i = round(kw * 1e6 / 230 / self.phases)
            return json.dumps({"ID": "3", "U1": 231, "U2": 0, "U3": 0, "I1": i, "I2": 0, "I3": 0, "P": round(kw * 1e6),
                               "PF": 995 if kw else 0, "E pres": round(self.session * 10000), "E total": round(self.total * 10000)})
        if cmd and cmd[0] == "curr":
            self.curr_ma = max(6000, min(16000, int(cmd[1])))
            return "TCH-OK :done"
        if cmd and cmd[0] == "ena":
            self.enabled = int(cmd[1])
            return "TCH-OK :done"
        return "TCH-OK :done"


def make_simulated_home(world: World | None = None) -> tuple[list[Any], World]:
    """The demo home through the real drivers: SolarAssistant + Growatt SPH 3600, KEBA P30, prices."""
    from .demo import EnergiDataService
    from .keba import Keba
    from .solarassistant import SolarAssistant

    world = world or World(pv_kwp=6.0)
    sph = SphPhysics(world)
    sph.step(0)
    keba = FakeKebaP30(world, sph)
    sa = SolarAssistant("solarassistant.local", FakeSolarAssistant.PASSWORD, transport=lambda: FakeSolarAssistant(sph))
    kb = Keba("keba.local", udp=keba.udp, poll_s=5)
    return [sa, kb, EnergiDataService(world)], world

