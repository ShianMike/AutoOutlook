# Expand 2026 Risk Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the hardcoded in-app risk verification archive to the supplied March-May 2026 event dates, using event-day HRRR 00Z f12-f36 for the full 12Z-to-12Z Day 1 window and locally cached SPC data.

**Architecture:** Keep the existing local-only acquisition and static export path. Add the event catalog to the historical verification helper, fetch missing HRRR/SPC inputs into `backend/artifacts/historical_enh_plus`, then regenerate the TypeScript archive consumed by the existing dashboard map component.

**Tech Stack:** Python, NumPy/SciPy/Shapely, HRRR S3 byte-range fetching, React/TypeScript, Vite.

---

### Task 1: Define the 2026 Event Catalog

**Files:**
- Modify: `backend/ml/historical_event_verification.py`
- Modify: `backend/tests/test_historical_event_verification.py`

- [x] Add all supplied March-May dates in chronological order.
- [x] Add source-backed Moderate labels from official SPC geometry.
- [x] Test default date resolution and event classification.

### Task 2: Fetch Missing Local Artifacts

**Files:**
- Reuse: `scripts/generate-custom-hrrr-artifacts.py`
- Create locally: `backend/artifacts/historical_enh_plus/<event>-hrrr00z-f12-f36_complete/`

- [x] Run event-day 00Z HRRR f12-f36 with grid stride 2 and tile stride 1.
- [x] Fetch archived SPC Day 1 category data after prediction generation.
- [x] Fetch and cache SPC tornado, hail, and wind reports.
- [x] Verify all 12 forecast hours are complete for every event.

### Task 3: Regenerate the Static Archive

**Files:**
- Modify generated file: `src/data/historicalEnhPlusVerification.ts`

- [x] Run `npm run verification:enh-plus-data`.
- [x] Verify every event uses f12-f36, grid stride 2, tile stride 1, and the active trained model metadata.
- [x] Verify hail/wind 5% contours may occupy TSTM but stay inside TSTM.
- [x] Verify 15% and higher contours remain inside MRGL-or-higher.

### Task 4: Validate the App

**Files:**
- Test: `backend/tests/test_historical_event_verification.py`
- Test: `backend/tests/test_merged_outlook.py`
- Test: `backend/tests/test_deployable_outlook_pipeline.py`

- [x] Run the historical, merged-outlook, and deployable pipeline tests.
- [x] Run `npm run build`.
- [x] Open `#docs-enh-verification`, select events across all three months, and verify risk/hazard modes render without console errors.
