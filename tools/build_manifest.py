"""Packs the Python code the app loads into Pyodide into one file (www/py/bundle.json: {path: source}).
Run after changing anything in www/py (and after copying a newer core from the Home Assistant app)."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "www" / "py"
files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
bundle = {str(p.relative_to(root)): p.read_text() for p in files}
(root / "bundle.json").write_text(json.dumps(bundle))
print(len(bundle), "files,", sum(map(len, bundle.values())) // 1024, "KB")
