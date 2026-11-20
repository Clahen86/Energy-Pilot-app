"""Energy Pilot's core on the virtual Home Assistant, with the app's real drivers (SolarAssistant over WebSocket,
KEBA over UDP) against simulated devices.   python3 -m unittest discover tests"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "www" / "py"))


class Standalone(unittest.TestCase):
    def test_home_through_the_drivers(self):
        data = Path(tempfile.mkdtemp())
        os.environ["DATA_DIR"] = str(data)
        # As if the guide is done and the user turned control on for battery and car
        (data / "energy_pilot_settings.json").write_text(json.dumps(
            {"language": "da", "setup_done": True, "control": {"battery": "control", "car": "control"}}))
        import standalone.boot as boot
        from standalone.devices import simulators

        # The inverter starts as its owner left it (Battery first), so Energy Pilot has something to change - a core
        # that only writes values that aren't already set sends nothing when its plan matches the simulator's defaults.
        sph_init = simulators.SphPhysics.__init__

        def left_by_owner(sph, *args, **kwargs):
            sph_init(sph, *args, **kwargs)
            sph.s["work_mode_priority"] = "Battery first"

        simulators.SphPhysics.__init__ = left_by_owner
        self.addCleanup(setattr, simulators.SphPhysics, "__init__", sph_init)
        boot.DATA = data

        async def go():
            boot.start("demo")
            for _ in range(100):
                if boot.ready():
                    break
                await asyncio.sleep(0.1)
            self.assertTrue(boot.ready())
            info = json.loads(boot.info())
            sa = next(d for d in info["devices"] if d["platform"] == "solar_assistant")
            keba = next(d for d in info["devices"] if d["platform"] == "keba")
            self.assertEqual(sa["status"], "forbundet")
            self.assertEqual(sa["missing"], [])
            self.assertEqual(keba["status"], "forbundet")

            await asyncio.sleep(30)  # main() waits 20 s before the first engine round
            ctl: dict = {}
            for _ in range(60):  # the engine's first round (slower on a busy machine)
                ctl = json.loads(json.loads(await boot.handle("GET", "/api/control"))["body"])
                if (ctl.get("battery") or {}).get("profile") and any(
                        s["domain"] in ("select", "number", "switch") for s in boot._ha.service_log):
                    break
                await asyncio.sleep(2)
            ov = json.loads(json.loads(await boot.handle("GET", "/api/overview", '{"lang": "da"}'))["body"])
            self.assertTrue(ov["flow"]["has"]["battery"] and ov["flow"]["has"]["ev"] and ov["flow"]["has"]["pv"])
            self.assertTrue(ov["plan"]["hours"])
            self.assertEqual(ctl["battery"]["profile"]["key"], "growatt")
            self.assertEqual(ctl["battery"]["profile"]["check"]["charge"]["missing"], [])
            self.assertIn("growatt_sph", json.dumps(ctl["battery"]["profile"]))
            # In control: Energy Pilot's commands reached the simulated inverter through SolarAssistant's API
            told = [s for s in boot._ha.service_log if s["domain"] in ("select", "number", "switch")]
            self.assertTrue(told, "nothing was sent to the inverter")
            self.assertTrue(all(e.startswith(("select.growatt_sph_", "number.growatt_sph_", "switch.growatt_sph_"))
                                for s in told for e in s["entity_id"]))
            errors = [line for line in boot.LOG if "SolarAssistant:" in line or "could not" in line.lower()]
            self.assertEqual(errors, [])

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
