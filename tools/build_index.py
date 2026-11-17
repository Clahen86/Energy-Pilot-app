"""Makes www/app.html from the panel (www/panel.html, the same as in the Home Assistant app) by adding the bridge to
the Python core and the app's icon and manifest, and www/version.json, which the app uses to update itself.
Run after tools/build_manifest.py (the bundle must be current)."""
import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
www = ROOT / "www"
html = (www / "panel.html").read_text()
head = ('<meta charset="utf-8">\n'
        '<meta name="theme-color" content="#06080d">\n'
        '<meta name="apple-mobile-web-app-capable" content="yes">\n'
        '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">\n'
        '<link rel="manifest" href="manifest.webmanifest">\n'
        '<link rel="icon" href="icon.svg" type="image/svg+xml">\n'
        '<link rel="apple-touch-icon" href="icon.svg">\n'
        '<script src="js/bridge.js"></script>\n')
assert '<meta charset="utf-8">\n' in html
html = html.replace('<meta charset="utf-8">\n', head, 1).replace("<html>", '<html lang="da">', 1)
(www / "app.html").write_text(html)

# version.json: what the app compares with the newest on GitHub Pages to update itself (see bridge.js).
def digest(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]

web_files = [www / "app.html", www / "js" / "bridge.js", www / "py" / "bundle.json", www / "static" / "suggest.js"]
native_files = sorted((ROOT / "android-src").glob("*.java")) + [ROOT / "capacitor.config.json"]
vid, native = digest(web_files), digest(native_files)
vf = www / "version.json"
old = {}
try:
    old = json.loads(vf.read_text())
except (OSError, ValueError):
    pass
core = json.loads((ROOT / "CORE_SOURCE.json").read_text()).get("version", "?")
built = old.get("built") if old.get("id") == vid else int(time.time())
vf.write_text(json.dumps({"id": vid, "built": built, "core": core, "native": native}, indent=2) + "\n")
print("www/app.html", len(html), "· version", vid, "core", core)
