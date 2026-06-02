# Research Formulations

AutoOutlook computes severe-weather ingredients from selected HRRR fields before
the ML hazard models run. This page documents which formulas are fixed
research-style composites and which outputs remain calibrated probabilities.

## Exact fixed-layer composites

### Fixed-layer STP

`stp` uses the SPC fixed-layer Significant Tornado Parameter structure:

```text
(sbCAPE / 1500) * LCL term * (0-1 km SRH / 150) * (0-6 km BWD / 20) * CIN term
```

The LCL, shear, and CIN terms are clipped to the same operational ranges used
by the SPC-style fixed-layer formula. Inputs are surface-based CAPE, LCL height,
0-1 km storm-relative helicity, 0-6 km bulk wind difference, and surface-based
CIN.

### Fixed-layer SCP substitute

`scp` follows the SPC/Thompson Supercell Composite Parameter structure using
fields that exist in the selected HRRR payload:

```text
(MUCAPE / 1000) * (0-3 km SRH / 50) * shear term * MU-CIN term
```

Because the selected HRRR fields do not include a full effective inflow layer,
AutoOutlook uses 0-3 km SRH and 0-6 km bulk shear as fixed-layer substitutes.
The effective-bulk-wind gate is preserved: weak deep-layer shear produces zero
SCP.

### EHI

`ehi` is computed from mixed-layer CAPE and 0-1 km SRH:

```text
(MLCAPE * 0-1 km SRH) / 160000
```

## SHIP availability

`ship` follows the SPC Significant Hail Parameter only when all required
hail-growth-zone inputs are available:

- MUCAPE
- 0-6 km bulk shear
- MU-parcel mixing ratio derived from 2 m dewpoint and surface pressure
- 700-500 mb lapse rate from 700/500 mb temperature and height
- 500 mb temperature
- freezing level from surface, 850, 700, and 500 mb temperature/height samples

When any mandatory pressure-level hail field is missing, AutoOutlook sets:

```text
ship = 0
shipAvailable = false
```

This is intentional. The pipeline should show that SHIP is unavailable instead
of silently replacing it with a simplified CAPE-and-shear proxy.

The optional selected HRRR fields used by SHIP are:

- `surface_pressure`
- `t850`, `t700`, `t500`
- `hgt850`, `hgt700`, `hgt500`

## Mandatory CAPE and CIN parcels

Research composites use different lifted parcels. AutoOutlook now fetches those
HRRR fields directly instead of treating one CAPE/CIN value as interchangeable:

- Surface parcel: `:CAPE:surface:` and `:CIN:surface:` for fixed-layer STP.
- Mixed-layer parcel: `:CAPE:90-0 mb above ground:` and
  `:CIN:90-0 mb above ground:` as the closest HRRR selected-field proxy for
  SPC-style 100 mb MLCAPE/MLCIN.
- Most-unstable parcel: `:CAPE:255-0 mb above ground:` and
  `:CIN:255-0 mb above ground:` for SCP and SHIP.
- Low-level instability: `:CAPE:0-3000 m above ground:` for low-level
  convective-mode and boundary diagnostics.

The older `180-0 mb above ground` CAPE/CIN pair is still decoded as
`cape180`/`cin180`, but it is no longer the canonical mixed-layer parcel.

## ML probabilities

The tornado, hail, and damaging-wind probability grids are not direct SPC
categorical products. They remain statistically calibrated AutoOutlook model
outputs trained or bootstrapped from the feature schema. The research composites
feed the feature matrix so future XGBoost artifacts train on stronger physical
predictors.

Changing the feature schema version and hash invalidates old model artifacts.
That prevents stale models from running against rows with a different feature
order or meaning.
