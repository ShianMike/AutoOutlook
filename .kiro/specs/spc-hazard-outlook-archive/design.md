# Design Document

## Overview

This feature extends AutoOutlook to display the official SPC hazard outlook (tornado, hail, wind, thunder probability shapes, including significant-severe hatching) in the same rendering style already used for the SPC categorical outlook, and to make those SPC hazard shapes available for archived ENH+ events. It also corrects four defects: a cache-corruption defect on page refresh (Requirement 4), incorrect Risk Archive ordering by issued date (Requirement 5), a broken "Our Model" vs "SPC Blend" comparison on the Risk Archive (Requirement 6), and a Risk Timeline that desyncs and flashes the wrong category in the merged view (Requirement 7).

The work spans the existing React/TypeScript frontend and the Flask/Python backend. It is deliberately additive and grounded in the current implementation:

- The backend already produces an SPC hazard probability GeoJSON (`spc_day1_hazards.geojson`) inside each merged Day 1 directory and blends it into the SPC-backed outlook (`backend/ml/merged_outlook.py`). It is *not* yet served as a standalone SPC hazard artifact for the live view, nor archived per event.
- The frontend already renders generated hazard probability shapes and can overlay SPC hazard shapes through `GeneratedHazardProbabilityMap` (via the `spcHazardProbabilityShapes` prop and `comparisonMode`), but the live view never supplies those SPC shapes, and the archive hook (`useEnhPlusArchiveEvents`) hardcodes `spcHazardProbabilityShapes` to an empty collection.
- The outlook cache (`useOutlookArtifacts`) keys its in-memory cache on an `incrementalCacheKey` and clears it whenever that key changes, which is the source of the refresh corruption.
- The Risk Archive assembly in `HistoricalEnhPlusVerification` merges live and catalog events and de-duplicates by `eventDate`, but sorts only the live set before merging, producing the ordering defect.
- The SPC backing comparison uses `useMergedD1Artifacts({ backing })`, which is driven by the `spcBacked` toggle in `OutlookMapPanel`; on the archive view the merged artifacts are supplied via `mergedArtifactsOverride`, which ignores the backing toggle — the source of the broken comparison.
- The Risk Timeline (`RiskTimeline` + `buildRiskTimeline`) derives periods from a `ForecastBundle` and an optional artifact timeline map; in the merged view the bundle and the displayed merged artifact can diverge, producing the desync and the transient wrong-category flash.

### Research Notes

- **SPC hazard data source**: `backend/ml/merged_outlook.py::fetch_archived_spc_category` returns a dict containing both `categoryGeojson` and `hazardGeojson`. The hazard GeoJSON carries a `properties.availableHazards` list and per-feature `properties.hazard`, `properties.probability`, and significant-severe markers. `_spc_hazard_probability_available` already reads `availableHazards` to decide per-hazard availability. This is the authoritative shape for the SPC hazard outlook and drives the response contract in Requirement 2.
- **Existing served shape type**: The frontend already types SPC/generated hazard shapes as `OutlookProbabilityShapeFeatureCollection` (`src/types/outlookArtifacts.ts`), with per-feature `hazard`, `probability`, `threshold`/`thresholdPercent`, `label`, and `color`. Reusing this type keeps the SPC hazard outlook consistent with the generated hazard outlook and satisfies Requirement 3.8's "same color mapping, legend, and overlay layout".
- **Archive artifact pipeline**: `backend/ml/enh_plus_archive.py::update_archive_for_date` copies a fixed `_ARCHIVE_FILES` set from the merged directory into the per-date archive folder. `spc_day1_hazards.geojson` is written into the merged directory but is not in `_ARCHIVE_FILES`, so it is never archived — this is exactly the gap Requirement 3.5 closes.
- **No frontend test runner is configured** (no vitest/jest in `package.json`). The backend uses `unittest`-style tests under `backend/tests/`. The Testing Strategy introduces `vitest` + `fast-check` for the frontend pure-logic properties and `hypothesis` for the backend property tests, since these pure modules (sorting/dedup, cache identity, shape normalization, timeline derivation) are ideal PBT targets.

## Architecture

```mermaid
flowchart TB
    subgraph SPC[SPC NOAA source]
        SPCHAZ[Day 1 hazard probability GeoJSON]
    end

    subgraph Backend[Flask backend]
        MERGE[merged_outlook.fetch_archived_spc_category\nproduces spc_day1_hazards.geojson]
        NORM[SPC hazard normalizer\nnormalize_spc_hazard_outlook]
        LIVEEP["/api/outlook/spc-hazard-shapes\n(new live endpoint)"]
        ARCHUP[enh_plus_archive.update_archive_for_date\n+ spc-hazard-shapes.geojson]
        ARCHEP["/api/outlook/enh-plus-archive-spc-hazard-shapes\n(new archive endpoint)"]
    end

    subgraph Frontend[React frontend]
        ARTHOOK[useOutlookArtifacts\ncycle-identity cache]
        SPCHOOK[useSpcHazardShapes\n(new live hook)]
        ARCHOOK[useEnhPlusArchiveEvents\n+ spcHazardProbabilityShapes + timeout/retry]
        PANEL[OutlookMapPanel\nbacking toggle wired to override]
        HAZMAP[GeneratedHazardProbabilityMap\nSPC overlay + legend]
        TIMELINE[RiskTimeline\nderives from displayed merged data]
        ARCHIVE[HistoricalEnhPlusVerification\nmerge-then-sort by Issued_Date]
    end

    SPCHAZ --> MERGE
    MERGE --> NORM
    NORM --> LIVEEP
    MERGE --> ARCHUP --> ARCHEP
    LIVEEP --> SPCHOOK --> PANEL
    ARCHEP --> ARCHOOK --> ARCHIVE
    ARTHOOK --> PANEL
    ARCHOOK --> PANEL
    PANEL --> HAZMAP
    PANEL --> TIMELINE
    ARCHIVE --> PANEL
```

The design keeps a clean separation:

1. **Backend data layer** normalizes SPC hazard GeoJSON into the shared `OutlookProbabilityShapeFeatureCollection` contract and serves it through a live endpoint and an archive endpoint, with distinct success / not-found / server-error statuses.
2. **Frontend data layer** (hooks) fetches and caches outlooks by cycle identity, and resolves the SPC hazard outlook for both live and archived views with timeout/retry.
3. **Frontend presentation layer** (map panel, hazard map, timeline, archive) renders SPC hazard shapes with the existing projection/legend conventions and keeps the timeline synchronized with the displayed outlook.

## Components and Interfaces

### Backend

#### 1. SPC hazard normalizer (`backend/ml/merged_outlook.py` or a small new helper module)

A pure function that converts the raw SPC hazard GeoJSON produced during merged-D1 generation into the served contract.

```python
def normalize_spc_hazard_outlook(
    hazard_geojson: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a hazard-shape FeatureCollection with one entry per supported
    Hazard_Type, guaranteeing every hazard key is present (possibly empty).

    Output shape:
    {
      "type": "FeatureCollection",
      "properties": {
        "availableHazards": ["tornado", "hail", "wind", "thunder"],
        "hazardsPresent": {"tornado": true, "hail": false, ...}
      },
      "features": [ { "type": "Feature", "geometry": {...},
        "properties": {
          "hazard": "tornado",              # exactly one of the four
          "probabilityPercent": 5.0,        # 0..100 inclusive
          "significantSevere": false,       # boolean
          "label": "5%", "color": "#..."
        } }, ... ]
    }
    """
```

Key rules (Requirement 2.2, 2.3):
- Every feature has exactly one `hazard` from `{tornado, hail, wind, thunder}`.
- `probabilityPercent` is clamped/validated to `[0, 100]`.
- `significantSevere` is always a boolean.
- `availableHazards` always lists all four hazard types; a hazard with no shapes is represented by an empty feature subset, never omitted.

#### 2. Live SPC hazard endpoint (`backend/server.py`)

```python
@app.get("/api/outlook/spc-hazard-shapes")
def spc_hazard_shapes():
    # 200 with normalized FeatureCollection when the SPC hazard outlook exists
    # 404 (distinct) when no SPC hazard outlook exists for the current outlook
    # 500 (distinct) on data-source/processing failure, with NO partial shapes
```

Status contract (Requirement 2.1, 2.4, 2.5):
- **200**: normalized collection (all four hazard keys present).
- **404**: JSON body with `{"error": ..., "code": "spc_hazard_unavailable"}` and no `features`.
- **500**: JSON body with `{"error": ..., "code": "spc_hazard_failed"}` and no partial `features`.

The endpoint reads the already-cached `spc_day1_hazards.geojson` (no blocking network fetch), mirroring the existing serve-time pattern in `_merged_windows`/archive endpoints.

#### 3. Archive SPC hazard artifact (`backend/ml/enh_plus_archive.py` + `backend/server.py`)

- Add `("spc_day1_hazards.geojson", "spc-hazard-shapes.geojson")` to `_ARCHIVE_FILES` so `update_archive_for_date` copies the SPC hazard outlook per archived event (Requirement 3.5). Because the source may be normalized, the copy step will pass the parsed GeoJSON through `normalize_spc_hazard_outlook` before writing so the archived artifact matches the live contract (Requirement 3.8).
- Add `"spc-hazard-shapes": "spc-hazard-shapes.geojson"` to `_ENH_PLUS_ARCHIVE_FILES` and a new endpoint:

```python
@app.get("/api/outlook/enh-plus-archive-spc-hazard-shapes")
def enh_plus_archive_spc_hazard_shapes():
    return _enh_plus_archive_file_response("spc-hazard-shapes")
```

`_enh_plus_archive_file_response` already returns 404 via `_json_path` when the file is missing, giving the archive the same distinct not-found behavior (Requirement 3.6).

### Frontend

#### 4. `useSpcHazardShapes` hook (`src/hooks/useOutlookArtifacts.ts`)

New hook for the live SPC hazard outlook.

```ts
interface SpcHazardShapesState {
  status: 'loading' | 'ready' | 'missing' | 'error';
  shapes: OutlookProbabilityShapeFeatureCollection | null;
  message: string | null;
}

export function useSpcHazardShapes(
  activeRegion?: ActiveRegion,
  enabled?: boolean,
): SpcHazardShapesState;
```

- `missing` maps to HTTP 404 (`artifact_missing`), `error` to any other failure (Requirement 1.6).
- On `missing`/`error` the caller keeps showing the generated hazard outlook.

#### 5. Cycle-identity cache in `useOutlookArtifacts` (Requirement 4)

Replace the current `incrementalCacheKey` (which mixes `generatedAtISO` and `featureSchemaHash`, causing eviction on every refresh) with a **cycle identity** derived only from the durable cycle fields:

```ts
interface CycleIdentity {
  issuingDay: string;      // e.g. "2026-06-10"
  cycleTimeISO: string;    // cycle timestamp
  outlookType: 'day1' | 'day2' | 'incremental';
}

function cycleIdentityKey(incremental: OutlookIncrementalIndex): string;
function sameCycleIdentity(a: CycleIdentity, b: CycleIdentity): boolean;
```

Cache reconciliation on each load (Requirement 4.1, 4.2, 4.4, 4.8):
- If the newly loaded outlook's cycle identity equals the cached identity → **reuse** the cached artifacts; reject a replacement that is empty or missing fields present in the retained cache.
- If the cycle identity differs → **replace** the cache with the new outlook.
- If no cache exists → **store** the new outlook.

Reload resilience (Requirement 4.5, 4.6, 4.7): during a post-refresh reload, keep displaying the last valid merged outlook; treat a reload that does not complete within 30s as failed; on failure retain the last valid displayed outlook and surface a per-day status message. A `guardReplacement(previous, next)` pure helper decides whether `next` may overwrite `previous`.

#### 6. Archive data service (`useEnhPlusArchiveEvents`) (Requirement 3)

- Fetch the archived SPC hazard outlook from `/api/outlook/enh-plus-archive-spc-hazard-shapes?date=...` for each event's `Issued_Date`, applying a **10s timeout and up to 2 retries** before treating it as unavailable (Requirement 3.4). A shared `fetchWithTimeoutRetries(url, { timeoutMs: 10000, retries: 2 })` helper wraps `fetchJson`.
- Populate `event.spcHazardProbabilityShapes` from the normalized response instead of the current hardcoded empty collection.
- Per-hazard partial availability (Requirement 3.7): the normalized collection's `availableHazards`/`hazardsPresent` lets the view show available hazards and mark only the missing one.

#### 7. Risk Archive assembly (`HistoricalEnhPlusVerification`) (Requirement 5)

Extract a pure module `src/utils/archiveEventOrdering.ts`:

```ts
export function orderArchiveEvents(
  liveEvents: HistoricalEnhPlusEvent[],
  catalogEvents: HistoricalEnhPlusEvent[],
): HistoricalEnhPlusEvent[];
```

Algorithm:
1. Combine live + catalog into one set (Requirement 5.2).
2. Drop events whose `Issued_Date` is missing/null/unparseable (Requirement 5.5).
3. De-duplicate by parsed `Issued_Date`, keeping the live event when a live and catalog event collide, otherwise the first live-priority occurrence (Requirement 5.3, 5.4).
4. Sort the combined, de-duplicated set by `Issued_Date` descending (Requirement 5.1).

#### 8. SPC backing comparison on the archive (Requirement 6)

`OutlookMapPanel` currently ignores the `spcBacked` toggle when `mergedArtifactsOverride` is supplied (the archive path). The fix introduces an **override resolver** so the archive supplies both variants and the panel selects by the toggle:

```ts
interface MergedArtifactsOverride {
  pure: OutlookArtifactState;   // "Our Model"
  blend: OutlookArtifactState;  // "SPC Blend"
}
```

- The panel resolves `effectiveMerged = spcBacked ? override.blend : override.pure` (Requirement 6.2, 6.3, 6.4).
- Default selection is "Our Model" (`spcBacked = false`) on first view of an event (Requirement 6.6).
- If the selected mode's data is unavailable, show an "unavailable" message and retain the previously displayed outlook (Requirement 6.5, 6.7).
- Changing the mode replaces only the displayed outlook without navigating away (Requirement 6.4).

`HistoricalEnhPlusVerification` builds both `pure` and `blend` artifact states for the selected event (the archive already stores both the SPC-blended risk polygons and can derive the pure model from the same probability tile / a pure archive artifact).

#### 9. Risk Timeline synchronization (Requirement 7)

The desync arises because `RiskTimeline` derives from `bundle.hours` while the merged view shows a merged artifact that is not reflected in that bundle. The fix:

- In the merged view, derive each `Timeline_Period` from the **currently displayed merged artifact** rather than a stale bundle. `buildRiskTimeline` already accepts `artifactHours` (an `OutlookTimelineHourSummary[]`); the merged view will pass the timeline hours belonging to the displayed merged outlook, and the bundle used for the timeline will be the one backing the displayed merged data (Requirement 7.1).
- Highlight exactly one period — the one containing `Selected_Forecast_Hour` (Requirement 7.2, 7.6). A pure `highlightedPeriod(segments, selectedForecastHour)` returns a single period id.
- Unresolvable periods render a distinct "no data" state while retaining other periods' categories (Requirement 7.3).
- Clicking a period sets `Selected_Forecast_Hour` to that period's `representativeHour` exactly once per click (Requirement 7.4) and transitions the displayed category directly from previous to resulting with no intermediate third category (Requirement 7.5). A pure state reducer `selectPeriod(state, period)` produces the next selection deterministically, so no transient category is emitted.

## Data Models

### Served SPC hazard shape (backend → frontend), reusing `OutlookProbabilityShapeFeatureCollection`

```ts
interface SpcHazardFeatureProperties {
  hazard: 'tornado' | 'hail' | 'wind' | 'thunder';
  probabilityPercent: number;   // 0..100 inclusive
  significantSevere: boolean;
  label: string;                // e.g. "5%", "SIG"
  color: string;                // shared threshold color mapping
  threshold?: number;           // fractional 0..1 (legacy compatibility)
}

interface SpcHazardOutlookCollection {
  type: 'FeatureCollection';
  properties: {
    availableHazards: Array<'tornado' | 'hail' | 'wind' | 'thunder'>;
    hazardsPresent: Record<'tornado' | 'hail' | 'wind' | 'thunder', boolean>;
  };
  features: Array<{
    type: 'Feature';
    geometry: { type: 'Polygon' | 'MultiPolygon'; coordinates: number[][][] | number[][][][] };
    properties: SpcHazardFeatureProperties;
  }>;
}
```

### Cycle identity (frontend cache key)

```ts
interface CycleIdentity {
  issuingDay: string;                       // durable, from cycleTimeISO date
  cycleTimeISO: string;                     // durable cycle timestamp
  outlookType: 'day1' | 'day2' | 'incremental';
}
```

### Archive event (extended)

`HistoricalEnhPlusEvent.spcHazardProbabilityShapes` changes from a hardcoded empty collection to the normalized `SpcHazardOutlookCollection` returned from the archive endpoint. `Issued_Date` corresponds to the existing `eventDate` (`YYYY-MM-DD`).

### Backing override

```ts
interface MergedArtifactsOverride {
  pure: OutlookArtifactState;
  blend: OutlookArtifactState;
}
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: SPC hazard response includes every hazard type

*For any* SPC hazard GeoJSON input (including empty or partial ones), the normalized SPC hazard outlook SHALL contain all four hazard types in `availableHazards`, and a hazard with no shapes SHALL be present as an empty subset rather than omitted.

**Validates: Requirements 2.3**

### Property 2: SPC hazard shape fields are well-formed

*For any* normalized SPC hazard outlook, every feature SHALL carry exactly one hazard from `{tornado, hail, wind, thunder}`, a `probabilityPercent` in the inclusive range `[0, 100]`, and a boolean `significantSevere`.

**Validates: Requirements 2.2**

### Property 3: Archived SPC hazard outlook matches the live contract

*For any* SPC hazard GeoJSON, normalizing it for the live endpoint and normalizing it for the archive artifact SHALL produce the same threshold-to-color mapping, the same per-threshold labels, and the same significant-severe entries.

**Validates: Requirements 3.8**

### Property 4: Cache reuse preserves matching-cycle outlooks

*For any* cached outlook and any newly loaded outlook that shares the same cycle identity (issuing day, cycle timestamp, outlook type), the cache SHALL retain the cached outlook and SHALL reject a replacement that is empty or missing fields present in the retained outlook.

**Validates: Requirements 4.1, 4.2**

### Property 5: Cache replaces on differing cycle identity, stores when absent

*For any* newly loaded outlook whose cycle identity differs from the cached outlook, the cache SHALL replace the cached outlook with the new one; and *for any* newly loaded outlook when no cache exists, the cache SHALL store it as the cached outlook for its cycle identity.

**Validates: Requirements 4.4, 4.8**

### Property 6: Archive events are ordered strictly descending by issued date

*For any* combined set of live and catalog archive events, the displayed result SHALL be sorted so each event's `Issued_Date` is greater than or equal to the next, across the entire combined set.

**Validates: Requirements 5.1, 5.2**

### Property 7: Archive events are de-duplicated by issued date with live precedence

*For any* combined set of live and catalog archive events, the displayed result SHALL contain at most one event per `Issued_Date`, and when a live and a catalog event share an `Issued_Date`, the retained event SHALL be the live one.

**Validates: Requirements 5.3, 5.4**

### Property 8: Invalid issued dates are excluded without dropping valid events

*For any* combined set of archive events, every event with a missing, null, or unparseable `Issued_Date` SHALL be excluded from the result, and every event with a valid `Issued_Date` SHALL be retained (subject to de-duplication) in descending order.

**Validates: Requirements 5.5**

### Property 9: Backing selection resolves to the matching outlook

*For any* archive event with both pure and blend override artifacts available, selecting "Our Model" SHALL resolve to the pure outlook and selecting "SPC Blend" SHALL resolve to the blend outlook, and the first-view default SHALL be "Our Model".

**Validates: Requirements 6.2, 6.3, 6.6**

### Property 10: Unavailable backing retains the prior outlook

*For any* archive event and any backing selection whose data is unavailable, the resolver SHALL retain the outlook displayed before the unavailable mode was selected.

**Validates: Requirements 6.5, 6.7**

### Property 11: Timeline highlights exactly one period for the selected hour

*For any* set of timeline segments and any selected forecast hour that falls within the timeline range, exactly one period SHALL be highlighted, and it SHALL be the period whose hour window contains the selected forecast hour.

**Validates: Requirements 7.2, 7.6**

### Property 12: Period selection is single-valued and transition-clean

*For any* timeline period click, the resulting selection SHALL set `Selected_Forecast_Hour` to that period's representative hour exactly once, and the displayed category SHALL move directly from the previous category to the resulting category with no intermediate category different from both.

**Validates: Requirements 7.4, 7.5**

### Property 13: Unresolvable timeline periods isolate the "no data" state

*For any* merged outlook data where one period cannot be resolved to a risk category, that period SHALL render a "no data" state distinct from all valid risk categories while every other period retains its resolved category.

**Validates: Requirements 7.3**

## Error Handling

### Backend

- **SPC hazard unavailable (404)**: When no `spc_day1_hazards.geojson` exists for the current/archived outlook, return HTTP 404 with `{"code": "spc_hazard_unavailable"}` and no `features`. This is distinct from 200 and 500 (Requirement 2.4, 3.6).
- **Processing failure (500)**: When reading/parsing/normalizing fails, return HTTP 500 with `{"code": "spc_hazard_failed"}` and no partial shapes. Normalization is performed fully in memory before any response is emitted, so a partial collection is never returned (Requirement 2.5).
- **Malformed source GeoJSON**: The normalizer treats missing/invalid features defensively — invalid probability values are clamped to `[0, 100]`, unknown hazard keys are dropped, and a hazard with zero valid features still appears as an empty subset.

### Frontend

- **Live SPC hazard missing/error (Requirement 1.6)**: `useSpcHazardShapes` returns `missing`/`error`; the hazard view shows a visible "SPC hazard outlook unavailable" message, keeps rendering the generated hazard outlook, and preserves the selected hazard type.
- **Archive SPC hazard timeout/retry (Requirement 3.4, 3.6)**: `fetchWithTimeoutRetries` enforces a 10s timeout and up to 2 retries; exhausting retries or receiving 404 marks the SPC hazard outlook unavailable for that event, while the archived categorical outlook continues to display and the selected hazard type is preserved.
- **Per-hazard partial availability (Requirement 3.7)**: available hazards render normally; only the unavailable hazard shows an unavailability indication, driven by `hazardsPresent`.
- **Cache reload failure (Requirement 4.5, 4.6, 4.7)**: reloads that exceed 30s are treated as failed; the last valid outlook is retained and a per-day status message identifies the affected day and the failed reload.
- **Backing unavailable (Requirement 6.5, 6.7)**: an unavailable mode shows an "unavailable comparison" message and the previously displayed outlook is retained.
- **Timeline unresolved periods (Requirement 7.3)**: rendered with a distinct "no data" indicator without disturbing other periods.

## Testing Strategy

Property-based testing applies to this feature because the core corrections are pure functions over large input spaces: SPC hazard normalization, cache-identity reconciliation, archive event ordering/dedup, backing resolution, and timeline derivation/selection. UI wiring and endpoint status plumbing are covered by example-based and integration tests.

### Tooling

- **Backend**: `hypothesis` for property tests over `normalize_spc_hazard_outlook` and archive normalization, alongside the existing `unittest`-style tests under `backend/tests/`. Endpoint status behavior (200/404/500) is covered by Flask test-client example/integration tests.
- **Frontend**: introduce `vitest` + `fast-check` (no runner is configured today). Pure modules under test: `cycleIdentity`/`guardReplacement` (cache), `archiveEventOrdering`, the backing resolver, and the timeline `highlightedPeriod`/`selectPeriod`/derivation helpers. Component rendering (legend entries, hatched significant-severe region, projection/base-layer reuse) uses example-based render tests.

### Property test configuration

- Each property test runs a minimum of **100 iterations**.
- Each property test references its design property with a tag comment in the format: **Feature: spc-hazard-outlook-archive, Property {number}: {property_text}**.
- Each correctness property (Properties 1–13) is implemented by a single property-based test.

### Property-to-test mapping

| Property | Module under test | Library |
| --- | --- | --- |
| 1, 2, 3 | `normalize_spc_hazard_outlook` / archive normalization | hypothesis |
| 4, 5 | cache cycle identity + `guardReplacement` | fast-check |
| 6, 7, 8 | `orderArchiveEvents` | fast-check |
| 9, 10 | backing resolver | fast-check |
| 11, 12, 13 | timeline `highlightedPeriod` / `selectPeriod` / derivation | fast-check |

### Example-based and integration tests

- **Backend endpoints**: 200 returns all four hazard keys; 404 for a missing outlook is distinct from 500 for a parse failure and neither returns partial shapes (Requirement 2.1, 2.4, 2.5); archive SPC hazard endpoint returns 404 when the artifact is absent (Requirement 3.6).
- **Archive artifact copy**: `update_archive_for_date` writes `spc-hazard-shapes.geojson` for an ENH+ day (Requirement 3.5).
- **Hazard rendering**: SPC hazard shapes render with the same projection and base layers as the categorical map (Requirement 1.3), significant-severe renders as a hatched region distinct from probability fills (Requirement 1.4), the legend lists each present threshold plus a SIG entry when shown (Requirement 1.7), and the SPC layer toggles as an overlay concurrent with the generated hazard outlook (Requirement 1.5).
- **Hazard selection**: selecting a hazard type shows only that hazard's shapes (Requirement 1.2); a default hazard is shown when none is selected for an archive event (Requirement 3.3).
- **Timing-oriented criteria** (Requirements 1.1, 1.2, 2.1, 3.1, 3.2, 7.1, 7.5, 7.6) are validated by example/integration tests asserting the behavior occurs; the strict latency bounds are treated as performance budgets rather than unit assertions.
