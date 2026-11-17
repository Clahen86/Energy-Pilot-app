"""Copy Energy Pilot's core from a checkout of the Home Assistant app into the app (never the other way).

    python3 tools/sync_core.py ../Energy-pilot
Then: the bundle and index.html are rebuilt; run the tests before committing."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core_files import APP, ha_version, pairs  # noqa: E402

ha = Path(sys.argv[1]).resolve()
core_dir = APP / "www" / "py" / "energy_pilot"
wanted = pairs(ha)
keep = {dst for _, dst in wanted}
for p in list(core_dir.rglob("*")):  # files removed in the HA app go here too
    if p.is_file() and p not in keep and "__pycache__" not in p.parts:
        p.unlink()
for src, dst in wanted:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
commit = subprocess.run(["git", "-C", str(ha), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
(APP / "CORE_SOURCE.json").write_text(json.dumps({"repo": "Clahen86/Energy-pilot", "commit": commit, "version": ha_version(ha)}, indent=2) + "\n")
subprocess.run([sys.executable, str(APP / "tools" / "build_manifest.py")], check=True)
subprocess.run([sys.executable, str(APP / "tools" / "build_index.py")], check=True)
print("core", ha_version(ha), commit[:7])
