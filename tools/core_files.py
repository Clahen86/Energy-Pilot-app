"""Which files are Energy Pilot's shared core (copied unchanged from the Home Assistant app) - used by sync and check."""
from pathlib import Path

APP = Path(__file__).resolve().parent.parent

# (path in the HA repo, path in the app)
def pairs(ha: Path) -> list[tuple[Path, Path]]:
    core = ha / "energy_pilot" / "energy_pilot"
    out = [(p, APP / "www" / "py" / "energy_pilot" / p.relative_to(core))
           for p in sorted(core.rglob("*")) if p.is_file() and "frontend" not in p.relative_to(core).parts
           and "__pycache__" not in p.parts]
    out.append((core / "frontend" / "index.html", APP / "www" / "panel.html"))
    out.append((core / "frontend" / "suggest.js", APP / "www" / "static" / "suggest.js"))
    return out


def ha_version(ha: Path) -> str:
    for line in (ha / "energy_pilot" / "config.yaml").read_text().splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"')
    return "?"
