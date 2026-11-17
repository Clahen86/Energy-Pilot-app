"""KEBA KeContact P20/P30: the charger's own UDP protocol on port 7090 (the same as Home Assistant's keba integration).

"report 1/2/3" read the charger (JSON answers); "curr <mA>", "ena 0|1", "setenergy <0.1 Wh>" control it.
The charger answers to port 7090 on the sender, and wants a short pause between commands.

Entities and services are named as in Home Assistant (sensor.keba_p30_charging_power, binary_sensor.keba_p30_plug,
keba.set_current …), so Energy Pilot's own KEBA profile and car planning work unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

from ..native import NetError, udp_request
from .base import Device

_LOGGER = logging.getLogger("energy_pilot.keba")
PORT = 7090

Udp = Callable[[str, int, str, float, int, bool], Awaitable[str]]


async def _default_udp(host: str, port: int, msg: str, timeout: float, local_port: int, expect: bool) -> str:
    return await udp_request(host, port, msg, timeout, local_port, expect)


class Keba(Device):
    platform = "keba"
    manufacturer = "KEBA"
    model = "KeContact P30"

    def __init__(self, host: str, name: str = "KEBA P30", udp: Udp | None = None, poll_s: float = 10.0) -> None:
        super().__init__("keba_1", name)
        self.host = host.strip()
        self.udp = udp or _default_udp
        self.poll_s = poll_s
        p = "keba_p30" if "p30" in name.lower() else "keba_" + "_".join(name.lower().split()[1:] or ["p20"])
        self.prefix = p
        e = self.entity
        self.e_power = e(f"sensor.{p}_charging_power", "Charging Power", "kW", "power")
        self.e_session = e(f"sensor.{p}_session_energy", "Session Energy", "kWh", "energy", None)
        self.e_total = e(f"sensor.{p}_total_energy", "Total Energy", "kWh", "energy", "total_increasing")
        self.e_max = e(f"sensor.{p}_max_current", "Max Current", "A", "current")
        self.e_plug = e(f"binary_sensor.{p}_plug", "Plug", device_class="plug")
        self.e_state = e(f"binary_sensor.{p}_charging_state", "Charging State", device_class="power")
        self.e_enable = e(f"binary_sensor.{p}_status", "Status")
        self.status = "ikke forbundet"
        self.product = ""
        self._lock = asyncio.Lock()
        self._last_cmd = 0.0
        self._next_poll = 0.0
        self.r2: dict[str, Any] = {}
        self.r3: dict[str, Any] = {}

    async def _cmd(self, msg: str, reply: bool = True) -> str:
        async with self._lock:
            wait = 0.12 - (time.time() - self._last_cmd)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self.udp(self.host, PORT, msg, 3.0, PORT, reply)
            finally:
                self._last_cmd = time.time()

    async def _report(self, n: int) -> dict[str, Any]:
        raw = await self._cmd(f"report {n}")
        try:
            return json.loads(raw)
        except ValueError as err:
            raise NetError(f"uventet svar: {raw[:60]}") from err

    async def refresh(self, dt: float) -> None:
        now = time.time()
        if now < self._next_poll:
            return
        self._next_poll = now + self.poll_s
        try:
            if not self.product:
                r1 = await self._report(1)
                self.product = str(r1.get("Product", ""))
                if self.product:
                    self.model = self.product
            self.r2 = await self._report(2)
            self.r3 = await self._report(3)
            self.online = True
            self.status = "forbundet"
        except (NetError, OSError, asyncio.TimeoutError) as err:
            self.online = False
            self.status = f"intet svar: {err}"
            for eid in self.entities:
                self.ha.publish(eid, "unavailable", self.entities[eid])
            return
        self._publish()

    def _publish(self) -> None:
        r2, r3 = self.r2, self.r3
        plug = int(r2.get("Plug", 0) or 0)
        state = int(r2.get("State", 0) or 0)
        self.set(self.e_power, round(float(r3.get("P", 0) or 0) / 1e6, 3),
                 power_factor=float(r3.get("PF", 0) or 0) / 1000,
                 voltage_u1=str(r3.get("U1", 0)), voltage_u2=str(r3.get("U2", 0)), voltage_u3=str(r3.get("U3", 0)),
                 current_i1=str(float(r3.get("I1", 0) or 0) / 1000), current_i2=str(float(r3.get("I2", 0) or 0) / 1000),
                 current_i3=str(float(r3.get("I3", 0) or 0) / 1000))
        self.set(self.e_session, round(float(r3.get("E pres", 0) or 0) / 10000, 2))
        self.set(self.e_total, round(float(r3.get("E total", 0) or 0) / 10000, 2))
        self.set(self.e_max, round(float(r2.get("Curr user", r2.get("Max curr", 0)) or 0) / 1000, 1))
        self.set(self.e_plug, "on" if plug >= 5 else "off", plugged_on_wallbox=plug >= 1,
                 plug_locked=plug in (3, 7), plugged_on_EV=plug >= 5)
        self.set(self.e_state, "on" if state == 3 else "off")
        self.set(self.e_enable, "on" if int(r2.get("Enable sys", 0) or 0) else "off", keba_state=state,
                 error_1=r2.get("Error1"), error_2=r2.get("Error2"))

    def services(self) -> dict[str, list[str]]:
        return {"keba": ["set_current", "enable", "disable", "set_energy", "request_data", "set_failsafe", "start", "stop"]}

    async def handle_service(self, domain: str, service: str, data: dict[str, Any], ids: list[str]) -> bool:
        if domain != "keba":
            return False
        if service == "set_current":
            amps = max(6.0, min(63.0, float(data.get("current") or 6)))
            await self._cmd(f"curr {int(amps * 1000)}")
        elif service in ("enable", "start"):
            await self._cmd("ena 1")
        elif service in ("disable", "stop"):
            await self._cmd("ena 0")
        elif service == "set_energy":
            await self._cmd(f"setenergy {int(float(data.get('energy') or 0) * 10000)}")
        elif service == "set_failsafe":
            await self._cmd(f"failsafe {int(data.get('failsafe_timeout', 30))} {int(float(data.get('failsafe_fallback', 0)) * 1000)} "
                            f"{1 if data.get('failsafe_persist') else 0}")
        self._next_poll = 0.0
        await self.refresh(0.0)
        return True

    def info(self) -> dict[str, Any]:
        return {"status": self.status, "host": self.host, "product": self.product,
                "state": self.r2.get("State"), "plug": self.r2.get("Plug")}
