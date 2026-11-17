"""Put Pyodide (Python for the app) and the packages Energy Pilot needs into www/pyodide/, so the Android app starts
without the internet. Used by the Android build; bridge.js uses this copy first and the CDN otherwise.

    python3 tools/fetch_pyodide.py            (PYODIDE_VERSION=0.28.3 to pin a version)"""
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "www" / "pyodide"
WANT = ["numpy", "scikit-learn", "tzdata"]


KNOWN = ["0.29.0", "0.28.3", "0.28.2", "0.28.1", "0.28.0", "0.27.7"]  # tried in order if the newest can't be looked up


def get(url: str) -> bytes:
    headers = {"User-Agent": "energipilot-build"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310
        return r.read()


candidates = [os.environ["PYODIDE_VERSION"]] if os.environ.get("PYODIDE_VERSION") else []
if not candidates:
    try:
        candidates.append(json.loads(get("https://api.github.com/repos/pyodide/pyodide/releases/latest"))["tag_name"])
    except Exception as err:  # noqa: BLE001
        print("kunne ikke slå nyeste Pyodide op:", err)
    candidates += KNOWN
core, version = None, ""
for v in candidates:
    v = v.lstrip("v")
    try:
        core = get(f"https://github.com/pyodide/pyodide/releases/download/{v}/pyodide-core-{v}.tar.bz2")
        version = v
        break
    except Exception as err:  # noqa: BLE001
        print(f"Pyodide {v}: {err}")
if core is None:
    sys.exit("Pyodide kunne ikke hentes")
print("Pyodide", version)
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
with tarfile.open(fileobj=io.BytesIO(core), mode="r:bz2") as tar:
    for m in tar.getmembers():
        if not m.isfile():
            continue
        name = m.name.split("/", 1)[1] if "/" in m.name else m.name
        if ".." in Path(name).parts:
            continue
        dst = OUT / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(tar.extractfile(m).read())
lock = json.loads((OUT / "pyodide-lock.json").read_text())
pkgs = {k.lower(): v for k, v in lock["packages"].items()}
need, todo = set(), [w.lower() for w in WANT]
while todo:
    n = todo.pop()
    if n in need:
        continue
    if n not in pkgs:
        sys.exit(f"Pyodide {version} mangler pakken {n}")
    need.add(n)
    todo.extend(d.lower() for d in pkgs[n].get("depends", []))
total = 0
for n in sorted(need):
    meta = pkgs[n]
    data = get(f"https://cdn.jsdelivr.net/pyodide/v{version}/full/{meta['file_name']}")
    if meta.get("sha256") and hashlib.sha256(data).hexdigest() != meta["sha256"]:
        sys.exit(f"forkert kontrolsum for {meta['file_name']}")
    (OUT / meta["file_name"]).write_bytes(data)
    total += len(data)
    print(f"  {n}: {len(data) // 1024} KB")
print(f"{len(need)} pakker, {total // 1048576} MB")
