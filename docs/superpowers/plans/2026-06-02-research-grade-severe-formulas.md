# Research Grade Severe Formulas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace simplified severe-weather composite and hazard calculations with published SPC/Thompson-style formulations where the required model fields exist, and explicitly mark unavailable formulas instead of silently substituting simplified proxies.

**Architecture:** Add research-grade composite calculations in the backend diagnostics layer and mirror the fixed-layer formulas in the TypeScript fallback. The deployable ML pipeline will carry these composite fields into its feature schema so future XGBoost artifacts are trained on the research predictors, while old model artifacts are invalidated by a schema hash change. HRRR selected decoding will collect pressure-level temperature/height and surface pressure fields needed by SHIP and freezing-level calculations as optional fields.

**Tech Stack:** Python/NumPy backend diagnostics and gridded ML pipeline, TypeScript React fallback provider, unittest/pytest-style backend tests, Vite/TypeScript build.

---

### Task 1: Backend research composite formulas

**Files:**
- Modify: `backend/metpy_diagnostics.py`
- Modify: `backend/tests/test_ml_pipeline.py`

- [ ] Implement fixed-layer STP using SPC formula: `(sbCAPE/1500) * LCL term * (SRH1/150) * (6BWD/20) * CIN term`, with official term clipping.
- [ ] Implement current SCP-style formula using available 0-3 km SRH / 0-6 km BWD terms, with EBWD gate and muCIN term.
- [ ] Implement SHIP only when required fields are present: MUCAPE, MU mixing ratio, 700-500 mb lapse rate, -500 mb temperature, 0-6 km shear, plus low-CAPE/lapse/freezing-level modifiers.
- [ ] Add tests for fixed STP, SCP shear gate, exact SHIP when fields are supplied, and zero SHIP when mandatory hail-growth-zone fields are missing.

### Task 2: Decode and carry missing HRRR research fields

**Files:**
- Modify: `backend/hrrr_selected.py`
- Modify: `backend/hrrr_filter.py`
- Modify: `backend/ml/gather_archive.py`
- Modify: `backend/bundle_builder.py`
- Modify: `backend/ml/outlook_pipeline.py`
- Modify: `backend/tests/test_deployable_outlook_pipeline.py`

- [ ] Add optional selected HRRR terms for surface pressure, 850/700/500 mb temperatures, and 850/700 mb heights.
- [ ] Decode those fields into canonical keys: `surface_pressure`, `t850`, `t700`, `t500`, `hgt850`, `hgt700`, `hgt500`.
- [ ] Pass point-sampled pressure-level fields into `diag.composites()` everywhere ingredients are built.
- [ ] Add tests proving pressure-level temperature fields decode and selected terms include research inputs.

### Task 3: ML feature schema upgrade

**Files:**
- Modify: `backend/ml/features.py`
- Modify: `backend/ml/gridded_outlook.py`
- Modify: `backend/ml/bootstrap_models.py`
- Modify: `backend/tests/test_ml_pipeline.py`
- Modify: `backend/tests/test_deployable_outlook_pipeline.py`

- [ ] Bump `FEATURE_SCHEMA_VERSION` and add `stp`, `scp`, `ehi`, `ship`, `lapseRate700500CPerKm`, `freezingLevelM`, and `surfacePressurePa` to `FEATURE_NAMES`.
- [ ] Compute those fields in `gridded_features_from_fields()` using `backend.metpy_diagnostics.composites()`.
- [ ] Update bootstrap term models so every referenced feature exists in the schema.
- [ ] Update tests for feature count/order and schema hash behavior.

### Task 4: Frontend fallback formulas

**Files:**
- Modify: `src/types/forecast.ts`
- Modify: `src/utils/ingredientsDerive.ts`
- Modify: `src/utils/hazardEngine.ts`
- Modify: `src/utils/providers/pythonBackendProvider.ts`

- [ ] Extend `Ingredients` with optional research fields used by SHIP.
- [ ] Replace simplified TypeScript STP/SCP/SHIP with formulas matching backend behavior.
- [ ] Update hail hazard wording/supporting evidence to distinguish true SHIP from unavailable SHIP.
- [ ] Preserve fallbacks for Open-Meteo/mock by returning `SHIP=0` when pressure-level hail fields are absent.

### Task 5: Documentation and validation

**Files:**
- Create: `docs/research-formulations.md`
- Modify: `README.md`

- [ ] Document which formulas are exact fixed-layer SPC/Thompson formulas and which remain statistically calibrated ML probabilities.
- [ ] Document required optional HRRR fields for SHIP and what happens when they are unavailable.
- [ ] Run targeted backend tests: `python -m unittest backend.tests.test_ml_pipeline backend.tests.test_deployable_outlook_pipeline`.
- [ ] Run frontend build: `npm run build`.
