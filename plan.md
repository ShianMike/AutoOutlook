# AutoOutlook Historical Training and SPC CIG Category Plan

## Goal

Build a stronger AutoOutlook hazard model from the 2020-2026 historical archive, then adapt the generated categorical outlook to the SPC probability plus Conditional Intensity Group conversion tables.

Primary source for the new categorical rules:

- https://www.spc.noaa.gov/exper/conditional-intensity-information/

The SPC change keeps the public risk categories the same, but category assignment now depends on both hazard probability and conditional intensity. AutoOutlook should move from:

```text
probability -> category
```

to:

```text
probability + CIG -> category
```

## Current Data State

Completed local downloads live under:

```text
backend/ml_data/archive_2020_2024_downloaded/
```

Downloaded completed outputs:

- `hrrr_features_part_2020_2022_preserved_until_20210614_z00_+06h.parquet`
- `hrrr_features_part_2020_2022_preserved_until_20210614_z00_+06h.with_lightning.parquet`
- `hrrr_features_part_2020_2022_remaining_00z_f00_f48.parquet`
- `hrrr_features_part_2023_2024_preserved_until_20230128_z00_+45h.parquet`
- `hrrr_features_part_2025_2026_through_feb_00z_f00_f48.parquet`
- SPC outlook manifests and summaries
- `spc_outlooks_all_days/` with `11,244` Day 1 outlook ZIP files

Still running on the VM through systemd:

- `autooutlook-archive-gather-2023-2024.service`
- `autooutlook-archive-lightning-followup.service`

These need to finish before final training.

## Phase 1: Finish and Download Remaining Archive Outputs

1. Monitor the remaining systemd jobs:

```bash
sudo systemctl status autooutlook-archive-gather-2023-2024.service
sudo systemctl status autooutlook-archive-lightning-followup.service
```

2. Confirm final files exist on the VM:

- `hrrr_features_part_2023_2024_remaining_00z_f00_f48.parquet`
- `hrrr_features_part_2020_2022_remaining_00z_f00_f48.with_lightning.parquet`
- `hrrr_features_part_2023_2024_remaining_00z_f00_f48.with_lightning.parquet`
- `hrrr_features_part_2025_2026_through_feb_00z_f00_f48.with_lightning.parquet`

3. Download only final `.parquet` outputs and logs. Do not train from `.ckpt.parquet` files.

4. Keep raw archive files immutable after download. Write merged and processed files to a new derived directory.

## Phase 2: Merge and Audit the Training Dataset

Create a merge script, likely:

```text
backend/ml/merge_archive_training_data.py
```

Required checks:

- row count per input file
- first and last `runDate`
- unique `(runDate, runCycle, forecastHour, sampleLat, sampleLon)` keys
- duplicate count before and after merge
- label counts for `label_tornado`, `label_hail`, `label_wind`
- lightning coverage by year
- missing or invalid feature count
- split coverage for 2020-2022, 2023-2024, and 2025-2026-Feb

Output:

```text
backend/ml_data/archive_training/autooutlook_hrrr_2020_202602_00z_f00_f48.parquet
backend/ml_data/archive_training/autooutlook_hrrr_2020_202602_00z_f00_f48_summary.json
```

## Phase 3: Add Intensity Labels for CIG Training

The current binary labels answer whether a hazard occurred near the sample point and valid hour. CIG needs a conditional intensity signal.

Add derived labels from SPC storm report values:

Tornado:

- `tornado_ef2_plus`
- `tornado_ef3_plus`
- optional `tornado_ef4_plus`

Hail:

- `hail_2in_plus`
- `hail_3_5in_plus`

Wind:

- `wind_56kt_plus`
- `wind_65kt_plus`
- `wind_74kt_plus`
- `wind_83kt_plus`

Implementation notes:

- Preserve the existing binary event labels.
- Add intensity labels only where the matching report has usable intensity data.
- Keep missing intensity distinct from low intensity.
- Record label provenance in the summary JSON.

## Phase 4: Train Hazard Probability Models

Train or retrain the base hazard probability models:

- tornado probability
- hail probability
- wind probability

Use a time-based split, not random-only:

- train: earlier years
- validation: later held-out years
- final test: most recent complete period, preferably 2025 through February 2026 if labels are clean

Metrics:

- ROC AUC
- average precision
- Brier score
- reliability bins
- category contingency by SPC threshold
- spatial sanity checks against SPC outlook polygons

Calibration:

- fit probability calibration per hazard
- compare isotonic vs sigmoid calibration
- reject models that improve AUC but worsen reliability badly

## Phase 5: Train or Estimate Conditional Intensity Groups

AutoOutlook needs a CIG value for each hazard grid cell before applying the new SPC table.

Recommended model outputs:

```text
tornado_cig: none_or_below_cig1, cig1, cig2, cig3
hail_cig: none_or_below_cig1, cig1, cig2
wind_cig: none_or_below_cig1, cig1, cig2, cig3
```

Candidate training approaches:

1. Direct multiclass model per hazard.
2. Ordered binary models, then map to CIG.
3. Initial heuristic fallback if intensity-label support is too sparse.

Feature signals to evaluate:

- tornado: STP, EHI, SRH01, SRH03, shear, LCL, CAPE, CIN, storm-relative wind
- hail: SHIP, lapse rate, CAPE, freezing level, shear, storm-relative wind
- wind: shear, storm-relative wind, lapse rate, moisture depth, CAPE, downdraft proxies if available
- lightning: HRRR LTNG and LTNGSD fields as storm coverage/intensity support

The first production version may use model probability plus an environment-derived CIG score if direct CIG training is not reliable enough.

## Phase 6: Implement New SPC Probability to Category Tables

Create a shared helper:

```text
backend/ml/spc_categories.py
```

Suggested API:

```python
category_from_probability_and_cig(
    hazard: str,
    probability: float,
    cig: str | int | None,
) -> str
```

Use SPC labels consistently:

```text
NONE, TSTM, MRGL, SLGT, ENH, MDT, HIGH
```

Do not use `MOD` internally for new code. Normalize legacy `MOD` to `MDT`.

### Tornado Table

Columns:

```text
<CIG1, CIG1, CIG2, CIG3
```

Rows:

```text
60% -> ENH, HIGH, HIGH, HIGH
45% -> ENH, MDT, HIGH, HIGH
30% -> ENH, MDT, HIGH, HIGH
15% -> ENH, ENH, MDT, MDT
10% -> SLGT, ENH, ENH, ENH
5%  -> SLGT, SLGT, ENH, not used
2%  -> MRGL, MRGL, SLGT, not used
```

### Wind Table

Columns:

```text
<CIG1, CIG1, CIG2, CIG3
```

Rows:

```text
90% -> ENH, MDT, HIGH, HIGH
75% -> ENH, MDT, HIGH, HIGH
60% -> ENH, MDT, HIGH, HIGH
45% -> ENH, ENH, MDT, HIGH
30% -> SLGT, ENH, ENH, not used
15% -> SLGT, SLGT, ENH, not used
5%  -> MRGL, MRGL, SLGT, not used
```

### Hail Table

Columns:

```text
<CIG1, CIG1, CIG2
```

Rows:

```text
60% -> ENH, MDT, MDT
45% -> ENH, ENH, MDT
30% -> SLGT, ENH, ENH
15% -> SLGT, SLGT, ENH
5%  -> MRGL, MRGL, SLGT
```

Implementation rule for `not used` cells:

- never promote into an SPC table cell marked `not used`
- clamp to the highest valid category for that probability row
- log/report the clamp count during validation

## Phase 7: Wire the New Category Helper Into the Pipeline

Update:

- `backend/ml/gridded_outlook.py`
- `backend/ml/validate_models.py`
- `backend/ml/outlook_pipeline.py`
- tests under `backend/tests/`

Pipeline behavior:

1. Generate hazard probabilities.
2. Generate or estimate hazard CIG grids.
3. Convert each hazard probability plus CIG grid to hazard category ordinals.
4. Combine tornado, hail, and wind category grids into the categorical risk grid.
5. Apply existing spatial post-processing and offshore/land caps.
6. Apply category-consistency probability ceilings using the new table-aware thresholds.

Keep a feature flag during rollout:

```text
AUTOOUTLOOK_SPC_CIG_CATEGORIES=1
```

This allows old and new category output to be compared side by side.

## Phase 8: Draw CIG Overlays Above Risk

CIG should be drawn as a visual intensity overlay above the categorical risk fill, matching SPC's hatch language.

Layer order:

```text
base map
categorical risk fills
CIG hatch overlays
state/county borders
labels, storm reports, markers, upper-air overlays
```

Backend artifact shape:

```text
tile.cigShapes
```

Suggested feature properties:

```json
{
  "hazard": "tornado",
  "cig": 2,
  "label": "TOR CIG2",
  "forecastHour": 24,
  "validTimeISO": "..."
}
```

Hazard support:

- tornado: CIG1, CIG2, CIG3
- wind: CIG1, CIG2, CIG3
- hail: CIG1, CIG2 only

Visual pattern:

- CIG1: dashed diagonal hatching
- CIG2: solid diagonal hatching
- CIG3: cross-hatching

Rendering rules:

- Keep CIG polygon fill transparent except for hatch strokes.
- Use black or near-black hatch strokes, matching SPC's pattern language.
- Use opacity around `0.65` to `0.80` so the risk category color remains readable.
- Draw CIG outlines with a thin black stroke.
- Use screen-space SVG patterns so hatch spacing stays stable while map geometry changes.
- Clip hatches to each CIG polygon.
- Do not render every hazard's CIG overlay at once by default.

Frontend behavior:

- Risk map default: show the max/controlling CIG overlay only.
- Hazard maps: show that hazard's CIG overlay.
- Add a `CIG Overlay` toggle.
- Add a compact legend:

```text
CIG1 dashed hatch
CIG2 solid hatch
CIG3 cross hatch
```

Suggested files:

- `src/types/outlookArtifacts.ts`: add `cigShapes` artifact type.
- `src/components/GeneratedOutlookMap.tsx`: render CIG overlay above risk fills.
- `src/components/GeneratedHazardProbabilityMap.tsx`: render hazard-specific CIG overlay.
- `src/utils/artifactProbabilities.ts`: add helpers to read and normalize CIG feature collections.
- `backend/ml/gridded_outlook.py`: generate CIG feature collections from CIG grids.

## Phase 9: Verification Against SPC Archive

Use the downloaded SPC outlook ZIPs for historical comparison.

Validation outputs:

- old table vs new CIG table category counts
- AutoOutlook vs SPC categorical overlap
- underforecast and overforecast areas
- MRGL/SLGT/ENH/MDT/HIGH hit rates
- probability reliability by hazard
- CIG distribution by hazard and year
- examples of major misses and false alarms

Important checks:

- New table should not erase valid ENH/MDT/HIGH events.
- New table should reduce unsupported MDT/HIGH jumps.
- SLGT and ENH areas should be possible when probability and CIG support it.
- TSTM should remain driven by thunderstorm/lightning support, not severe-only labels.
- CIG hatches should remain readable over all risk category colors.
- CIG hatches should not obscure storm reports, map labels, or risk boundaries.

## Phase 10: Repo and Production Rollout

1. Keep training data out of normal repo commits unless explicitly needed.
2. Commit code, tests, and metadata only.
3. Store large parquet/model artifacts in the existing model/artifact path or external storage.
4. Run unit tests:

```bash
python -m unittest backend.tests.test_ml_pipeline
python -m unittest backend.tests.test_deployable_outlook_pipeline
```

5. Run a historical smoke case with known SPC outlooks.
6. Compare live generated output before and after the CIG conversion.
7. Push only after validation passes.

## Open Decisions

- Whether CIG is directly trained or initially estimated from ingredients.
- Whether to include 2025-February 2026 in final test only or also final training.
- Whether to train one model per hazard plus CIG heads, or a shared model with multiple outputs.
- Whether to parse official 2026+ SPC CIG shapes for verification once they are operational.
- Whether to use HRRR lightning as a training feature for all hazards or only thunder/TSTM support.

## Immediate Next Steps

1. Wait for the two systemd jobs to finish.
2. Download the remaining final parquet outputs.
3. Write the merge/audit script.
4. Add `backend/ml/spc_categories.py` with table tests.
5. Build the first merged training parquet.
6. Train baseline hazard models.
7. Add CIG/intensity labels and compare CIG methods.
8. Implement and verify the CIG hatch overlay rendering.
