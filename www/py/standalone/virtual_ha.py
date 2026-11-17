"""A small Home Assistant inside the app.

Energy Pilot's core was written for Home Assistant: it reads entities, calls services and asks the recorder for
history. This class has the same interface as energy_pilot.ha.HomeAssistant, but the entities come from the app's
own device drivers (standalone/devices). A driver makes the same entities as the Home Assistant integration for
that device would (Growatt: select.…_work_mode_priority, KEBA: keba.set_current …), so auto-discovery, the brand
profiles in adapters.py and everything else work unchanged.

Also here: a recorder (hourly means per sensor and a short state history), so learning works as it does in HA.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

_LOGGER = logging.getLogger("energy_pilot.virtual_ha")

VERSION = "app"


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).isoformat()


class Recorder:
    """Hourly mean per numeric sensor (like HA's long-term statistics) and the last days' state changes."""

    KEEP_DAYS = 60
    HISTORY_DAYS = 3

    def __init__(self, data_dir: Path) -> None:
        self.file = data_dir / "app_recorder.json"
        self.hours: dict[str, dict[str, list[float]]] = {}  # entity -> {hour start (epoch s, str): [sum, n]}
        self.changes: dict[str, list[tuple[float, str]]] = {}
        self._dirty = False
        try:
            raw = json.loads(self.file.read_text())
            self.hours = raw.get("hours") or {}
            self.changes = {k: [tuple(x) for x in v] for k, v in (raw.get("changes") or {}).items()}
        except (OSError, ValueError):
            pass

    def see(self, entity_id: str, state: Any, now: float) -> None:
        try:
            v = float(state)
        except (TypeError, ValueError):
            v = None
        if v is not None and entity_id.startswith("sensor."):
            h = str(int(now // 3600 * 3600))
            row = self.hours.setdefault(entity_id, {}).setdefault(h, [0.0, 0])
            row[0] += v
            row[1] += 1
            self._dirty = True
        ch = self.changes.setdefault(entity_id, [])
        s = str(state)
        if not ch or ch[-1][1] != s:
            ch.append((now, s))
            self._dirty = True

    def seed_hour(self, entity_id: str, hour_start: float, mean: float) -> None:
        self.hours.setdefault(entity_id, {})[str(int(hour_start))] = [mean, 1]
        self._dirty = True

    def statistics(self, entity_id: str, start: float, end: float) -> list[dict[str, Any]]:
        out = []
        for h, (s, n) in sorted(self.hours.get(entity_id, {}).items(), key=lambda kv: int(kv[0])):
            t = int(h)
            if start <= t < end and n:
                out.append({"start": t * 1000, "end": (t + 3600) * 1000, "mean": s / n})
        return out

    def history(self, entity_id: str, start: float, end: float | None, include_start: bool = False) -> list[dict[str, Any]]:
        rows = self.changes.get(entity_id, [])
        end = end or time.time()
        out = [{"s": s, "lu": t, "lc": t} for t, s in rows if start <= t <= end]
        if include_start:
            before = [r for r in rows if r[0] < start]
            if before:
                out.insert(0, {"s": before[-1][1], "lu": start, "lc": start})
        return out

    def prune(self) -> None:
        cut_h = time.time() - self.KEEP_DAYS * 86400
        for e in list(self.hours):
            self.hours[e] = {h: v for h, v in self.hours[e].items() if int(h) >= cut_h}
        cut_c = time.time() - self.HISTORY_DAYS * 86400
        for e in list(self.changes):
            rows = self.changes[e]
            keep = [r for r in rows if r[0] >= cut_c]
            self.changes[e] = ([rows[len(rows) - len(keep) - 1]] if len(keep) < len(rows) else []) + keep

    def save(self) -> None:
        if not self._dirty:
            return
        self.prune()
        try:
            self.file.write_text(json.dumps({"hours": self.hours, "changes": self.changes}))
            self._dirty = False
        except OSError:
            _LOGGER.debug("Could not save the recorder", exc_info=True)


class VirtualHA:
    """Same interface as energy_pilot.ha.HomeAssistant."""

    def __init__(self, devices: list[Any] | None = None, config: dict[str, Any] | None = None,
                 data_dir: Path | None = None) -> None:
        import os

        from aiohttp import ClientSession

        self.data_dir = data_dir or Path(os.environ.get("DATA_DIR", "/data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.states: dict[str, dict[str, Any]] = {}
        self.session = ClientSession()
        self.on_state: list[Callable[[str, dict | None, dict | None], None]] = []
        self._event_handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self.connected = asyncio.Event()
        self.config: dict[str, Any] = {
            "version": VERSION, "time_zone": "Europe/Copenhagen", "country": "DK", "currency": "DKK", "language": "da",
            "latitude": 55.68, "longitude": 12.57, "location_name": "Hjem", "unit_system": {"length": "km"},
        }
        self.config.update(config or {})
        self.devices: list[Any] = list(devices or [])
        self.recorder = Recorder(self.data_dir)
        self.notifications: dict[str, dict[str, Any]] = {}
        self.service_log: list[dict[str, Any]] = []  # what Energy Pilot told the devices (shown in the app)
        self._inputs: dict[str, dict[str, Any]] = {}

    # ---- the entities ----

    def _entity_owner(self, entity_id: str) -> Any | None:
        for d in self.devices:
            if entity_id in d.entities:
                return d
        return None

    def publish(self, entity_id: str, state: Any, attributes: dict[str, Any] | None = None) -> None:
        """A driver (or the app) sets an entity's state - like a state_changed event in HA."""
        now = time.time()
        old = self.states.get(entity_id)
        attrs = dict(attributes if attributes is not None else (old or {}).get("attributes") or {})
        s = "unknown" if state is None else (str(round(state, 3)) if isinstance(state, float) else str(state))
        changed = old is None or old.get("state") != s
        new = {"entity_id": entity_id, "state": s, "attributes": attrs,
               "last_changed": _iso(now) if changed else old.get("last_changed", _iso(now)),
               "last_updated": _iso(now), "context": {"id": ""}}
        self.states[entity_id] = new
        self.recorder.see(entity_id, s, now)
        if changed or old.get("attributes") != attrs:
            for cb in self.on_state:
                try:
                    cb(entity_id, old, new)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("state callback failed", exc_info=True)

    async def run(self) -> None:
        """Keep the devices' entities up to date (instead of the websocket to HA)."""
        last = time.time()
        for d in self.devices:
            d.attach(self)
        for d in self.devices:
            try:
                await d.refresh(0.0)
            except Exception:  # noqa: BLE001 - a device that can't be reached must not stop the app
                _LOGGER.warning("Device %s could not be read", d.name, exc_info=True)
        # Give the devices a moment to report before Energy Pilot looks for them (a device that never answers is
        # simply shown as unavailable - the engine finds it once it does).
        for _ in range(60):
            if all(d.has_data() for d in self.devices):
                break
            await asyncio.sleep(0.25)
            for d in self.devices:
                if not d.has_data():
                    try:
                        await d.refresh(0.0)
                    except Exception:  # noqa: BLE001
                        pass
        self.connected.set()
        _LOGGER.info("Virtual Home Assistant ready (%s entities, %s devices)", len(self.states), len(self.devices))
        saved = time.time()
        while True:
            await asyncio.sleep(5)
            now = time.time()
            dt = now - last
            last = now
            for d in self.devices:
                try:
                    await d.refresh(dt)
                except Exception:  # noqa: BLE001 - one device offline must not stop the others
                    _LOGGER.warning("Device %s could not be read", d.name, exc_info=True)
            if now - saved > 120:
                self.recorder.save()
                saved = now

    # ---- services ----

    async def call_service(self, domain: str, service: str, data: dict[str, Any], response: bool = False) -> Any:
        data = dict(data or {})
        if domain == "recorder" and service == "get_statistics":
            return {"response": self._statistics(data)}
        ids = data.get("entity_id")
        ids = [ids] if isinstance(ids, str) else list(ids or [])
        self.service_log.append({"t": _iso(), "domain": domain, "service": service,
                                 "data": {k: v for k, v in data.items() if k != "entity_id"}, "entity_id": ids})
        del self.service_log[:-200]
        if domain == "persistent_notification":
            nid = data.get("notification_id") or str(len(self.notifications))
            if service == "create":
                self.notifications[nid] = {"title": data.get("title"), "message": data.get("message"), "t": _iso()}
            elif service == "dismiss":
                self.notifications.pop(nid, None)
            return None
        # A driver's own services (keba.set_current …) or its entities' (select.select_option on its select)
        handled = False
        for d in self.devices:
            if await d.handle_service(domain, service, data, ids):
                handled = True
        if handled:
            return {"response": {}} if response else None
        # Helpers Energy Pilot may use (input_boolean / input_number / input_datetime …)
        if domain.startswith("input_") or domain == "homeassistant":
            for e in ids:
                st = self.states.get(e)
                if not st:
                    continue
                if service in ("turn_on", "turn_off"):
                    self.publish(e, "on" if service == "turn_on" else "off")
                elif service == "set_value":
                    self.publish(e, data.get("value"))
                elif service == "set_datetime":
                    self.publish(e, data.get("time") or data.get("datetime"))
            return None
        _LOGGER.info("No device handles %s.%s %s", domain, service, ids)
        raise RuntimeError(f"Service {domain}.{service} not found")

    def _statistics(self, data: dict[str, Any]) -> dict[str, Any]:
        def ts(v: Any) -> float:
            return datetime.fromisoformat(str(v)).timestamp()

        start = ts(data["start_time"]) if data.get("start_time") else time.time() - 30 * 86400
        end = ts(data["end_time"]) if data.get("end_time") else time.time()
        return {"statistics": {e: self.recorder.statistics(e, start, end) for e in data.get("statistic_ids") or []}}

    async def set_state(self, entity_id: str, state: Any, attributes: dict[str, Any] | None) -> None:
        self.publish(entity_id, state, attributes or {})

    async def history(self, entity_id: str, start: datetime, end: datetime | None) -> list[list[dict[str, Any]]]:
        rows = self.recorder.history(entity_id, start.timestamp(), end.timestamp() if end else None, include_start=True)
        attrs = (self.states.get(entity_id) or {}).get("attributes") or {}
        return [[{"entity_id": entity_id, "state": r["s"], "attributes": attrs,
                  "last_changed": _iso(r["lc"]), "last_updated": _iso(r["lu"])} for r in rows]]

    async def listen_event(self, event: str, cb: Callable[[dict[str, Any]], None]) -> None:
        self._event_handlers.setdefault(event, []).append(cb)

    # ---- the websocket commands Energy Pilot uses ----

    def _registry(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        entities, devices = [], []
        for d in self.devices:
            devices.append({"id": d.device_id, "name": d.name, "name_by_user": None, "manufacturer": d.manufacturer,
                            "model": d.model, "sw_version": d.sw_version, "area_id": None, "identifiers": [[d.platform, d.device_id]],
                            "config_entries": [d.device_id], "disabled_by": None})
            for e in d.entities:
                entities.append({"entity_id": e, "platform": d.platform, "device_id": d.device_id, "disabled_by": None,
                                 "hidden_by": None, "config_entry_id": d.device_id, "unique_id": f"{d.device_id}_{e}",
                                 "name": None, "original_name": (self.states.get(e) or {}).get("attributes", {}).get("friendly_name"),
                                 "entity_category": None, "options": {}})
        return entities, devices

    async def command(self, msg: dict[str, Any], timeout: float = 60) -> Any:
        kind = msg.get("type")
        if kind == "get_config":
            return dict(self.config)
        if kind == "get_states":
            return list(self.states.values())
        if kind == "config/entity_registry/list":
            return self._registry()[0]
        if kind == "config/entity_registry/list_for_display":
            return {"entities": [{"ei": e["entity_id"], "pl": e["platform"], "di": e["device_id"]} for e in self._registry()[0]],
                    "entity_categories": {}}
        if kind == "config/entity_registry/get":
            for e in self._registry()[0]:
                if e["entity_id"] == msg.get("entity_id"):
                    return e
            raise RuntimeError("not found")
        if kind == "config/device_registry/list":
            return self._registry()[1]
        if kind == "config/area_registry/list":
            return []
        if kind == "get_services":
            out: dict[str, dict[str, Any]] = {}
            for d in self.devices:
                for dom, svcs in d.services().items():
                    out.setdefault(dom, {}).update({s: {"fields": {}} for s in svcs})
            for dom in ("select", "number", "switch", "button", "input_boolean", "input_number", "input_datetime", "homeassistant"):
                out.setdefault(dom, {})
            return out
        if kind == "energy/get_prefs":
            src: list[dict[str, Any]] = []
            for d in self.devices:
                src.extend(d.energy_prefs())
            return {"energy_sources": src, "device_consumption": []}
        if kind == "energy/solar_forecast":
            return {}
        if kind == "history/history_during_period":
            start = datetime.fromisoformat(str(msg["start_time"])).timestamp()
            end = datetime.fromisoformat(str(msg["end_time"])).timestamp() if msg.get("end_time") else None
            return {e: self.recorder.history(e, start, end, bool(msg.get("include_start_time_state"))) for e in msg.get("entity_ids") or []}
        if kind in ("lovelace/resources",):
            return []
        if kind and (kind.startswith("lovelace/") or kind in ("fire_event", "subscribe_events")):
            return None
        raise RuntimeError(f"Unknown command {kind}")
