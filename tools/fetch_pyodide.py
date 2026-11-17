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


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "energipilot-build"})
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310
        return r.read()


version = os.environ.get("PYODIDE_VERSION") or json.loads(get("https://api.github.com/repos/pyodide/pyodide/releases/latest"))["tag_name"]
version = version.lstrip("v")
print("Pyodide", version)
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
core = get(f"https://github.com/pyodide/pyodide/releases/download/{version}/pyodide-core-{version}.tar.bz2")
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
