# AutoOutlook

**Automated Convective Risk Intelligence**

A fully automated severe-weather outlook dashboard and backend artifact pipeline. The live dashboard loads the latest HRRR-derived forecast bundle, while the deployable pipeline can generate SPC-style HRRR/XGBoost map artifacts for forecast hours `0..48` without user input.

Designed in a neo-brutalist / RetroUI aesthetic (thick borders, hard offset shadows, bold cards, scanline overlays, monospace accents).

## Stack

- **Frontend**: Vite + React 18 + TypeScript + Tailwind CSS
- **Map**: `react-simple-maps` over the US states topojson
- **Data layer (3-tier provider chain)**:
  1. **Python backend** (Flask + HRRR GRIB2 byte-range filtering + MetPy-style diagnostics) — pulls selected HRRR fields, derives severe-weather ingredients, and activates XGBoost hazard probabilities when model artifacts are valid.
  2. **Open-Meteo** (browser-side) — free GFS-Seamless JSON endpoints used as an automatic fallback if the backend isn't running.
  3. **Mock** — deterministic Plains severe-weather day used when both live providers fail.
- **Deployable artifact pipeline**: `backend.ml.outlook_pipeline` detects the latest available extended HRRR cycle, processes forecast hours `0..48`, writes GeoJSON/probability/metadata/preview artifacts, then fetches the current SPC Day 1 outlook only for verification.

## Run

You'll need **two terminals** for the full live experience.

### 1) Frontend (always required)

```powershell
npm install
npm run dev
```

Vite serves on `http://localhost:5173`. The dev server proxies `/api/*` to the backend on `http://127.0.0.1:8765`.

If you stop here, AutoOutlook still works — it'll fall through to Open-Meteo, then to mock data. You'll see a `LIVE` badge for Open-Meteo and a `FALLBACK` badge for mock.

### 2) Backend (optional — for NOMADS-direct data)

One-time install of the only missing Python dependency:

```powershell
python -m pip install netCDF4
```

Everything else (Flask, flask-cors, siphon, MetPy, xarray, numpy, scipy) is already on your system.

Then either:

```powershell
.\backend\run.ps1
```

or:

```powershell
python -m backend.server
```

The service listens on `http://127.0.0.1:8765` with:

- `GET /api/forecast`
- `GET /api/health`
- `GET /api/outlook/latest`
- `GET /api/outlook/risk-polygons`
- `GET /api/outlook/aggregate-risk-polygons`
- `GET /api/outlook/probability-tiles`
- `GET /api/outlook/verification`
- `GET /api/outlook/preview.png`

When the backend is up, the dashboard's `SOURCE` badge will read the HRRR backend provider and the System Status panel will show `WINNER` next to the backend provider.

### 3) Deployable HRRR/XGBoost outlook artifacts

Generate the latest deployable outlook once:

```powershell
python -m backend.ml.outlook_pipeline
```

Run it as a scheduler loop:

```powershell
python -m backend.ml.outlook_pipeline --loop --interval-minutes 30
```

The pipeline writes to `backend/artifacts/latest/` by default. That directory is intentionally git-ignored because it contains generated runtime artifacts.

Important leakage guard: the pipeline writes prediction artifacts first, then downloads the current official SPC Day 1 GeoJSON for verification. The official SPC outlook is never passed into the model feature matrix.

## Project layout

```
src/
  App.tsx                          # composes the layout
  hooks/
    useAutoForecast.ts             # fetch + 15-min refresh
    useForecastHour.ts             # slider state + play/pause + keyboard
  utils/
    fetchLatestForecast.ts         # provider chain
    providers/
      pythonBackendProvider.ts     # /api/forecast (NOMADS+MetPy)
      openMeteoProvider.ts         # browser-side Open-Meteo fallback
      mockProvider.ts              # deterministic mock
    outlookEngine.ts               # ingredients -> RiskCategory + headline
    hazardEngine.ts                # tornado/hail/wind/flood probabilities
    discussionGenerator.ts         # auto forecast-discussion paragraph
    riskTimeline.ts                # morning/afternoon/evening/overnight
    ingredientsDerive.ts           # STP/SCP/EHI/SHIP composites
    polygonBuilder.ts              # stepped risk-area rings on the map
    mockForecastData.ts            # canned 7-stop bundle
  components/
    CommandHeader.tsx
    ForecastTimeSlider.tsx
    PrimaryOutlookBanner.tsx
    OutlookMapPanel.tsx
    HazardProbabilityBoard.tsx
    EnvironmentalIngredientsGrid.tsx
    ForecastDiscussion.tsx
    RiskTimeline.tsx
    WatchReadinessPanel.tsx
    SystemStatusPanel.tsx
    retro/                         # primitives: card, badge, button, panel, divider
  types/forecast.ts                # ForecastBundle, HourSnapshot, RiskCategory, ...

backend/
  server.py                        # Flask app (port 8765)
  bundle_builder.py                # builds the JSON bundle per request
  nomads_pipeline.py               # siphon/THREDDS/NCSS access
  metpy_diagnostics.py             # bulk shear, SRH surrogate, composites
  region_picker.py                 # auto-detect CONUS focus region
  cache.py                         # 10-min TTL cache per GFS cycle
  requirements.txt
  run.ps1

public/
  us-states-10m.json               # us-atlas topojson (114 KB)
```

## Interaction model

Per spec, the **only** interactive controls are:

- **Forecast-hour slider** — 7 stops: `Current · +3h · +6h · +9h · +12h · +18h · +24h`
- **Play / Pause** — auto-steps through the slider every 1.5 s
- **Previous / Next** — single-step navigation
- **Manual refresh** (in System Status) — re-fetch from the provider chain

Keyboard shortcuts:

- `← / →` previous / next hour
- `Space` play / pause

There is no search bar, station selector, dropdown, text input, upload, or manual mode.

## Forecast hours and refresh

- 7 forecast stops: `0, 3, 6, 9, 12, 18, 24` hours from current cycle.
- Auto-refresh every 15 minutes.
- All dashboard sections (banner, map, hazards, ingredients, discussion, timeline, readiness) update automatically on slider change.

## Design tokens

Tailwind `extend` in `tailwind.config.ts` adds:

- **Risk ramp** — TSTM lime → MRGL amber → SLGT orange → ENH red → MOD dark red → HIGH violet
- **Shadows** — `shadow-retro` (6px hard offset), `shadow-retro-lg` (10px), `shadow-retro-sm` (3px)
- **Fonts** — Space Grotesk (display), Inter (body), JetBrains Mono (mono accents)
- **Animations** — `pulse-dot`, `scan` (scanline), `ticker` (header marquee)
- All animations respect `prefers-reduced-motion`.

## Adding a new provider

1. Implement `ForecastProvider` in `src/utils/providers/yourProvider.ts`:

   ```ts
   export const yourProvider: ForecastProvider = {
     id: 'yours',
     label: 'Your data source',
     async fetchBundle(signal) {
       // ...
       return bundle;
     },
   };
   ```

2. Insert it into the chain in `src/utils/fetchLatestForecast.ts` at your preferred priority.

The TS engines will run on the bundle's ingredients and produce the displayed outlook automatically.

## Future extension: AWS NODD GRIB2 provider

The plan explicitly leaves a seam for an AWS GRIB2 provider (`s3://noaa-gfs-bdp-pds`, `s3://noaa-hrrr-bdp-pds`). Recommended approach when adding it:

- Use `.idx` byte-range subsetting to fetch only the GRIB messages you need (CAPE, CIN, dewpoint, winds at levels) rather than the full 100+ MB file.
- Decode with a WASM GRIB2 reader (`wgrib2-wasm` / `eccodes-wasm`) in a Web Worker.
- Or do the same on the Python backend with `cfgrib`/`pygrib` if you prefer keeping the browser thin.

## ML dataset gathering & historical archive

To power the XGBoost severe weather probability models (tornado, hail, wind), AutoOutlook utilizes a robust dataset generation pipeline designed for aggressive, concurrent deployment across cloud providers.

- **Historical Fetching**: The `backend.ml.gather_archive` script pulls historical `.idx` and `grib2` data from AWS S3 (`s3://noaa-hrrr-bdp-pds`). It uses byte-range subsetting to fetch only critical fields instead of downloading 100+ MB files per hour.
- **Concurrent Processing**: The pipeline deploys across multiple nodes (e.g., DigitalOcean droplets for summer severe convective days, AWS instances for winter/wind events).
- **Parquet Checkpointing**: Instead of loading everything into memory, nodes continually append incremental `.ckpt.parquet` rows locally while matching Storm Prediction Center (SPC) severe reports to the precise HRRR grid valid times.
- **Data Densities**: Configurable CLI inputs like `--points-per-hour` and `--forecast-hours` allow sweeping 12-48 hour extended outlook profiles per localized report.

```powershell
python -u -m backend.ml.gather_archive --years 2021 2022 --months 4 5 6 7 --points-per-hour 30 --forecast-hours 6 12 18 24 30
```

## ML archive training guardrails

The backend only activates XGBoost hazards when model artifacts are production-capable.

- Minimum training rows: `5000`
- Feature schema hash must match runtime
- Artifacts marked `datasetQuality.experimentalOnly = true` remain inactive unless explicitly opted in
- Archive gathering defaults to de-duplicating repeated feature+label rows

Recommended flow:

```powershell
# 1) Gather larger archive sample (defaults to dedupe feature+label duplicates)
python -m backend.ml.gather_archive `
  --years 2022 2023 2024 2025 `
  --months 3 4 5 6 `
  --cycles 0 6 12 18 `
  --forecast-hours 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 `
  --points-per-hour 10 `
  --negative-points-per-hour 2 `
  --output backend/ml_data/archive_samples.parquet

# 2) Train XGBoost artifacts
python -m backend.ml.train_xgboost --input backend/ml_data/archive_samples.parquet
```

Once `trainingRows >= 5000` and schema checks pass, `/api/forecast` will automatically begin returning active `mlHazards` and non-zero `mlHazardHours`.

## Out of scope

Skew-T, hodograph, raw data tables, manual inputs, dropdowns, search, station selectors, uploads, editable fields, rawinsonde-style tabs, glassmorphism, generic SaaS dashboard look.
