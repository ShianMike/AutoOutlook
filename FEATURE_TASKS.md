# AutoOutlook Feature Task List

This file converts the project analysis into separated implementation tasks. The recommended order is based on impact, trust-building value, and how naturally each feature fits the current AutoOutlook architecture.

## Task 1: SPC Verification Panel

**Priority:** Very High  
**Goal:** Surface objective forecast verification against the official SPC Day 1 outlook.

### Background

The backend already exposes or stores verification-related data through:

- `/api/outlook/verification`
- `metadata.spcVerification`
- `agreementFraction`
- `underforecastCells`
- `overforecastCells`
- SPC issue, valid, and expiration timestamps

### Requirements

- Add a dedicated dashboard section for forecast verification.
- Show agreement percentage against SPC Day 1.
- Show the verification of the hazards (tornado, hail, and wind reports of SPC Day 1).
- Show underforecast and overforecast cell counts.
- Show SPC issue time, valid time, and expiration time.
- Show a short interpretation such as conservative, aggressive, or aligned.
- Add the section to the sidebar navigation.

### Suggested Files

- `src/components/VerificationPanel.tsx`
- `src/App.tsx`
- `src/components/DashboardSidebar.tsx`
- `src/hooks/useOutlookArtifacts.ts`
- `src/types/outlookArtifacts.ts`

### Acceptance Criteria

- Verification data appears when artifact metadata contains SPC verification.
- The panel handles missing verification gracefully.
- Sidebar navigation includes a Verification entry.
- The app still builds successfully with `npm run build`.

---

## Task 2: Forecast Trend Since Previous Cycle

**Priority:** Very High  
**Goal:** Show whether the forecast is increasing, decreasing, shifting, or stabilizing from the previous HRRR/artifact cycle.

### Requirements

- Store or retrieve the previous artifact cycle.
- Compare the current cycle against the previous cycle.
- Display risk category changes, such as `SLGT → ENH`.
- Display hazard probability deltas for tornado, hail, and wind.
- Detect approximate spatial shift of the main risk area.
- Display confidence trend if available.

### Backend Work

- Preserve previous cycle metadata and probability summaries.
- Add an endpoint such as `/api/outlook/trends` or `/api/outlook/previous`.
- Return current-vs-previous deltas in a frontend-friendly shape.

### Frontend Work

- Add a `ForecastTrendPanel` component.
- Add trend badges to existing outlook or hazard panels where useful.
- Show fallback messaging when no previous cycle exists.

### Acceptance Criteria

- Users can tell whether the risk increased, decreased, or shifted.
- Missing previous-cycle data does not break the dashboard.
- Trend values match the artifact metadata or backend trend endpoint.

---

## Task 3: Model Transparency / Model Audit Panel

**Priority:** High  
**Goal:** Make the ML model state, dataset quality, and deployment readiness understandable to users and developers.

### Requirements

- Display whether the ML model is active or inactive.
- Show model version and artifact type.
- Show feature schema hash/version.
- Show training row count.
- Show dataset quality status.
- Show positive sample counts for tornado, hail, and wind if available.
- Show the reason the model is inactive if it fails guardrails.

### Future Enhancements

- Add validation metrics from `backend/ml/validate_models.py`.
- Add Brier score, ROC AUC, precision, recall, and calibration bins.
- Add a small reliability chart for each hazard.

### Suggested Files

- `src/components/ModelAuditPanel.tsx`
- `src/App.tsx`
- `src/components/DashboardSidebar.tsx`
- `src/types/forecast.ts`
- `backend/ml/validate_models.py`

### Acceptance Criteria

- Users can clearly see whether AutoOutlook is ML-driven or rule-based.
- Inactive model states explain why the model was not used.
- The panel handles missing metadata safely.


## Task 5: Share / Export Forecast Products

**Priority:** High  
**Goal:** Let users export and share AutoOutlook forecast products.

### Background

The project already includes dependencies/utilities that can support this feature:

- `html-to-image`
- `gif.js`
- `src/utils/gifRecorder.ts`

### Requirements

- Add “Download PNG” for the current outlook map.
- Add “Download PNG” for the hazard probability board.
- Add “Export GIF” for a forecast-hour loop.
- Add “Copy permalink” for the selected forecast hour/view.
- Include cycle and valid-time metadata on exported products.

### Suggested Files

- `src/components/ExportControls.tsx`
- `src/utils/exportImage.ts`
- `src/utils/gifRecorder.ts`
- `src/App.tsx`
- map and hazard board components that need export refs

### Acceptance Criteria

- PNG export works for the current visible forecast product.
- GIF export captures multiple forecast hours in sequence.
- Exports include enough metadata to identify cycle and valid time.
- Export failures show a friendly error message.

---

## Task 6: Full Incremental Forecast-Hour Explorer

**Priority:** Medium-High  
**Goal:** Let users inspect all generated artifact hours while preserving the simple default interaction model.

### Background

The main dashboard currently focuses on seven forecast stops:

- `0, 3, 6, 9, 12, 18, 24`

The backend artifact pipeline can generate many more hours, including `F00-F48`.

### Requirements

- Keep the existing simple forecast-hour slider.
- Add a generated-hour status rail or compact explorer.
- Show ready, pending, failed, and missing generated hours.
- Allow jumping to a generated hour if that hour is ready.
- Avoid adding a generic manual search/dropdown workflow.

### Suggested Files

- `src/components/GeneratedHourExplorer.tsx`
- `src/components/ForecastTimeSlider.tsx`
- `src/hooks/useForecastHour.ts`
- `src/hooks/useOutlookArtifacts.ts`

### Acceptance Criteria

- Users can see which artifact hours are ready or failed.
- Clicking a ready generated hour updates the active displayed hour.
- Pending and failed hours are visually distinct.
- The default 7-stop workflow remains easy to use.

---

## Task 7: SPC Overlay Comparison Mode

**Priority:** Medium-High  
**Goal:** Visually compare AutoOutlook risk polygons against official SPC polygons.

### Requirements

- Add a comparison map mode inside the Verification panel or outlook map.
- Support display modes:
  - AutoOutlook only
  - SPC only
  - Both overlays
  - Difference view
- Highlight underforecast and overforecast areas if available.
- Explain comparison limitations and timing differences.

### Backend Work

- Ensure official SPC Day 1 polygons are available in artifact metadata or verification output.
- Return normalized GeoJSON suitable for frontend overlay rendering.

### Frontend Work

- Render SPC polygons using the same map projection as AutoOutlook.
- Use clear colors/patterns for agreement, overforecast, and underforecast areas.

### Acceptance Criteria

- The comparison mode works when SPC verification data exists.
- Missing SPC data shows a clear unavailable state.
- Users can visually identify where AutoOutlook differs from SPC.

---

## Task 8: Frontend Test Coverage

**Priority:** Medium-High  
**Goal:** Add automated tests for the TypeScript forecast logic and dashboard smoke behavior.

### Background

The backend has tests, but the frontend currently relies mainly on TypeScript build verification.

### Requirements

- Add Vitest for unit tests.
- Add tests for core forecast utilities:
  - `outlookEngine`
  - `hazardEngine`
  - `riskTimeline`
  - `ingredientsDerive`
  - `artifactProbabilities`
- Add Playwright smoke tests for the dashboard.

### Suggested Package Updates

- Add `vitest`.
- Add `@testing-library/react` if component tests are needed.
- Add `playwright` or `@playwright/test` for browser smoke tests.

### Acceptance Criteria

- `npm run test` runs frontend unit tests.
- `npm run build` still passes.
- At least one smoke test confirms the dashboard loads.
- Key forecast threshold logic has regression coverage.

---

## Task 9: Pipeline Operations Dashboard

**Priority:** Medium  
**Goal:** Improve production observability for the artifact generation pipeline.

### Requirements

- Show latest job start and end time.
- Show duration per generated forecast hour.
- Show failed-hour error details.
- Show GCS publish status if running in cloud mode.
- Show active run-lock status.
- Show last successful cycle.
- Show artifact staleness and current cycle policy.
- Show Cloud Run job revision or image tag if available.

### Backend Work

- Add or extend pipeline metadata written by `backend.ml.outlook_pipeline`.
- Add endpoint such as `/api/outlook/pipeline-status`.

### Frontend Work

- Add a detailed operations section or expand `SystemStatusPanel`.
- Keep high-level status visible in the main dashboard.

### Acceptance Criteria

- Operators can diagnose whether the artifact job is healthy.
- Failed forecast hours expose useful non-sensitive errors.
- Public API errors do not leak raw bucket paths or sensitive infrastructure details.

---

## Task 10: Better Mobile Experience

**Priority:** Medium  
**Goal:** Make the dense dashboard easier to use on mobile and small screens.

### Requirements

- Add a compact sticky mobile header.
- Add collapsible dashboard panels.
- Add swipe or large-button forecast-hour navigation.
- Reduce map overlay density by default on small screens.
- Consider a bottom navigation pattern for key sections.
- Ensure exported/share controls remain usable on mobile.

### Suggested Files

- `src/components/CommandHeader.tsx`
- `src/components/DashboardSidebar.tsx`
- `src/components/ForecastTimeSlider.tsx`
- `src/components/OutlookMapPanel.tsx`
- `src/index.css`

### Acceptance Criteria

- The dashboard is usable on common mobile viewport widths.
- Navigation does not consume excessive vertical space.
- Map and hazard panels remain readable.
- No horizontal overflow is introduced.

---

# Recommended Implementation Order

1. SPC Verification Panel
2. Share / Export Forecast Products
3. Model Transparency / Model Audit Panel
4. Forecast Trend Since Previous Cycle
5. Alert / Threshold Trigger System
6. Frontend Test Coverage
7. Full Incremental Forecast-Hour Explorer
8. SPC Overlay Comparison Mode
9. Pipeline Operations Dashboard
10. Better Mobile Experience

# Features To Avoid For Now

These are currently listed or implied as out of scope for AutoOutlook’s automated outlook workflow:

- Manual station selector
- Search bar
- Upload workflow
- Raw data tables
- Skew-T / hodograph tabs
- Editable forecast inputs
- Generic SaaS dashboard redesign

# Notes

The highest-value next work is centered around trust and operational value: verification, trend analysis, model transparency, alerts, and export/share tooling. The app already has a strong forecast engine and visualization foundation, so these tasks should make the product feel more credible and production-ready without changing its core identity.
