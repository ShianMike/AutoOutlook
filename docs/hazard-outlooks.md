# Hazard Probability Outlooks — Rendering System

Reference for how the per-hazard severe-weather outlook maps
(tornado / hail / wind / thunder) are drawn. Two cooperating paths
share a common visual language so the dashboard reads the same whether
the backend is publishing model artifacts or the frontend is falling
back to rule-derived geometry.

---

## 1. Two rendering paths

| Path | Component | When it runs | Source of geometry |
| ---- | --------- | ------------ | ------------------ |
| **Artifact-driven** (dominant) | `src/components/GeneratedHazardProbabilityMap.tsx` | Whenever `OutlookArtifacts.probabilityTiles` is present for the active forecast hour | Per-cell probability grid baked by `backend.ml.outlook_pipeline`, plus optional pre-vectorized polygon shapes |
| **Rule-based fallback** | `src/components/HazardOutlookMap.tsx` | When artifacts are unavailable (mock provider, Open-Meteo path, or `outlook_not_ready`) | `buildHazardBands()` in `src/utils/hazardProbabilityBands.ts`, driven entirely by ingredients + forecast hour |

Both paths render onto the same `react-simple-maps` US states topojson and
share the same legend, 500 mb upper-air overlay, and SIG visual style so
the dashboard does not "switch personalities" between providers.

---

## 2. Probability band ladder

Each hazard has its own threshold ladder, color ramp, and outline
"personality" (radial harmonics) declared once in `HAZARD_CONFIGS` in
`src/utils/hazardProbabilityBands.ts`:

| Hazard | Thresholds | Base radius (°lat) | SIG threshold |
| ------ | ---------- | ------------------ | ------------- |
| thunder | 10 / 40 / 70% | 3.10 | — (no SIG) |
| tornado | 2 / 5 / 10 / 15 / 30 / 45 / 60% | 1.45 | 10% |
| hail | 5 / 15 / 30 / 45 / 60% | 2.15 | 30% |
| wind | 5 / 15 / 30 / 45 / 60% | 2.05 | 30% |
| flood | 5 / 15 / 30 / 45 / 60% | 2.00 | — (no SIG) |

The colors mirror the SPC / rawinsonde outlook palette so the bands are
recognizable to anyone reading official severe-weather outlooks.

---

## 3. SIG (Significant Severe) layer

The dark hatched "SIG" overlay represents the area of significant severe
potential — EF2+ tornadoes, 2"+ hail, 74+ mph wind. SPC products draw it
as a distinct hatched polygon, *not* as a per-cell overlay on every
high-probability grid box. The renderer mirrors that convention.

### 3.1 When SIG kicks in

The SIG **polygon** only renders when:

- The hazard has a `sigThreshold` in `HAZARD_CONFIGS`
  (tornado / hail / wind — not thunder or flood), **and**
- The peak hazard probability for the active forecast hour
  meets or exceeds that threshold

The legend's "SIG" **swatch** is shown whenever the active hazard has a
`sigThreshold` (so the user knows the layer exists for that hazard),
even on forecast hours where the peak hasn't reached it yet — matching
the convention SPC uses on its outlook products.

### 3.2 SIG has its own location (not stuck to ENH+)

The SIG core is rendered as a **single smooth polygon** anchored at — but
**offset from** — the primary high-probability region, so it does not
sit directly on top of the ENH+/MOD band centroid.

Per-hazard offsets are declared once as `SIG_LOBE_OFFSETS` in
`src/utils/hazardProbabilityBands.ts` and applied along the lobe's
tilted axis via `offsetPoint()`:

| Hazard | along (°) | cross (°) | Physical rationale |
| ------ | --------- | --------- | ------------------ |
| tornado | +0.55 | -0.45 | Toward the warm-sector / triple-point where STP and 0–1 km SRH peak |
| hail | -0.65 | +0.55 | Back-left along the dry-line / mid-level lapse-rate axis where 2"+ stones cluster |
| wind | +0.95 | +0.40 | Downshear along the QLCS / cold-pool axis where significant gusts cluster ahead of the line |

Both rendering paths apply the same offset vector so the visual identity
matches across providers.

### 3.3 SIG shape morphs through the forecast cycle

The SIG polygon is **not** a static stamp that only translates with the
peak cell. The shape itself evolves over forecast hours, driven by a
per-hazard motion clock (`buildArtifactSigBlob` in
`src/utils/hazardProbabilityBands.ts`):

- **Per-hazard motion seed** — tornado / hail / wind morph out of phase
  with each other so the four panels read as distinct objects through
  the loop instead of pulsing in lock-step.
- **Tilt drift** (`±~14°` over the forecast window) — the polygon
  visibly rotates through the cycle.
- **Aspect drift** (`±20%`) — stretches and squashes the ellipse.
- **Harmonic amplitude + phase modulation** — the outline bulges
  themselves grow, shrink, and rotate; not just a tiny phase nudge.
- **Offset wobble** — the SIG centroid swings around the peak rather
  than locking to a fixed offset vector.
- **Asymmetric directional bulge** — leans the SIG toward the live
  offset axis (warm-sector for tornado, dry-line for hail, downshear
  for wind), so the polygon is not a perfectly symmetric ellipse.
- **Size pulse** (`±10%`) — a small breathing variation on top of the
  intensity-driven scaling, so the SIG isn't frozen at one radius
  while everything else moves.

Example: tornado SIG at peak = 15%, sampled across the forecast window
(values from `buildArtifactSigBlob`):

```
F+ 0h  tilt= +1.4°  aspect=2.01  along=0.58  cross=-0.19
F+12h  tilt= -1.9°  aspect=1.93  along=0.50  cross=-0.17
F+24h  tilt= -7.3°  aspect=1.80  along=0.40  cross=-0.20
F+36h  tilt=-13.5°  aspect=1.67  along=0.30  cross=-0.26
F+48h  tilt=-19.2°  aspect=1.53  along=0.23  cross=-0.34
```

### 3.4 SIG size scales with peak intensity

When the peak probability is just at the SIG threshold, the SIG core
renders small (`~0.78×` the base radius). As the peak grows well above
threshold (e.g. 60% hail vs. a 30% SIG threshold), it expands toward
`~1.43×`. The mapping is linear in
`min(peakAboveSig / intensitySpan, 1) * 0.65 + 0.78`, where
`intensitySpan = max(0.18, 1 - sigThreshold)`.

For a tornado outlook at fixed F+12h:

```
peak=10%  R=0.396
peak=30%  R=0.469
peak=60%  R=0.579
```

### 3.5 SIG visual style

| Property | Value |
| -------- | ----- |
| Fill | `#1a1a1a` (near-black) |
| Fill opacity | `0.58` |
| Stroke | `#cc1f1f` (signal red) |
| Stroke width | `1.1` |
| Stroke dash | `3 2` (dashed) |

Mirrored exactly between the artifact map and the rule-based fallback
so the SIG hatch reads identically regardless of provider.

---

## 4. Upper-air 500 mb overlay

Both maps overlay the same 500 mb streamline + intensity-streak +
wind-barb decoration (`src/utils/upperAirLines.ts` +
`upperAirLineStyle.ts` + `upperAirWind.ts`). The streamlines do not
participate in SIG calculations; they are purely a synoptic context
overlay.

---

## 5. Where to change things

| To change… | Edit |
| ---------- | ---- |
| Threshold ladder, colors, or base radius for a hazard | `HAZARD_CONFIGS` in `src/utils/hazardProbabilityBands.ts` |
| SIG offset direction / magnitude | `SIG_LOBE_OFFSETS` in `src/utils/hazardProbabilityBands.ts` |
| How the SIG polygon morphs through forecast hours | `buildArtifactSigBlob()` (artifact path) and the SIG block inside `buildHazardBands()` (rule-based path), both in `src/utils/hazardProbabilityBands.ts` |
| SIG fill / stroke style on the artifact map | `sigStyle` in `src/components/GeneratedHazardProbabilityMap.tsx` |
| SIG fill / stroke style on the rule-based map | The SIG band rendering in `src/components/HazardOutlookMap.tsx` |
| Legend entry for SIG | `src/components/GeneratedHazardProbabilityMap.tsx` (artifact) and `src/components/HazardOutlookMap.tsx` (rule-based) |

Keep the two paths visually aligned — when something changes on one
side, mirror it on the other so the dashboard does not flicker between
"styles" when artifacts arrive or fall back.
