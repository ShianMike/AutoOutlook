# Replace Rule Engine With XGBoost Hazard Models

## Summary
Build an archive gatherer using SPC severe reports plus archived HRRR fields, train calibrated XGBoost classifiers for tornado/hail/wind probabilities, and use those ML probabilities as the primary source for displayed severe-weather outlooks. Flood and general thunder remain rule-derived for now.

Sources: [SPC WCM severe weather CSV data](https://origin-west-www-spc.woc.noaa.gov/wcm/index.html), [NOAA HRRR archive on AWS](https://registry.opendata.aws/noaa-hrrr-pds/).

## Key Changes
- Add `backend/ml/`:
  - `gather_archive.py`: builds a Parquet training dataset from SPC tornado/hail/wind reports and HRRR archive fields.
  - `train_xgboost.py`: trains one calibrated `XGBClassifier` each for tornado, hail, and wind.
  - `inference.py`: loads model artifacts once and returns hazard probabilities for live forecast ingredients.
- Archive gatherer defaults:
  - Pilot scope: warm-season months `3,4,5,6` for years `2022,2023,2024`.
  - Labels: positive if a matching SPC report occurs within `40 km` and within `[validTime, validTime + 1 hour)`.
  - Samples: HRRR `00/06/12/18Z` runs, forecast hours `0..48`; include `forecastHour` as a feature.
  - Features: current backend ingredient fields plus encoded composites: CAPE/CIN/dewpoint/PWAT/LCL/SRH/shear/STP/SCP/EHI/SHIP/front/cap/storm mode.
- Add model artifacts under `backend/models/`, but keep trained models and archive datasets out of git by default.
- Backend `/api/forecast` adds optional per-hour:
  - `mlHazards: { tornado: number, hail: number, wind: number }`
  - bundle-level `mlModel: { version, trainedAtISO, featureSchemaHash }`
- Frontend/provider behavior:
  - `pythonBackendProvider` uses `mlHazards` to build `HazardAssessment` for tornado/hail/wind.
  - `buildOutlook` derives category from those ML hazard probabilities.
  - If model files are missing or inference fails, fall back to the current rule engine and expose that in provider notes.
  - Open-Meteo/mock providers remain rule-based.

## Test Plan
- Unit-test report matching: distance threshold, UTC hour window, hazard type mapping.
- Unit-test feature schema: training and live inference produce identical feature order/types.
- Run a tiny archive gather dry run for 1-2 known dates and confirm Parquet rows, labels, and no full-GRIB downloads.
- Train on a tiny fixture and verify model files plus metrics are written.
- Backend tests:
  - Starts with models present.
  - Starts with models absent and falls back cleanly.
  - `/api/forecast` includes `mlHazards` only when inference succeeds.
- Frontend tests/build:
  - `npm run build`
  - Browser check hazards/outlook at several forecast hours; categorical risk should come from ML hazard probabilities when backend model is active.

## Assumptions
- "Replace rules" means tornado/hail/wind severe probabilities replace the rule engine for categorical outlook generation; flood and thunder remain rule-derived.
- First archive run is intentionally a pilot, not a production-grade climatology.
- SPC reports are accepted as the first target dataset even though NCEI Storm Events may be used later for official/cleaned labels.
- Model artifacts are local runtime assets unless a later deployment plan defines artifact storage.
