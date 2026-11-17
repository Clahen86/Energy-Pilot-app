"""Smoke test: Energy Pilot's core starts on the virtual Home Assistant with the simulated home, finds the devices,
picks the Growatt SPH profile and answers the panel's API.   python3 -m unittest discover tests"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Standalone(unittest.TestCase):
    def test_demo_home(self):
        os.environ["DATA_DIR"] = tempfile.mkdtemp()
        sys.path.insert(0, str(ROOT / "www" / "py"))
        from standalone import boot

        async def go():
            boot.start("demo")
            for _ in range(100):
                if boot.ready():
                    break
                await asyncio.sleep(0.1)
            self.assertTrue(boot.ready())
            await asyncio.sleep(35)  # main() waits 20 s before the first engine round
            ov = json.loads(await boot.handle("GET", "/api/overview", '{"lang": "da"}'))
            self.assertEqual(ov["status"], 200)
            data = json.loads(ov["body"])
            self.assertTrue(data["flow"]["has"]["battery"])
            self.assertTrue(data["flow"]["has"]["ev"])
            self.assertTrue(data["plan"]["hours"])
            ctl = json.loads(json.loads(await boot.handle("GET", "/api/control"))["body"])
            calls = json.dumps(ctl["battery"]["now"])
            self.assertIn("growatt", json.dumps(ctl))
            self.assertIn("intent", calls)

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
