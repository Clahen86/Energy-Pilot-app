"""What every device driver in the app has in common.

A driver makes the entities the Home Assistant integration for that device would make, with the same names and
units, and carries out the services Energy Pilot calls (select.select_option, number.set_value, switch.turn_on,
keba.set_current …) by talking to the device itself (Modbus TCP, UDP, the maker's cloud …).
"""

from __future__ import annotations

from typing import Any


class Device:
    platform = "unknown"  # the HA integration this driver stands in for (growatt_local, keba …)
    manufacturer = ""
    model = ""
    sw_version = "app"

    def __init__(self, device_id: str, name: str) -> None:
        self.device_id = device_id
        self.name = name
        self.entities: dict[str, dict[str, Any]] = {}  # entity_id -> attributes
        self.ha: Any = None
        self.online = True

    # ---- set up ----

    def entity(self, entity_id: str, name: str, unit: str | None = None, device_class: str | None = None,
               state_class: str | None = "measurement", **extra: Any) -> str:
        attrs: dict[str, Any] = {"friendly_name": f"{self.name} {name}"}
        if unit:
            attrs["unit_of_measurement"] = unit
        if device_class:
            attrs["device_class"] = device_class
        if state_class and entity_id.startswith("sensor.") and unit:
            attrs["state_class"] = state_class
        attrs.update(extra)
        self.entities[entity_id] = attrs
        return entity_id

    def attach(self, ha: Any) -> None:
        self.ha = ha

    def set(self, entity_id: str, state: Any, **attrs: Any) -> None:
        a = dict(self.entities.get(entity_id) or {})
        a.update(attrs)
        self.entities[entity_id] = a
        if not self.online:
            state = "unavailable"
        self.ha.publish(entity_id, state, a)

    def get(self, entity_id: str) -> Any:
        return ((self.ha.states.get(entity_id) or {}) if self.ha else {}).get("state")

    def get_float(self, entity_id: str, default: float = 0.0) -> float:
        try:
            return float(self.get(entity_id))
        except (TypeError, ValueError):
            return default

    # ---- what a driver does ----

    async def refresh(self, dt: float) -> None:
        """Read the device (dt: seconds since last time) and publish its entities."""

    async def handle_service(self, domain: str, service: str, data: dict[str, Any], ids: list[str]) -> bool:
        """Carry out a service if it is for this device. Returns True when handled."""
        mine = [e for e in ids if e in self.entities]
        if not mine:
            return False
        for e in mine:
            if domain == "select" and service == "select_option":
                await self.write(e, data.get("option"))
            elif domain == "number" and service == "set_value":
                await self.write(e, data.get("value"))
            elif domain in ("switch", "input_boolean", "homeassistant") and service in ("turn_on", "turn_off"):
                await self.write(e, "on" if service == "turn_on" else "off")
            elif domain == "button" and service == "press":
                await self.write(e, "press")
            elif domain == "time" and service == "set_value":
                await self.write(e, data.get("time"))
            else:
                return False
        return True

    async def write(self, entity_id: str, value: Any) -> None:
        """Send one setting to the device (override). The default just keeps it."""
        self.set(entity_id, value)

    def services(self) -> dict[str, list[str]]:
        """The device's own services, e.g. {"keba": ["set_current", "enable", "disable"]}."""
        return {}

    def energy_prefs(self) -> list[dict[str, Any]]:
        """Entries for HA's Energy dashboard (helps auto-discovery)."""
        return []
