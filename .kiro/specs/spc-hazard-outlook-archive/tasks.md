
# Implementation Plan: SPC Hazard Outlook + Archive

## Overview

This plan implements the SPC hazard outlook display and archiving, plus the four defect fixes (cache corruption on refresh, Risk Archive ordering, SPC backing comparison, and Risk Timeline sync). Work proceeds from the backend data layer (normalization, live and archive endpoints) through the frontend pure-logic modules (cache identity, archive ordering, backing resolution, timeline derivation) and finally into presentation wiring. Each correctness property from the design is turned into its own property-based test placed next to the module it validates. Backend property tests use `hypothesis`; frontend property tests use `vitest` + `fast-check`.

## Tasks

- [x] 1. Backend: SPC hazard normalization
  - [x] 1.1 Implement `normalize_spc_hazard_outlook`
    - Add the pure normalizer in `backend/ml/merged_outlook.py` (or a small helper module) that converts raw SPC hazard GeoJSON into the served `OutlookProbabilityShapeFeatureCollection` contract
    - Guarantee all four hazard keys in `availableHazards` with `hazardsPresent` flags; a hazard with no shapes appears as an empty subset, never omitted
    - Ensure each feature has exactly one `hazard`, a `probabilityPercent` clamped/validated to `[0, 100]`, and a boolean `significantSevere`; drop unknown hazard keys defensively
    - _Requirements: 2.2, 2.3_

  - [x] 1.2 Write property test for hazard-type completeness
    - **Property 1: SPC hazard response includes every hazard type**
    - **Validates: Requirements 2.3**
    - Use `hypothesis`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 1`

  - [x] 1.3 Write property test for well-formed shape fields
    - **Property 2: SPC hazard shape fields are well-formed**
    - **Validates: Requirements 2.2**
    - Use `hypothesis`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 2`

- [x] 2. Backend: Live SPC hazard endpoint
  - [x] 2.1 Add `/api/outlook/spc-hazard-shapes` endpoint
    - Implement the GET endpoint in `backend/server.py` reading the cached `spc_day1_hazards.geojson` (no blocking network fetch), returning the normalized collection
    - Return distinct statuses: 200 with all four hazard keys; 404 `{"code": "spc_hazard_unavailable"}` with no `features`; 500 `{"code": "spc_hazard_failed"}` with no partial shapes (normalize fully in memory before responding)
    - _Requirements: 2.1, 2.3, 2.4, 2.5_

  - [x] 2.2 Write endpoint status integration tests
    - Flask test-client tests: 200 returns all four hazard keys; 404 (missing) is distinct from 500 (parse failure); neither error returns partial shapes
    - _Requirements: 2.1, 2.4, 2.5_

- [x] 3. Backend: Archive the SPC hazard outlook
  - [x] 3.1 Copy the SPC hazard artifact per archived event
    - Add `("spc_day1_hazards.geojson", "spc-hazard-shapes.geojson")` to `_ARCHIVE_FILES` in `backend/ml/enh_plus_archive.py`
    - Pass the parsed GeoJSON through `normalize_spc_hazard_outlook` before writing so the archived artifact matches the live contract
    - _Requirements: 3.5, 3.8_

  - [x] 3.2 Write property test for archive/live contract equivalence
    - **Property 3: Archived SPC hazard outlook matches the live contract**
    - **Validates: Requirements 3.8**
    - Use `hypothesis`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 3`

  - [x] 3.3 Add the archive SPC hazard endpoint
    - Add `"spc-hazard-shapes": "spc-hazard-shapes.geojson"` to `_ENH_PLUS_ARCHIVE_FILES` and add `GET /api/outlook/enh-plus-archive-spc-hazard-shapes` in `backend/server.py` delegating to `_enh_plus_archive_file_response("spc-hazard-shapes")`
    - Rely on existing `_json_path` 404 behavior for a distinct not-found response
    - _Requirements: 3.6_

  - [x] 3.4 Write archive copy and endpoint integration tests
    - Assert `update_archive_for_date` writes `spc-hazard-shapes.geojson` for an ENH+ day, and the endpoint returns 404 when the artifact is absent
    - _Requirements: 3.5, 3.6_

- [x] 4. Checkpoint - backend complete
  - Ensure all backend tests pass, ask the user if questions arise.

- [x] 5. Frontend: Test tooling setup
  - [x] 5.1 Configure the frontend test runner
    - Add `vitest` + `fast-check` dev dependencies and a test config/scripts in `package.json`
    - Add a minimal smoke test to confirm the runner executes with `--run` (single execution)
    - _Requirements: 4.1, 5.1, 6.2, 7.2_

- [x] 6. Frontend: Cycle-identity cache (Requirement 4)
  - [x] 6.1 Implement cycle-identity and replacement-guard helpers
    - Create a pure module exposing `cycleIdentityKey`, `sameCycleIdentity`, and `guardReplacement(previous, next)` derived only from durable cycle fields (issuing day, cycle timestamp, outlook type)
    - `guardReplacement` rejects an empty replacement or one missing fields present in the retained cache
    - _Requirements: 4.1, 4.2, 4.4, 4.8_

  - [x] 6.2 Write property test for cache reuse on matching cycle
    - **Property 4: Cache reuse preserves matching-cycle outlooks**
    - **Validates: Requirements 4.1, 4.2**
    - Use `fast-check`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 4`

  - [x] 6.3 Write property test for cache replace/store behavior
    - **Property 5: Cache replaces on differing cycle identity, stores when absent**
    - **Validates: Requirements 4.4, 4.8**
    - Use `fast-check`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 5`

  - [x] 6.4 Integrate cycle-identity cache and reload resilience into `useOutlookArtifacts`
    - Replace `incrementalCacheKey` with cycle identity; reconcile reuse/replace/store on each load using `guardReplacement`
    - Keep displaying the last valid merged outlook during reload; treat a reload exceeding 30s as failed; on failure retain the last valid outlook and surface a per-day status message; ensure refreshed geometries/categories equal the retained cache
    - _Requirements: 4.3, 4.5, 4.6, 4.7_

- [x] 7. Frontend: Live SPC hazard hook and rendering (Requirement 1)
  - [x] 7.1 Implement `useSpcHazardShapes`
    - Add the hook to `src/hooks/useOutlookArtifacts.ts` returning `status: 'loading' | 'ready' | 'missing' | 'error'`, mapping 404 to `missing` and other failures to `error`
    - _Requirements: 1.6_

  - [x] 7.2 Wire the SPC hazard overlay, legend, and hatching into the map
    - Supply SPC hazard shapes from `useSpcHazardShapes` into `OutlookMapPanel` / `GeneratedHazardProbabilityMap` via `spcHazardProbabilityShapes` and `comparisonMode`
    - Render per-hazard filtering, significant-severe hatching distinct from probability fills, a threshold legend with a distinct SIG entry, and a toggleable overlay concurrent with the generated hazard outlook using the shared projection/base layers
    - On `missing`/`error` show an "SPC hazard outlook unavailable" message, keep the generated hazard outlook, and preserve the selected hazard type
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x] 7.3 Write render tests for hazard presentation
    - Assert same projection/base layers as categorical map, hatched significant-severe distinct from fills, legend threshold + SIG entries, per-hazard filtering, and overlay toggle behavior
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.7_

- [x] 8. Frontend: Archive data service (Requirement 3)
  - [x] 8.1 Implement `fetchWithTimeoutRetries`
    - Add a shared helper wrapping `fetchJson` with a 10s timeout and up to 2 retries
    - _Requirements: 3.4_

  - [x] 8.2 Populate archived SPC hazard shapes in `useEnhPlusArchiveEvents`
    - Fetch `/api/outlook/enh-plus-archive-spc-hazard-shapes?date=...` per event `Issued_Date` via `fetchWithTimeoutRetries`, replacing the hardcoded empty collection with the normalized response
    - Provide a default hazard type when none selected; drive per-hazard partial availability from `hazardsPresent`; on unavailable keep the categorical outlook and preserve the selected hazard type
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 3.7, 3.8_

- [x] 9. Frontend: Risk Archive ordering (Requirement 5)
  - [x] 9.1 Implement `orderArchiveEvents`
    - Create `src/utils/archiveEventOrdering.ts` that combines live + catalog, drops missing/null/unparseable `Issued_Date`, de-duplicates by parsed `Issued_Date` with live precedence, and sorts descending
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 9.2 Write property test for descending order
    - **Property 6: Archive events are ordered strictly descending by issued date**
    - **Validates: Requirements 5.1, 5.2**
    - Use `fast-check`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 6`

  - [x] 9.3 Write property test for de-duplication with live precedence
    - **Property 7: Archive events are de-duplicated by issued date with live precedence**
    - **Validates: Requirements 5.3, 5.4**
    - Use `fast-check`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 7`

  - [x] 9.4 Write property test for invalid-date exclusion
    - **Property 8: Invalid issued dates are excluded without dropping valid events**
    - **Validates: Requirements 5.5**
    - Use `fast-check`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 8`

  - [x] 9.5 Wire `orderArchiveEvents` into `HistoricalEnhPlusVerification`
    - Replace the live-only sort-then-merge with merge-then-order across the combined set
    - _Requirements: 5.1, 5.2_

- [x] 10. Frontend: SPC backing comparison (Requirement 6)
  - [x] 10.1 Implement the backing override resolver
    - Create a pure module resolving `effectiveMerged = spcBacked ? override.blend : override.pure`, defaulting first-view to "Our Model", and retaining the previously displayed outlook when the selected mode is unavailable
    - _Requirements: 6.2, 6.3, 6.5, 6.6, 6.7_

  - [x] 10.2 Write property test for backing selection resolution
    - **Property 9: Backing selection resolves to the matching outlook**
    - **Validates: Requirements 6.2, 6.3, 6.6**
    - Use `fast-check`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 9`

  - [x] 10.3 Write property test for unavailable-backing retention
    - **Property 10: Unavailable backing retains the prior outlook**
    - **Validates: Requirements 6.5, 6.7**
    - Use `fast-check`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 10`

  - [x] 10.4 Wire the resolver into `OutlookMapPanel` and build pure/blend overrides
    - Change `mergedArtifactsOverride` to `{ pure, blend }`, select by the `spcBacked` toggle without navigating away, and build both artifact states for the selected event in `HistoricalEnhPlusVerification`; show an "unavailable comparison" message when a mode is unavailable
    - _Requirements: 6.1, 6.4, 6.5_

- [x] 11. Frontend: Risk Timeline synchronization (Requirement 7)
  - [x] 11.1 Implement timeline derivation and selection helpers
    - Create a pure module with `highlightedPeriod(segments, selectedForecastHour)` (single period id), `selectPeriod(state, period)` (deterministic next selection with no transient category), and derivation of `Timeline_Period` categories from the displayed merged artifact hours
    - Represent an unresolvable period with a distinct "no data" state while retaining other periods' categories
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 11.2 Write property test for single-period highlight
    - **Property 11: Timeline highlights exactly one period for the selected hour**
    - **Validates: Requirements 7.2, 7.6**
    - Use `fast-check`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 11`

  - [x] 11.3 Write property test for clean period selection
    - **Property 12: Period selection is single-valued and transition-clean**
    - **Validates: Requirements 7.4, 7.5**
    - Use `fast-check`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 12`

  - [x] 11.4 Write property test for isolated no-data periods
    - **Property 13: Unresolvable timeline periods isolate the "no data" state**
    - **Validates: Requirements 7.3**
    - Use `fast-check`; minimum 100 iterations; tag comment `Feature: spc-hazard-outlook-archive, Property 13`

  - [x] 11.5 Wire timeline helpers into `RiskTimeline` / `buildRiskTimeline`
    - In the merged view derive periods from the currently displayed merged artifact and its backing bundle, highlight the single selected period, set `Selected_Forecast_Hour` once per click, and render unresolved periods with the "no data" state
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

- [x] 12. Final checkpoint - Ensure all tests pass
  - Ensure all backend and frontend tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP; they are the unit, integration, and property-based tests.
- Each task references specific requirement sub-clauses for traceability.
- Each correctness property (Properties 1–13) is implemented by exactly one property-based test placed next to the module it validates; backend properties use `hypothesis`, frontend properties use `fast-check`.
- Every property test runs a minimum of 100 iterations and carries a `Feature: spc-hazard-outlook-archive, Property {number}` tag comment.
- Checkpoints ensure incremental validation between the backend and frontend phases.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "5.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.1", "3.1", "6.1", "8.1", "9.1", "10.1", "11.1"] },
    { "id": 2, "tasks": ["2.2", "3.2", "3.3", "6.2", "6.3", "9.2", "9.3", "9.4", "10.2", "10.3", "11.2", "11.3", "11.4"] },
    { "id": 3, "tasks": ["3.4", "6.4", "7.1", "8.2", "9.5", "10.4", "11.5"] },
    { "id": 4, "tasks": ["7.2"] },
    { "id": 5, "tasks": ["7.3"] }
  ]
}
```
