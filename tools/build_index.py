"""Makes www/index.html from the panel (www/panel.html, the same as in the Home Assistant app) by adding the bridge
to the Python core and the app's icon and manifest. Run after updating panel.html from the HA repo."""
from pathlib import Path

www = Path(__file__).resolve().parent.parent / "www"
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
(www / "index.html").write_text(html)
print("www/index.html", len(html))
