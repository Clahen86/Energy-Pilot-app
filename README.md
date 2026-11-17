# Energipilot – selvstændig app

Energipilot som en almindelig mobilapp til iPhone og Android, **uden Home Assistant**.
Den planlægger hjemmebatteri, solceller og elbil efter de billigste elpriser i kvarterstimer.

Søsterprojekt til [Energy-pilot](https://github.com/Clahen86/Energy-pilot) (Home Assistant-appen).

## Hvad den kan

- **Spotpriser** for DK1/DK2 i kvarterer fra Energi Data Service, inkl. nettarif, afgifter og moms.
  Før kl. 13 estimeres morgendagen, så bilen kan planlægges hen over natten.
- **Sol- og vejrprognose** fra Open-Meteo.
- **Plan for batteriet**: optimal opladning/afladning (dynamisk programmering over ladetilstand).
- **Plan for elbilen**: de billigste kvarterer før klar-tidspunktet, solcelleoverskud først. Knap til "Lad nu".
- **Lærer husets forbrug** pr. ugedag og time.
- **Besparelser**: samme hus med og uden batteri, plus hvad den lagrede strøm har kostet.
- **Skyggetilstand** som standard – appen viser hvad den ville gøre uden at styre noget.
- **Drivere** til forskellige invertere og ladere bag ét fælles interface (`www/js/drivers.js`).
- Dansk brugerflade. Alle data og konti gemmes kun på telefonen.

## Status for drivere

| Enhed | Forbindelse | Status |
|---|---|---|
| Demo (simuleret hus og elbil) | – | ✅ klar |
| Growatt SPH / MIN / MOD | cloud (OpenAPI) | 🚧 under udvikling |
| KEBA KeContact | lokal UDP 7090 | 🚧 under udvikling |
| Fox ESS, GivEnergy, Deye, Sungrow, Huawei | cloud | planlagt |
| SolaX, SolarEdge | lokal Modbus TCP | planlagt |
| Easee, Zaptec, Wallbox | cloud | planlagt |
| go-e | lokal HTTP | planlagt |

## Prøv den i browseren

Appen er ren HTML/JavaScript, så den kan køre direkte i en browser:

```bash
npm run serve        # åbn http://localhost:8080
```

Eller slå **GitHub Pages** til for repoet (Settings → Pages → Deploy from branch → `main` / root),
så kører den på `https://clahen86.github.io/energy-pilot-app/` med live priser og vejr.
På telefonen: åbn siden og vælg "Føj til hjemmeskærm".

## Byg som rigtig app (Android og iPhone)

Appen pakkes med [Capacitor](https://capacitorjs.com), så den samme kode bliver en native app.

```bash
npm install
npx cap add android      # første gang
npx cap add ios          # første gang (kræver Mac med Xcode)
npm run android          # åbner projektet i Android Studio → Run
npm run ios              # åbner projektet i Xcode → Run
```

I den native app bruges native HTTP (`CapacitorHttp`), så producenternes API'er kan kaldes direkte.
Lokale protokoller (KEBA UDP, Modbus TCP) kræver et ekstra Capacitor-plugin og at telefonen er på samme wifi.

## Struktur

```
www/
  index.html          brugerflade
  css/style.css
  js/planner.js       priser, prognoser og optimering (ingen afhængigheder)
  js/sources.js       Energi Data Service og Open-Meteo (med demodata som reserve)
  js/drivers.js       driver-interface og drivere til invertere og ladere
  js/app.js           skærme, live-visning og styring
tests/                tests af planlæggeren (npm test)
capacitor.config.json
```

## Tilføj en ny inverter eller lader

1. Tilføj den til `EP.DRIVERS` i `www/js/drivers.js`.
2. Skriv en klasse med `getLive()` og `applyPlan(plan)` (ladere også `chargeNow(on)`).
3. Registrér den i `EP.createInverter` / `EP.createCharger`.

Invertere, der kun kan gemme et fast antal tidsrum (fx Growatt SPH med 3), kan bruge
`EP.compressToBlocks(plan, 3)` til at gøre planen om til de vigtigste lade- og afladeblokke.

## Licens

MIT
