"""Starts Energy Pilot's own main() inside the app, with the virtual Home Assistant in place of the real one.

Everything else is Energy Pilot's own code, unchanged: the engine, learning, the controller, the ledger, the
self-test, the panel's API. The app's panel calls handle() for each /api/... request.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

DATA = Path(os.environ.setdefault("DATA_DIR", "/data"))
os.environ.setdefault("EP_REPORT_URL", "")  # nothing leaves the phone
os.environ.setdefault("BUILD_VERSION", "app")
os.environ.setdefault("APPS_DIR", str(DATA / "apps"))

_app: Any = None
_ha: Any = None
_task: asyncio.Task | None = None
LOG: list[str] = []


class _Collect(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            LOG.append(f"{record.levelname[0]} {record.name.replace('energy_pilot.', '')}: {record.getMessage()}")
            del LOG[:-300]
        except Exception:  # noqa: BLE001
            pass


def _install(devices: list[Any], config: dict[str, Any] | None) -> None:
    """Put the virtual Home Assistant where main.py looks for the real one, and catch the panel's API."""
    global _ha
    from standalone.virtual_ha import VirtualHA

    import energy_pilot.ha as ha_mod
    import energy_pilot.web as web_mod

    ha = VirtualHA(devices, config, DATA)
    _ha = ha
    ha_mod.HomeAssistant = lambda: ha  # type: ignore[assignment]
    original = web_mod.make_app

    def make_app(runner: Any, overview: Any) -> Any:
        global _app
        _app = original(runner, overview)
        _app["overview"] = overview
        return _app

    web_mod.make_app = make_app
    import energy_pilot.main as main_mod

    main_mod.HomeAssistant = ha_mod.HomeAssistant
    main_mod.make_app = make_app


def start(mode: str = "demo", config_json: str = "{}") -> None:
    """mode "demo": a simulated home. Later: "devices" with the user's own drivers."""
    global _task
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger().addHandler(_Collect())
    DATA.mkdir(parents=True, exist_ok=True)
    config = json.loads(config_json or "{}")
    if mode == "demo":
        from standalone.devices.demo import make_demo, seed_history

        devices, world = make_demo()
        _install(devices, config)
        seed_history(_ha, world)
        _seed_settings()
    else:
        raise ValueError(f"unknown mode {mode}")
    import energy_pilot.main as main_mod

    _task = asyncio.ensure_future(main_mod.main())


def _seed_settings() -> None:
    """The demo starts as if the guide was done: Danish, Danish prices, a 10 kWh battery."""
    f = DATA / "energy_pilot_settings.json"
    if f.exists():
        return
    f.write_text(json.dumps({"language": "da", "price_area": "DK2", "capacity_kwh": 10, "max_power_kw": 5}))


def ready() -> bool:
    return _app is not None and _ha is not None and _ha.connected.is_set()


async def handle(method: str, path: str, query_json: str = "{}", body: str = "") -> str:
    """One request from the panel. Returns JSON: {"status", "content_type", "body"}."""
    from aiohttp import web

    for _ in range(600):
        if _app is not None:
            break
        if _task is not None and _task.done():
            exc = _task.exception()
            return json.dumps({"status": 500, "content_type": "application/json", "body": json.dumps({"error": repr(exc)})})
        await asyncio.sleep(0.1)
    if _app is None:
        return json.dumps({"status": 503, "content_type": "application/json", "body": json.dumps({"error": "starting"})})
    req = web.Request(method, path, json.loads(query_json or "{}"), body.encode() if body else None)
    try:
        resp = await _app.handle(req)
    except Exception as err:  # noqa: BLE001
        logging.getLogger("energy_pilot.app").warning("API %s %s failed", method, path, exc_info=True)
        return json.dumps({"status": 500, "content_type": "application/json", "body": json.dumps({"error": str(err)})})
    return json.dumps({"status": resp.status, "content_type": resp.content_type, "body": resp.text})


def info() -> str:
    """For the app's own status line: what the devices were last told, and the log."""
    return json.dumps({
        "ready": ready(),
        "entities": len(_ha.states) if _ha else 0,
        "services": (_ha.service_log[-20:] if _ha else []),
        "log": LOG[-60:],
    })


def save() -> None:
    if _ha:
        _ha.recorder.save()
