# Energipilot – selvstændig app

Energipilot som en almindelig app – **uden Home Assistant**. Appen kører **den samme Python-kerne og det samme panel**
som Home Assistant-appen [Energy-pilot](https://github.com/Clahen86/Energy-pilot): plan i kvarterstimer, læring,
auto-genkendelse, profiler til invertere og ladere, besparelser og selvtest.

> Dette repo ændrer aldrig noget i Home Assistant-appen. Kernen kopieres kun **fra** Energy-pilot hertil.

## Sådan virker det

```
┌──────────────────── appen (Android / browser) ────────────────────┐
│  panelet (samme som i HA-appen)  ──fetch("api/…")──►  js/bridge.js │
│                                                        │           │
│  Pyodide (Python i appen):                             ▼           │
│    energy_pilot/*            kernen, uændret fra HA-appen          │
│    standalone/virtual_ha.py  et lille "Home Assistant" i appen     │
│    standalone/devices/*      drivere: SolarAssistant, KEBA, …      │
└────────────────────────────────│───────────────────────────────────┘
                                 ▼  på dit hjemmenetværk
               SolarAssistant (WebSocket)   KEBA (UDP 7090)   priser og vejr (internet)
```

Driverne laver **de samme entities som i Home Assistant** (`sensor.growatt_sph_pv_power`,
`select.growatt_sph_work_mode_priority`, `sensor.keba_p30_charging_power`, `keba.set_current` …). Derfor bruger appen
præcis de samme profiler som HA-appen – for en Growatt SPH "Growatt (SPH/SPA)".

## Drivere

| Enhed | Forbindelse | Status |
|---|---|---|
| Inverter via **SolarAssistant** (Growatt SPH m.fl.) | lokalt WebSocket-API | ✅ testet mod simulator – klar til test derhjemme |
| **KEBA** P20/P30 | lokal UDP 7090 | ✅ testet mod simulator – klar til test derhjemme (kræver Android-appen) |
| Demo (simuleret SolarAssistant + KEBA) | – | ✅ |
| MQTT, Modbus TCP, OCPP, cloud-API'er | – | planlagt |

## Kom i gang

1. **Installér appen på Android:** åbn
   [udgivelsen "android-latest"](https://github.com/Clahen86/Energy-Pilot-app/releases/tag/android-latest) på telefonen,
   hent `Energipilot.apk` og installér (tillad "installér ukendte apps" for din browser første gang).
2. **Forbind:** tryk 🔌 (nederst til højre) → "Mit hjem" → SolarAssistants IP og lokale adgangskode
   (SolarAssistant → Configuration) og KEBA'ens IP → "Gem og genstart".
3. Under 🔌 → Status ses om begge er forbundet, og alle emner SolarAssistant sender. Står der
   "Indstillinger SolarAssistant ikke har meldt", så send listen – så rettes oversættelsen.
4. Appen starter i **skygge** ligesom HA-appen. Kør "Test dit system" under Styring, før du slår styring til.

Uden Android: [GitHub Pages](https://clahen86.github.io/Energy-Pilot-app/) viser demoen i browseren.

**Telefonen skal være hjemme og appen åben** for at styre. Til fast drift: en gammel Android-telefon/tablet i strøm
derhjemme med appen åben. (Kørsel i baggrunden kommer senere.)

## Opdateringer kommer af sig selv

Appen henter selv nye versioner af panelet og kernen fra GitHub Pages (`www/version.json`) ved opstart og hver 3. time,
og viser "Energipilot x.y er hentet – Genstart". Startsiden (`www/index.html`) kører den nyeste version, appen har.
Kun når selve Android-delen ændres (`android-src/`, `capacitor.config.json`), skal en ny APK installeres – så siger
appen det. `?ota=0` starter altid den indbyggede version.

## Kernen følger Home Assistant-appen

- `CORE_SOURCE.json` siger, hvilken version af Energy-pilot appen bygger på.
- **Hent ny kerne fra Home Assistant-appen** (GitHub Actions, hver 6. time eller med "Run workflow"): kopierer den
  nyeste kerne, kører testene, og hvis de består: gemmer og bygger en ny Android-app.
- **Ret kernen i Energy-pilot, ikke her.** Bygget stopper, hvis kopien her er ændret (`tools/check_core.py`).
- Det, der kun findes i appen: `www/py/standalone/`, `www/py/aiohttp/`, `www/js/bridge.js`, `android-src/`, `tools/`.
- Indstillinger deles ikke: HA-appen og mobilappen gemmer hver deres.

## Udvikling

```bash
pip install numpy scikit-learn
python3 -m unittest discover tests        # kernen + drivere mod simulatorer
python3 tools/dev_server.py               # demo på http://localhost:8099/?dev=1
EP_MODE=home EP_CONFIG='{"solarassistant": {"host": "192.168.1.20", "password": "…"}, "keba": {"host": "192.168.1.30"}}' \
  python3 tools/dev_server.py             # dine egne enheder fra en computer på hjemmenetværket
python3 tools/sync_core.py ../Energy-pilot  # ny kerne fra en lokal kopi af HA-appen
```

Android lokalt (Android Studio): `npm install && python3 tools/fetch_pyodide.py && npx cap add android &&
python3 tools/patch_android.py && npx cap sync android && npx cap open android`.

Signering: `android-src/energipilot-test.keystore` er en **testnøgle** (åben i repoet), så nye byg kan installeres oven
på de gamle. Til Google Play: brug en hemmelig nøgle via `EP_KEYSTORE`, `EP_KEYSTORE_PASSWORD`, `EP_KEY_ALIAS`, `EP_KEY_PASSWORD`.

## Struktur

```
www/index.html            startside: kører den nyeste version (indbygget eller hentet)
www/app.html              panelet + bro (bygges af tools/build_index.py)
www/version.json          versionen, appen sammenligner med for at opdatere sig selv
www/panel.html            panelet, uændret fra HA-appen
www/js/bridge.js          starter kernen, API-routing, forbindelses-skærm, opdatering
www/js/worker.js          Python-kernen (Pyodide) i en baggrundstråd
www/py/energy_pilot/      kernen, uændret fra HA-appen
www/py/standalone/        virtuel Home Assistant, opstart, drivere og simulatorer
www/py/aiohttp/           lille aiohttp-erstatning (fetch)
www/py/bundle.json        al Python-koden i én fil (tools/build_manifest.py)
android-src/              UDP-plugin, MainActivity, testnøgle
tools/                    byg, sync, tjek, dev-server
.github/workflows/        Android-byg og kerne-sync
```

## Licens

MIT
