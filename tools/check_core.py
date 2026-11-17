"""Check that the app's copy of the core is exactly the Home Assistant app's (at the commit in CORE_SOURCE.json).
Changes to the core belong in the Home Assistant app's repo; this fails if someone edited the copy here.

    python3 tools/check_core.py ../Energy-pilot   (a checkout at that commit)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core_files import APP, pairs  # noqa: E402

ha = Path(sys.argv[1]).resolve()
bad = []
wanted = pairs(ha)
for src, dst in wanted:
    if not dst.exists() or dst.read_bytes() != src.read_bytes():
        bad.append(str(dst.relative_to(APP)))
extra = [str(p.relative_to(APP)) for p in (APP / "www" / "py" / "energy_pilot").rglob("*.py")
         if "__pycache__" not in p.parts and p not in {d for _, d in wanted}]
if bad or extra:
    print("Kernen i appen er ikke den samme som i Home Assistant-appen. Ret i Energy-pilot-repoet i stedet:")
    for b in bad + extra:
        print("  ", b)
    sys.exit(1)
print("Kernen er uændret:", len(wanted), "filer")
