# Energipilot – selvstændig app

Energipilot som en almindelig mobilapp til iPhone og Android – **uden Home Assistant**.

Appen kører **den samme Python-kerne** som Home Assistant-appen [Energy-pilot](https://github.com/Clahen86/Energy-pilot):
planlægning i kvarterstimer, læring med ML, auto-genkendelse, profiler til invertere og ladere, besparelser,
selvtest og hele panelet. Forskellen er kun, hvor enhederne kommer fra.

## Sådan virker det

```
┌──────────────── appen (WebView) ────────────────┐
│  panelet (samme index.html som i HA-appen)      │
│        │  fetch("api/…")                        │
│        ▼                                        │
│  js/bridge.js  ──►  Pyodide (Python i appen)    │
│                       energy_pilot/*  (uændret) │
│                       standalone/virtual_ha.py  │
│                       standalone/devices/*      │
└───────────────────────────│─────────────────────┘
                            ▼
          inverter · lader · priser (Modbus, UDP, cloud …)
```

- **Pyodide** kører Python med numpy og scikit-learn direkte i appen.
- **`standalone/virtual_ha.py`** er et lille Home Assistant med samme interface som `energy_pilot/ha.py`
  (states, services, recorder/statistik, registry). Kernen mærker ingen forskel.
- **Drivere** (`standalone/devices/`) laver de samme entities, som HA-integrationerne ville lave
  (`select.growatt_work_mode_priority`, `keba.set_current` …). Derfor virker auto-genkendelse og profilerne i
  `adapters.py` uden ændringer.
- **`aiohttp`-shim** (`www/py/aiohttp/`) sender kernens HTTP-kald gennem appens fetch (native HTTP i Capacitor).
- Intet sendes ud af telefonen (rapport-tjenesten er slået fra).

## Status

| Del | Status |
|---|---|
| Energy Pilot-kernen og panelet | ✅ kører (samme kode som HA-appen, commit `430feaf`) |
| Demo: simuleret Growatt SPH + KEBA + Energi Data Service | ✅ |
| Driver: Growatt SPH (Modbus TCP) | 🚧 næste skridt |
| Driver: KEBA (UDP 7090) | 🚧 |
| Andre invertere/ladere | planlagt – samme mønster |
| Kørsel i baggrunden | Android: forgrundstjeneste (planlagt). iPhone: kun mens appen er åben |

## Prøv den

**I browseren (Pyodide):** åbn `www/index.html` via en webserver, fx GitHub Pages
(Settings → Pages → Deploy from branch → `main` / root) → `https://clahen86.github.io/energy-pilot-app/www/`.

**Hurtigt på computeren (uden Pyodide):** samme kerne i almindelig Python.

```bash
pip install numpy scikit-learn
python3 tools/dev_server.py      # åbn http://localhost:8099/?dev=1
```

## Byg som rigtig app

```bash
npm install
npx cap add android              # første gang
npx cap add ios                  # første gang (Mac med Xcode)
npm run android                  # eller: npm run ios
```

Til den native app bør Pyodide ligge i pakken, så den virker uden net ved opstart: hent Pyodide-distributionen
og læg den i `www/pyodide/` (bridge.js bruger den lokale kopi først og ellers jsDelivr).

## Opdatér kernen fra Home Assistant-appen

```bash
cp -r ../Energy-pilot/energy_pilot/energy_pilot/*.py www/py/energy_pilot/
cp ../Energy-pilot/energy_pilot/energy_pilot/frontend/index.html www/panel.html
cp ../Energy-pilot/energy_pilot/energy_pilot/frontend/suggest.js www/static/
python3 tools/build_manifest.py && python3 tools/build_index.py
python3 -m unittest discover tests
```

## Struktur

```
www/
  index.html            panelet + bro (bygges af tools/build_index.py fra panel.html)
  panel.html            panelet, uændret fra HA-appen
  js/bridge.js          Pyodide, filer, API-routing
  py/energy_pilot/      kernen, uændret fra HA-appen
  py/standalone/        virtuel Home Assistant, opstart og drivere
  py/aiohttp/           lille aiohttp-erstatning (fetch)
  py/bundle.json        al Python-koden i én fil (tools/build_manifest.py)
tools/                  byg- og udviklingsværktøjer
tests/                  røgtest af den selvstændige kerne
```

## Licens

MIT
