"""SolarAssistant: reads and controls the inverter through SolarAssistant's local WebSocket API.

ws://<SolarAssistant>/api/websocket?password=<local password>
  -> {"topic": "metrics", "event": "join", "payload": {"topics": [{"topic": "*", "max_frequency_s": 5}]}, "ref": "1"}
     (at most one update per topic every 5 s - SolarAssistant can send far more than a phone should chew through)
  <- definition {topic, device, group, name, unit} once per topic, then data {metrics: [{topic, value}]}
  -> set {topic, value, verify}  <- set_result {topic, result: ok|error, message}

The entities are named the way SolarAssistant names them in Home Assistant (the device's name, then the setting:
sensor.growatt_sph_pv_power, select.growatt_sph_work_mode_priority …). So Energy Pilot's own profile for the
inverter (adapters.py, "Growatt (SPH/SPA)" for an SPH) controls it exactly as it does in Home Assistant.

A setting's topic is "inverter_1/<key>" for the entity "<domain>.<prefix>_<key>". If SolarAssistant names a setting
differently on some inverter, the app's connection screen shows every topic SolarAssistant sends, and a mapping
(config "map": {"<key>": "<topic>"}) puts it right.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

from ..native import NetError, WebSocket
from .base import Device

_LOGGER = logging.getLogger("energy_pilot.solarassistant")

# The totals SolarAssistant always has (its API's own names) -> the entity's key, unit, device class.
TOTALS = {
    "total/pv_power": ("pv_power", "W", "power"),
    "total/load_power": ("load_power", "W", "power"),
    "total/grid_power": ("grid_power", "W", "power"),            # + import, - export
    "total/battery_power": ("battery_power", "W", "power"),      # + charging, - discharging
    "total/battery_state_of_charge": ("battery_state_of_charge", "%", "battery"),
    "total/pv_energy": ("pv_energy", "kWh", "energy"),
    "total/load_energy": ("load_energy", "kWh", "energy"),
    "total/battery_energy_in": ("battery_energy_in", "kWh", "energy"),
    "total/battery_energy_out": ("battery_energy_out", "kWh", "energy"),
    "total/grid_energy_in": ("grid_energy_in", "kWh", "energy"),
    "total/grid_energy_out": ("grid_energy_out", "kWh", "energy"),
}

ON_OFF = ["Disabled", "Enabled"]
TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)] + ["23:59"]

# Settings per inverter family, as SolarAssistant shows them in Home Assistant: key -> (domain, options/limits).
SETTINGS: dict[str, dict[str, tuple[str, Any]]] = {
    "growatt_sph": {
        "work_mode_priority": ("select", ["Load first", "Battery first", "Grid first"]),
        "load_first_stop_discharge": ("number", (10, 100)),
        "load_first_stop_charge": ("number", (10, 100)),
        "free_mode_discharge_rate": ("number", (0, 100)),
        "free_mode_charge_rate": ("number", (0, 100)),
        "battery_first_grid_charge": ("select", ON_OFF),
        "battery_first_slot_1_enabled": ("switch", None),
        "battery_first_slot_1_start": ("select", TIMES),
        "battery_first_slot_1_end": ("select", TIMES),
        "battery_first_charge_rate": ("number", (0, 100)),
        "battery_first_stop_charge": ("number", (10, 100)),
        "grid_first_slot_1_enabled": ("switch", None),
        "grid_first_slot_1_start": ("select", TIMES),
        "grid_first_slot_1_end": ("select", TIMES),
        "grid_first_discharge_rate": ("number", (0, 100)),
        "grid_first_stop_discharge": ("number", (10, 100)),
        "export_limiter": ("select", ON_OFF),
        "export_power_rate": ("number", (0, 100)),
    },
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def unit_class(unit: str | None, name: str) -> str | None:
    u = (unit or "").strip()
    if u in ("W", "kW"):
        return "power"
    if u in ("Wh", "kWh"):
        return "energy"
    if u == "%" and re.search(r"charge|soc", name, re.I):
        return "battery"
    return {"V": "voltage", "A": "current", "°C": "temperature", "Hz": "frequency"}.get(u)


class SolarAssistant(Device):
    platform = "solar_assistant"
    manufacturer = "Growatt"
    model = "SPH"

    def __init__(self, host: str, password: str = "", family: str = "growatt_sph", name: str = "Growatt SPH",
                 model: str = "SPH 3600", manufacturer: str = "Growatt", mapping: dict[str, str] | None = None,
                 transport: Callable[[], Any] | None = None) -> None:
        super().__init__("solar_assistant_1", name)
        self.host, self.password, self.family = host.strip(), password, family
        self.model, self.manufacturer = model, manufacturer
        self.prefix = slug(name)
        self.mapping = dict(mapping or {})
        self.transport = transport or WebSocket
        self.ws: Any = None
        self.defs: dict[str, dict[str, Any]] = {}  # every topic SolarAssistant has sent (shown in the app)
        self.values: dict[str, Any] = {}
        self.topic_entity: dict[str, str] = {}
        self.entity_topic: dict[str, str] = {}
        self.kind: dict[str, tuple[str, Any]] = {}
        self.pending: dict[str, asyncio.Future] = {}
        self.status = "ikke forbundet"
        self.last_data = 0.0
        self._ref = 0
        self._task: asyncio.Task | None = None
        self._make_entities()

    # ---- entities ----

    def _make_entities(self) -> None:
        for topic, (key, unit, dc) in TOTALS.items():
            sc = "total_increasing" if dc == "energy" else "measurement"
            eid = self.entity(f"sensor.{self.prefix}_{key}", key.replace("_", " ").capitalize(), unit, dc, sc)
            self.topic_entity[topic] = eid
            self.entity_topic[eid] = topic
        for key, (domain, spec) in SETTINGS.get(self.family, {}).items():
            extra: dict[str, Any] = {}
            if domain == "select":
                extra["options"] = list(spec)
            elif domain == "number":
                extra.update(min=spec[0], max=spec[1], step=1, mode="box")
            eid = self.entity(f"{domain}.{self.prefix}_{key}", key.replace("_", " ").capitalize(),
                              "%" if domain == "number" else None, **extra)
            topic = self.mapping.get(key) or f"inverter_1/{key}"
            self.topic_entity[topic] = eid
            self.entity_topic[eid] = topic
            self.kind[eid] = (domain, spec)

    def _entity_for_new_topic(self, topic: str, d: dict[str, Any]) -> str | None:
        """Any other number SolarAssistant sends becomes a sensor too (temperatures, voltages …)."""
        if topic in self.topic_entity:
            return self.topic_entity[topic]
        key = slug(topic.split("/", 1)[1] if topic.startswith("inverter_1/") else topic)
        eid = f"sensor.{self.prefix}_{key}"
        if eid in self.entities:
            return None
        unit = d.get("unit") or None
        self.entity(eid, d.get("name") or key, unit, unit_class(unit, d.get("name") or key))
        self.topic_entity[topic] = eid
        self.entity_topic[eid] = topic
        return eid

    # ---- connection ----

    def attach(self, ha: Any) -> None:
        super().attach(ha)
        for eid in self.entities:
            self.ha.publish(eid, "unavailable", self.entities[eid])
        self._task = asyncio.ensure_future(self._run())

    def _url(self) -> str:
        host = self.host
        if "://" not in host:
            host = "ws://" + host
        from urllib.parse import quote
        return f"{host.rstrip('/')}/api/websocket?password={quote(self.password)}"

    async def _run(self) -> None:
        delay = 2.0
        while True:
            try:
                self.status = "forbinder…"
                self.ws = self.transport()
                await self.ws.connect(self._url())
                self.status = "forbundet"
                delay = 2.0
                await self._send("join", {"topics": [{"topic": "*", "max_frequency_s": 5}]})
                while True:
                    raw = await self.ws.recv()
                    if raw is None:
                        break
                    self._on_message(raw)
            except (NetError, OSError, asyncio.TimeoutError) as err:
                self.status = f"kan ikke forbinde: {err}"
                _LOGGER.warning("SolarAssistant %s: %s", self.host, err)
            except Exception as err:  # noqa: BLE001
                self.status = f"fejl: {err}"
                _LOGGER.warning("SolarAssistant failed", exc_info=True)
            self.online = False
            for eid in self.entities:
                self.ha.publish(eid, "unavailable", self.entities[eid])
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(delay)
            delay = min(60.0, delay * 2)

    async def _send(self, event: str, payload: dict[str, Any]) -> str:
        self._ref += 1
        ref = str(self._ref)
        await self.ws.send(json.dumps({"topic": "metrics", "event": event, "payload": payload, "ref": ref}))
        return ref

    def _on_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        ev, p = msg.get("event"), msg.get("payload") or {}
        if ev == "definition":
            for d in p.get("definitions") or [p]:
                t = d.get("topic")
                if t:
                    self.defs[t] = d
                    self._entity_for_new_topic(t, d)
        elif ev == "data":
            self.online = True
            self.last_data = time.time()
            for m in p.get("metrics") or [p]:
                t = m.get("topic")
                if t is None:
                    continue
                self.values[t] = m.get("value")
                eid = self.topic_entity.get(t) or self._entity_for_new_topic(t, self.defs.get(t, {}))
                if eid:
                    self.set(eid, self._to_state(eid, m.get("value")))
        elif ev == "set_result":
            fut = self.pending.pop(p.get("topic"), None)
            if fut and not fut.done():
                fut.set_result(p)

    def _to_state(self, eid: str, value: Any) -> Any:
        domain = self.kind.get(eid, ("sensor", None))[0]
        if domain == "switch":
            return "on" if str(value).strip().lower() in ("enabled", "on", "true", "1", "yes") else "off"
        if isinstance(value, bool):
            return "on" if value else "off"
        return value

    def _to_value(self, eid: str, value: Any) -> str:
        """Our value -> what SolarAssistant expects, in the same form it reports the setting."""
        domain = self.kind.get(eid, ("sensor", None))[0]
        if domain == "switch":
            on = value == "on"
            cur = str(self.values.get(self.entity_topic.get(eid), "")).strip()
            if cur.lower() in ("true", "false"):
                return "true" if on else "false"
            if cur.lower() in ("on", "off"):
                return "on" if on else "off"
            if cur in ("1", "0"):
                return "1" if on else "0"
            return "Enabled" if on else "Disabled"
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)

    async def write(self, entity_id: str, value: Any) -> None:
        topic = self.entity_topic.get(entity_id)
        if not topic or not self.ws or not self.ws.open:
            raise RuntimeError(f"SolarAssistant er ikke forbundet ({self.status})")
        sa_value = self._to_value(entity_id, value)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[topic] = fut
        await self._send("set", {"topic": topic, "value": sa_value, "verify": True})
        try:
            res = await asyncio.wait_for(fut, 90)
        except asyncio.TimeoutError as err:
            self.pending.pop(topic, None)
            raise RuntimeError(f"SolarAssistant svarede ikke på {topic}") from err
        if res.get("result") != "ok":
            raise RuntimeError(f"SolarAssistant: {topic} = {sa_value}: {res.get('message') or res.get('result')}")
        if self.kind.get(entity_id, ("",))[0] == "number":
            value = float(value)
            value = int(value) if value.is_integer() else value
        self.values[topic] = sa_value
        self.set(entity_id, value)

    def has_data(self) -> bool:
        return bool(self.last_data)

    async def refresh(self, dt: float) -> None:
        if self.online and self.last_data and time.time() - self.last_data > 120:
            self.status = "ingen data i 2 minutter"

    def info(self) -> dict[str, Any]:
        return {"status": self.status, "host": self.host,
                "topics": [{"topic": t, "name": d.get("name"), "unit": d.get("unit"), "group": d.get("group"),
                            "value": self.values.get(t), "entity": self.topic_entity.get(t)} for t, d in sorted(self.defs.items())],
                "missing": [e for e, t in self.entity_topic.items() if e in self.kind and t not in self.defs and t not in self.values]}

    def energy_prefs(self) -> list[dict[str, Any]]:
        p = self.prefix
        return [
            {"type": "solar", "stat_energy_from": f"sensor.{p}_pv_energy", "stat_rate": f"sensor.{p}_pv_power"},
            {"type": "battery", "stat_energy_from": f"sensor.{p}_battery_energy_out", "stat_energy_to": f"sensor.{p}_battery_energy_in",
             "stat_rate": f"sensor.{p}_battery_power"},
            {"type": "grid", "flow_from": [{"stat_energy_from": f"sensor.{p}_grid_energy_in"}],
             "flow_to": [{"stat_energy_to": f"sensor.{p}_grid_energy_out"}], "stat_rate": f"sensor.{p}_grid_power"},
        ]
