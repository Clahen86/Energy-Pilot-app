"""After `npx cap add android`: the app's own Java (UDP plugin), signing with a fixed key (so new builds install over
the old one), network permissions, and a version number per build."""
import os
import re
import shutil
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
android = APP / "android"
java = android / "app" / "src" / "main" / "java" / "dk" / "energipilot" / "app"
java.mkdir(parents=True, exist_ok=True)
for f in ("EpUdpPlugin.java", "MainActivity.java"):
    shutil.copyfile(APP / "android-src" / f, java / f)

gradle = android / "app" / "build.gradle"
g = gradle.read_text()
signing = """
    signingConfigs {
        energipilot {
            storeFile file(System.getenv("EP_KEYSTORE") ?: "../../android-src/energipilot-test.keystore")
            storePassword System.getenv("EP_KEYSTORE_PASSWORD") ?: "energipilot"
            keyAlias System.getenv("EP_KEY_ALIAS") ?: "energipilot"
            keyPassword System.getenv("EP_KEY_PASSWORD") ?: "energipilot"
        }
    }
"""
if "signingConfigs" not in g:
    g = re.sub(r"(android\s*\{)", r"\1" + signing.replace("\\", "\\\\"), g, count=1)
if not re.search(r"buildTypes\s*\{[^}]*debug\s*\{", g):
    g = re.sub(r"(buildTypes\s*\{)", r"\1\n        debug {\n            signingConfig signingConfigs.energipilot\n        }", g, count=1)
g = re.sub(r"(release\s*\{)", r"\1\n            signingConfig signingConfigs.energipilot", g, count=1)
run = int(os.environ.get("GITHUB_RUN_NUMBER", "1"))
g = re.sub(r"versionCode\s+\d+", f"versionCode {run}", g)
g = re.sub(r'versionName\s+"[^"]*"', f'versionName "0.3.{run}"', g)
gradle.write_text(g)

manifest = android / "app" / "src" / "main" / "AndroidManifest.xml"
m = manifest.read_text()
for perm in ("ACCESS_NETWORK_STATE", "ACCESS_WIFI_STATE", "WAKE_LOCK", "CHANGE_WIFI_MULTICAST_STATE"):
    tag = f'<uses-permission android:name="android.permission.{perm}" />'
    if tag not in m:
        m = m.replace("</manifest>", f"    {tag}\n</manifest>")
if "usesCleartextTraffic" not in m:
    m = m.replace("<application", '<application\n        android:usesCleartextTraffic="true"', 1)
manifest.write_text(m)
if "signingConfigs" not in gradle.read_text():
    sys.exit("kunne ikke sætte signering op")
print("android patched: version", run)
