// hazardProbabilityBands: per-hazard probability contour generator.
// Mirrors the SPC / rawinsonde-style 4-panel outlook visual:
// each hazard gets its own threshold ladder (e.g. 5%/15%/30%/45%/60%
// for hail, 2%/5%/10%/15%/30%/45%/60% for tornado), and probability bands are
// drawn as SPC/rawinsonde-style swaths around the focus region. Secondary
// lobes are generated when the local peak still exceeds a threshold, so the
// outlook can break into disconnected areas instead of one resized ellipse.

import type {
  HazardKey,
  Ingredients,
  Region,
} from '../types/forecast';
import { clipOrganizedSevereCenter, clipOrganizedSeverePoint, isOrganizedSevereThreshold } from './coastalClip';

export type OutlookHazardKey = HazardKey | 'thunder';

export interface ProbBand {
  threshold: number;
  coords: [number, number][];
  hole?: [number, number][];
  color: string;
  label: string;
  significant?: boolean;
}

/**
 * Smooth radial perturbation specifying a hazard's outline "personality".
 * Each entry contributes `amp * sin(k * t + phase)` to the radius multiplier
 * (which starts at 1.0). Keep total |amp| sum below ~0.45 so the polygon
 * doesn't become non-convex / self-intersecting.
 */
export interface ShapeHarmonic { k: number; amp: number; phase: number }

interface HazardLobe {
  centerLat: number;
  centerLon: number;
  along: number;
  cross: number;
  radiusScale: number;
  aspectScale: number;
  probabilityScale: number;
  tiltOffset: number;
  harmonicPhase: number;
  absorbStrength: number;
}

interface ShapeBulge {
  angle: number;
  amp: number;
  width: number;
}

export interface HazardConfig {
  thresholds: number[];   // ascending probabilities
  colors: string[];       // one per threshold
  labels: string[];       // legend labels
  baseLatRadius: number;  // outermost contour size in degrees lat
  aspect: number;
  tilt: number;
  sigThreshold?: number;  // peak prob above which "SIG" hatching kicks in
  // Per-hazard organic shape signature so each hazard's outline reads as
  // a distinct blob rather than a generic ellipse. Concentric bands of
  // the SAME hazard share these harmonics so they nest cleanly.
  harmonics: ShapeHarmonic[];
}

// Color schemes mirror the rawinsonde / SPC family of outlook palettes.
export const HAZARD_CONFIGS: Record<OutlookHazardKey, HazardConfig> = {
  thunder: {
    thresholds: [0.10, 0.40, 0.70],
    colors:     ['#c9a279', '#5cdde6', '#ef6055'],
    labels:     ['10%', '40%', '70%'],
    baseLatRadius: 3.1,
    aspect: 1.7,
    tilt: -8,
    // Broad, rounded blob with a gentle south-east bulge.
    harmonics: [
      { k: 2, amp: 0.16, phase: 0.45 },
      { k: 3, amp: 0.09, phase: 1.20 },
      { k: 5, amp: 0.05, phase: 0.70 },
    ],
  },
  tornado: {
    thresholds: [0.02, 0.05, 0.10, 0.15, 0.30, 0.45, 0.60],
    colors:     ['#3b9b3b', '#a87d4f', '#d4ad7c', '#cf2727', '#c43eb1', '#6e0099', '#4b006b'],
    labels:     ['2%', '5%', '10%', '15%', '30%', '45%', '60%'],
    baseLatRadius: 1.45,
    aspect: 1.7,
    tilt: -10,
    sigThreshold: 0.10,
    // Narrow, lobed shape with a pinched waist - typical of tornado swaths.
    harmonics: [
      { k: 2, amp: 0.20, phase: 1.70 },
      { k: 3, amp: 0.12, phase: 0.30 },
      { k: 7, amp: 0.05, phase: 2.10 },
    ],
  },
  hail: {
    thresholds: [0.05, 0.15, 0.30, 0.45, 0.60],
    colors:     ['#a87d4f', '#f6c842', '#cf2727', '#c43eb1', '#6e0099'],
    labels:     ['5%', '15%', '30%', '45%', '60%'],
    baseLatRadius: 2.15,
    aspect: 1.85,
    tilt: -10,
    sigThreshold: 0.30,
    // Bulbous shape with two flanks - typical of hail swaths along the
    // dry-line and a secondary axis on the cold-pool gust front.
    harmonics: [
      { k: 2, amp: 0.14, phase: 0.90 },
      { k: 4, amp: 0.10, phase: 1.50 },
      { k: 5, amp: 0.06, phase: 0.20 },
    ],
  },
  wind: {
    thresholds: [0.05, 0.15, 0.30, 0.45, 0.60],
    colors:     ['#a87d4f', '#f6c842', '#cf2727', '#c43eb1', '#6e0099'],
    labels:     ['5%', '15%', '30%', '45%', '60%'],
    baseLatRadius: 2.05,
    aspect: 1.95,
    tilt: -8,
    sigThreshold: 0.30,
    // Long, stretched, slightly wavy contour - typical of derecho-style
    // wind swaths that ride a synoptic boundary east-by-northeast.
    harmonics: [
      { k: 2, amp: 0.10, phase: 2.20 },
      { k: 3, amp: 0.13, phase: 0.80 },
      { k: 6, amp: 0.04, phase: 1.10 },
    ],
  },
  flood: {
    thresholds: [0.05, 0.15, 0.30, 0.45, 0.60],
    colors:     ['#5b8540', '#7eb453', '#cf2727', '#c43eb1', '#6e0099'],
    labels:     ['5%', '15%', '30%', '45%', '60%'],
    baseLatRadius: 2.0,
    aspect: 1.65,
    tilt: -6,
    // Smoother, rounder shape - flood-prone areas tend to follow basin
    // boundaries rather than tight convective lines.
    harmonics: [
      { k: 2, amp: 0.10, phase: 1.30 },
      { k: 3, amp: 0.10, phase: 0.50 },
      { k: 4, amp: 0.06, phase: 1.80 },
    ],
  },
};

// Per-hazard SIG offset relative to the primary lobe's tilted axis (in
// degrees lat/lon along/cross). Gives the SIG core its own location so it
// doesn't sit perfectly on top of the ENH+ band centroid:
//   - tornado: shifted upshear/back-right toward the warm-sector triple-point
//     where STP/0-1km SRH peak
//   - hail:    shifted upshear/back-left along the dry-line / mid-level lapse
//     rate axis where 2"+ stones are most likely
//   - wind:    shifted downshear along the QLCS / cold-pool axis where
//     significant gusts cluster ahead of the primary band.
const SIG_LOBE_OFFSETS: Record<OutlookHazardKey, { along: number; cross: number }> = {
  tornado: { along: 0.55, cross: -0.45 },
  hail:    { along: -0.65, cross: 0.55 },
  wind:    { along: 0.95, cross: 0.40 },
  flood:   { along: 0, cross: 0 },
  thunder: { along: 0, cross: 0 },
};

// Chaikin curve subdivision: smooth raw contours into organic SPC-like shapes.
function chaikinSmooth(pts: [number, number][], iterations = 2): [number, number][] {
  let ring = pts;
  for (let iter = 0; iter < iterations; iter++) {
    const next: [number, number][] = [];
    for (let i = 0; i < ring.length; i++) {
      const a = ring[i];
      const b = ring[(i + 1) % ring.length];
      next.push([0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]]);
      next.push([0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]]);
    }
    ring = next;
  }
  return ring;
}

function blobPoints(
  centerLat: number,
  centerLon: number,
  rLat: number,
  rLon: number,
  tiltDeg: number,
  n: number,
  harmonics: ShapeHarmonic[],
  bulges: ShapeBulge[] = [],
): [number, number][] {
  // CW in (lon, lat) so d3-geo treats the interior as the small region.
  // Radial perturbation by a sum of sinusoidal harmonics gives each hazard
  // a distinct organic outline. Concentric bands of the same hazard share
  // these harmonics, so they nest exactly without crossing.
  const tilt = (tiltDeg * Math.PI) / 180;
  const cosT = Math.cos(tilt);
  const sinT = Math.sin(tilt);
  const out: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    const t = -(i / n) * Math.PI * 2;
    let wob = 1;
    for (const h of harmonics) {
      wob += h.amp * Math.sin(h.k * t + h.phase);
    }
    for (const b of bulges) {
      const d = angleDistance(t, b.angle);
      wob += b.amp * Math.exp(-(d * d) / (2 * b.width * b.width));
      wob -= b.amp * 0.28 * Math.exp(-(angleDistance(t, b.angle + Math.PI) ** 2) / (2 * (b.width * 0.85) ** 2));
    }
    wob = clamp(wob, 0.62, 1.42);
    const ex = rLon * wob * Math.cos(t);
    const ey = rLat * wob * Math.sin(t);
    const lon = centerLon + (ex * cosT - ey * sinT);
    const lat = centerLat + (ex * sinT + ey * cosT);
    // CONUS boundary clamp — keep polygon on land
    out.push([
      Math.max(-125, Math.min(-66, lon)),
      Math.max(24, Math.min(50, lat)),
    ]);
  }
  return chaikinSmooth(out, 2);
}

function pointIsOrganizedSafe([lon, lat]: [number, number]): boolean {
  const [safeLon, safeLat] = clipOrganizedSeverePoint(lon, lat);
  return Math.abs(safeLon - lon) <= 0.03 && Math.abs(safeLat - lat) <= 0.03;
}

function coastalSafeBlobPoints(
  centerLat: number,
  centerLon: number,
  rLat: number,
  rLon: number,
  tiltDeg: number,
  n: number,
  harmonics: ShapeHarmonic[],
  bulges: ShapeBulge[],
  initialShrink = 1,
): { coords: [number, number][]; shrink: number } {
  let shrink = initialShrink;
  let coords = blobPoints(centerLat, centerLon, rLat * shrink, rLon * shrink, tiltDeg, n, harmonics, bulges);

  for (let attempt = 0; attempt < 10; attempt++) {
    if (coords.every(pointIsOrganizedSafe)) return { coords, shrink };
    shrink *= 0.84;
    coords = blobPoints(centerLat, centerLon, rLat * shrink, rLon * shrink, tiltDeg, n, harmonics, bulges);
  }

  return { coords, shrink };
}

function pointInPolygon([x, y]: [number, number], poly: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    const crosses = (yi > y) !== (yj > y);
    if (crosses && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function sampleCoords(coords: [number, number][], targetCount: number): [number, number][] {
  const step = Math.max(1, Math.floor(coords.length / targetCount));
  return coords.filter((_, index) => index % step === 0);
}

function minSampleDistance(a: [number, number][], b: [number, number][]): number {
  let minDistance = Number.POSITIVE_INFINITY;
  for (const [lonA, latA] of a) {
    for (const [lonB, latB] of b) {
      const distance = Math.hypot(lonA - lonB, latA - latB);
      if (distance < minDistance) minDistance = distance;
    }
  }
  return minDistance;
}

function shouldDrawSplitLobe(candidate: [number, number][], primary: [number, number][]): boolean {
  const candidateSamples = sampleCoords(candidate, 24);
  const primarySamples = sampleCoords(primary, 24);
  const candidateInsidePrimary = candidateSamples.filter((point) => pointInPolygon(point, primary)).length;
  const primaryInsideCandidate = primarySamples.filter((point) => pointInPolygon(point, candidate)).length;
  const overlapRatio = Math.max(
    candidateInsidePrimary / Math.max(1, candidateSamples.length),
    primaryInsideCandidate / Math.max(1, primarySamples.length),
  );

  if (overlapRatio > 0.08) return false;
  return minSampleDistance(candidateSamples, primarySamples) > 0.42;
}

function angleDistance(a: number, b: number): number {
  let d = a - b;
  while (d > Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;
  return d;
}

function offsetPoint(
  centerLat: number,
  centerLon: number,
  alongDeg: number,
  crossDeg: number,
  tiltDeg: number,
): { lat: number; lon: number } {
  const tilt = (tiltDeg * Math.PI) / 180;
  const cosT = Math.cos(tilt);
  const sinT = Math.sin(tilt);
  return {
    lon: centerLon + alongDeg * cosT - crossDeg * sinT,
    lat: centerLat + alongDeg * sinT + crossDeg * cosT,
  };
}

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

// ── Shared ingredient-driven morphing helpers ──────────────────────────
// These are the same expressions previously inlined in buildHazardBands /
// morphHarmonics. Extracted so the artifact-driven SIG path can apply
// identical environmental sensitivity instead of relying solely on a
// synthetic motion clock.

function ingredientAspect(baseAspect: number, ing: Ingredients | undefined): number {
  if (!ing) return baseAspect;
  return baseAspect * Math.min(
    1.35,
    0.92
    + Math.min(ing.shear06Kt / 120, 0.24)
    + (ing.stormMode === 'linear' ? 0.12 : ing.stormMode === 'discrete' ? -0.08 : 0),
  );
}

function ingredientTilt(
  baseTilt: number,
  ing: Ingredients | undefined,
  forecastHour: number,
  motion: { phase: number; rate: number; wobble: number },
): number {
  if (!ing) return baseTilt;
  return baseTilt
    + (ing.stormMode === 'linear' ? 4 : ing.stormMode === 'discrete' ? -3 : 0)
    + Math.sin(forecastHour * 0.018 * motion.rate + motion.phase) * 0.35;
}

function stableWaveSeed(region: Region, hazard: OutlookHazardKey): number {
  const basis = `${hazard}:${region.label}:${region.centerLat.toFixed(2)}:${region.centerLon.toFixed(2)}`;
  let hash = 2166136261;
  for (let i = 0; i < basis.length; i++) {
    hash ^= basis.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function motionProfile(region: Region, hazard: OutlookHazardKey): { phase: number; rate: number; wobble: number } {
  const seed = stableWaveSeed(region, hazard);
  const hazardOffset =
    hazard === 'thunder' ? 0.0 :
    hazard === 'hail' ? 1.2 :
    hazard === 'wind' ? 2.4 :
    hazard === 'tornado' ? 3.3 : 4.1;
  return {
    phase: seed * Math.PI * 2 + hazardOffset,
    rate: 0.10 + seed * 0.08,
    wobble: 0.75 + seed * 0.55,
  };
}

interface BandBounds {
  minLon: number;
  maxLon: number;
  minLat: number;
  maxLat: number;
}

function bandBounds(coords: [number, number][]): BandBounds {
  return coords.reduce(
    (acc, [lon, lat]) => ({
      minLon: Math.min(acc.minLon, lon),
      maxLon: Math.max(acc.maxLon, lon),
      minLat: Math.min(acc.minLat, lat),
      maxLat: Math.max(acc.maxLat, lat),
    }),
    { minLon: Infinity, maxLon: -Infinity, minLat: Infinity, maxLat: -Infinity },
  );
}

function bandCentroid(coords: [number, number][]): [number, number] {
  const sum = coords.reduce((acc, [lon, lat]) => [acc[0] + lon, acc[1] + lat], [0, 0]);
  return [sum[0] / Math.max(1, coords.length), sum[1] / Math.max(1, coords.length)];
}

function signedRingArea(coords: [number, number][]): number {
  let area = 0;
  for (let i = 0; i < coords.length; i++) {
    const [x0, y0] = coords[i];
    const [x1, y1] = coords[(i + 1) % coords.length];
    area += x0 * y1 - x1 * y0;
  }
  return area / 2;
}

function normalizeExteriorRing(coords: [number, number][]): [number, number][] {
  return signedRingArea(coords) > 0 ? [...coords].reverse() : coords;
}

function boundsGap(a: BandBounds, b: BandBounds): { lon: number; lat: number } {
  return {
    lon: Math.max(0, Math.max(a.minLon - b.maxLon, b.minLon - a.maxLon)),
    lat: Math.max(0, Math.max(a.minLat - b.maxLat, b.minLat - a.maxLat)),
  };
}

function boundsOverlapArea(a: BandBounds, b: BandBounds): number {
  const lon = Math.max(0, Math.min(a.maxLon, b.maxLon) - Math.max(a.minLon, b.minLon));
  const lat = Math.max(0, Math.min(a.maxLat, b.maxLat) - Math.max(a.minLat, b.minLat));
  return lon * lat;
}

function boundsArea(bounds: BandBounds): number {
  return Math.max(0, bounds.maxLon - bounds.minLon) * Math.max(0, bounds.maxLat - bounds.minLat);
}

function outerMergeConfig(hazard: OutlookHazardKey): {
  gapLon: number;
  gapLat: number;
  maxDistance: number;
  maxSpanLon: number;
  maxSpanLat: number;
  pad: number;
} {
  if (hazard === 'thunder') {
    return { gapLon: 1.25, gapLat: 0.75, maxDistance: 6.3, maxSpanLon: 18, maxSpanLat: 9.5, pad: 0.16 };
  }
  if (hazard === 'wind') {
    return { gapLon: 0.9, gapLat: 0.6, maxDistance: 5.6, maxSpanLon: 16, maxSpanLat: 8, pad: 0.12 };
  }
  if (hazard === 'hail') {
    return { gapLon: 0.8, gapLat: 0.55, maxDistance: 4.8, maxSpanLon: 14, maxSpanLat: 7, pad: 0.10 };
  }
  return { gapLon: 0.6, gapLat: 0.42, maxDistance: 3.8, maxSpanLon: 10, maxSpanLat: 5.5, pad: 0.07 };
}

function shouldMergeOuterBands(a: ProbBand, b: ProbBand, hazard: OutlookHazardKey): boolean {
  const cfg = outerMergeConfig(hazard);
  const aBounds = bandBounds(a.coords);
  const bBounds = bandBounds(b.coords);
  const mergedBounds = {
    minLon: Math.min(aBounds.minLon, bBounds.minLon),
    maxLon: Math.max(aBounds.maxLon, bBounds.maxLon),
    minLat: Math.min(aBounds.minLat, bBounds.minLat),
    maxLat: Math.max(aBounds.maxLat, bBounds.maxLat),
  };
  const gap = boundsGap(aBounds, bBounds);
  const overlapArea = boundsOverlapArea(aBounds, bBounds);
  const smallerArea = Math.min(boundsArea(aBounds), boundsArea(bBounds));

  if (overlapArea / Math.max(0.001, smallerArea) >= 0.06) return true;

  const [aLon, aLat] = bandCentroid(a.coords);
  const [bLon, bLat] = bandCentroid(b.coords);
  const meanLat = ((aLat + bLat) / 2) * Math.PI / 180;
  const distance = Math.hypot((aLon - bLon) * Math.cos(meanLat), aLat - bLat);

  if (
    mergedBounds.maxLon - mergedBounds.minLon > cfg.maxSpanLon ||
    mergedBounds.maxLat - mergedBounds.minLat > cfg.maxSpanLat
  ) {
    return false;
  }

  if (gap.lon <= cfg.gapLon && gap.lat <= cfg.gapLat) return true;

  return distance <= cfg.maxDistance && (gap.lon <= cfg.gapLon * 1.4 || gap.lat <= cfg.gapLat * 1.4);
}

function connectedOuterGroups(bands: ProbBand[], hazard: OutlookHazardKey): ProbBand[][] {
  const visited = new Set<number>();
  const groups: ProbBand[][] = [];

  for (let i = 0; i < bands.length; i++) {
    if (visited.has(i)) continue;
    const queue = [i];
    const group: ProbBand[] = [];
    visited.add(i);

    while (queue.length > 0) {
      const idx = queue.shift()!;
      group.push(bands[idx]);
      for (let j = 0; j < bands.length; j++) {
        if (visited.has(j) || !shouldMergeOuterBands(bands[idx], bands[j], hazard)) continue;
        visited.add(j);
        queue.push(j);
      }
    }

    groups.push(group);
  }

  return groups;
}

function outerEnvelope(group: ProbBand[], hazard: OutlookHazardKey): [number, number][] {
  if (group.length === 1) return group[0].coords;

  const cfg = outerMergeConfig(hazard);
  const weighted = group.map((band) => {
    const bounds = bandBounds(band.coords);
    const [lon, lat] = bandCentroid(band.coords);
    const weight = Math.max(1, (bounds.maxLon - bounds.minLon) * (bounds.maxLat - bounds.minLat));
    return { lon, lat, weight };
  });
  const totalWeight = weighted.reduce((sum, item) => sum + item.weight, 0);
  const centerLon = weighted.reduce((sum, item) => sum + item.lon * item.weight, 0) / totalWeight;
  const centerLat = weighted.reduce((sum, item) => sum + item.lat * item.weight, 0) / totalWeight;
  const lonScale = Math.max(0.5, Math.cos(centerLat * Math.PI / 180));
  const samples = 144;
  const radii = Array(samples).fill(0) as number[];

  group.flatMap((band) => band.coords).forEach(([lon, lat]) => {
    const x = (lon - centerLon) * lonScale;
    const y = lat - centerLat;
    const angle = Math.atan2(y, x);
    const idx = Math.floor((((angle + Math.PI) / (Math.PI * 2)) * samples)) % samples;
    radii[idx] = Math.max(radii[idx], Math.hypot(x, y) + cfg.pad);
  });

  for (let i = 0; i < samples; i++) {
    if (radii[i] > 0) continue;
    let left = 1;
    while (left < samples && radii[(i - left + samples) % samples] === 0) left++;
    let right = 1;
    while (right < samples && radii[(i + right) % samples] === 0) right++;
    const leftR = radii[(i - left + samples) % samples] || 0;
    const rightR = radii[(i + right) % samples] || leftR;
    const w = left / Math.max(1, left + right);
    radii[i] = leftR * (1 - w) + rightR * w;
  }

  let smoothRadii = radii;
  for (let pass = 0; pass < 3; pass++) {
    smoothRadii = smoothRadii.map((r, i) => (
      r * 0.50 +
      smoothRadii[(i - 1 + samples) % samples] * 0.25 +
      smoothRadii[(i + 1) % samples] * 0.25
    ));
  }

  const coords = smoothRadii.map((r, i): [number, number] => {
    const angle = (i / samples) * Math.PI * 2 - Math.PI;
    const lon = centerLon + (Math.cos(angle) * r) / lonScale;
    const lat = centerLat + Math.sin(angle) * r;
    return [Math.max(-125, Math.min(-66, lon)), Math.max(24, Math.min(50, lat))];
  });

  return normalizeExteriorRing(chaikinSmooth(coords, 1));
}

export function mergeOuterHazardBands(bands: ProbBand[], hazard: OutlookHazardKey): ProbBand[] {
  const grouped = new Map<string, ProbBand[]>();
  bands.forEach((band) => {
    const key = `${band.threshold}:${band.significant === true ? 'sig' : 'base'}`;
    grouped.set(key, [...(grouped.get(key) ?? []), band]);
  });

  const merged = Array.from(grouped.values()).flatMap((thresholdBands) => (
    connectedOuterGroups(thresholdBands, hazard).map((group) => ({
      ...group[0],
      label: group[0].label.replace(' satellite', ''),
      coords: outerEnvelope(group, hazard),
    }))
  ));

  return merged.sort((a, b) => {
    if (a.threshold !== b.threshold) return a.threshold - b.threshold;
    if (a.significant === b.significant) return 0;
    return a.significant ? 1 : -1;
  });
}

function hazardLobes(
  region: Region,
  hazard: OutlookHazardKey,
  peakProb: number,
  ingredients: Ingredients | undefined,
  forecastHour: number,
  tiltDeg: number,
  motion: { phase: number; rate: number; wobble: number },
): HazardLobe[] {
  const ing = ingredients;
  const shear = clamp((ing?.shear06Kt ?? 35) / 60, 0.25, 1.35);
  const instability = clamp(Math.max(ing?.mucape ?? 1200, ing?.mlcape ?? 1200) / 3000, 0.2, 1.4);
  const front = ing?.frontSignal === 'strong' ? 1 : ing?.frontSignal === 'moderate' ? 0.72 : ing?.frontSignal === 'weak' ? 0.38 : 0.18;
  const modeStretch = ing?.stormMode === 'linear' ? 1.25 : ing?.stormMode === 'discrete' ? 0.78 : 1;
  const phase = forecastHour * 0.018 * motion.rate + motion.phase;
  const pulse = 0.5 + 0.5 * Math.sin(phase + shear * 1.7 + motion.wobble);
  const splitPulse = 0.5 + 0.5 * Math.sin(phase * 1.45 + instability * 2.1 + motion.phase * 0.37);
  const absorbPulse = 0.5 + 0.5 * Math.sin(phase * 0.9 + front * 2.7 + motion.wobble * 0.9);
  const driftFactor =
    hazard === 'wind' ? 1.15 :
    hazard === 'hail' ? 0.92 :
    hazard === 'tornado' ? 0.74 :
    hazard === 'thunder' ? 0.68 : 0.55;
  const driftAlong = Math.min(4.2, forecastHour * (0.028 + shear * 0.018) * driftFactor);
  const driftCross = Math.sin(forecastHour * 0.055 * motion.rate + motion.phase) * (0.14 + front * 0.18) * driftFactor;
  const driftTilt = tiltDeg + Math.sin(motion.phase) * 8;
  const driftedCenter = offsetPoint(region.centerLat, region.centerLon, driftAlong, driftCross, driftTilt);
  const lobes: HazardLobe[] = [
    {
      centerLat: clamp(driftedCenter.lat, 25, 49),
      centerLon: clamp(driftedCenter.lon, -124, -68),
      along: 0,
      cross: 0,
      radiusScale: 1,
      aspectScale: 1,
      probabilityScale: 1,
      tiltOffset: 0,
      harmonicPhase: 0,
      absorbStrength: 0,
    },
  ];

  const add = (
    along: number,
    cross: number,
    radiusScale: number,
    aspectScale: number,
    probabilityScale: number,
    tiltOffset: number,
    harmonicPhase: number,
    absorbStrength: number,
  ) => {
    const liveProbScale = probabilityScale * (0.74 + 0.42 * Math.sin(phase + harmonicPhase + front + motion.phase * 0.21));
    if (peakProb * liveProbScale < HAZARD_CONFIGS[hazard].thresholds[0]) return;
    const liveAlong = along * (0.84 + 0.36 * Math.sin(phase * (0.95 + motion.wobble * 0.18) + harmonicPhase));
    const liveCross = cross + Math.sin(phase * (1.35 + motion.rate * 0.28) + harmonicPhase + motion.phase * 0.31) * (0.55 + 0.45 * absorbStrength);
    const p = offsetPoint(driftedCenter.lat, driftedCenter.lon, liveAlong, liveCross, tiltDeg);
    lobes.push({
      centerLat: clamp(p.lat, 25, 49),
      centerLon: clamp(p.lon, -124, -68),
      along: liveAlong,
      cross: liveCross,
      radiusScale: radiusScale * (0.93 + 0.10 * Math.sin(phase * (1.08 + motion.rate * 0.16) + harmonicPhase + shear)),
      aspectScale: aspectScale * (0.95 + 0.09 * Math.cos(phase * (0.86 + motion.wobble * 0.12) + harmonicPhase + motion.phase * 0.17)),
      probabilityScale: liveProbScale,
      tiltOffset: tiltOffset + Math.sin(phase * (0.74 + motion.rate * 0.18) + harmonicPhase + motion.phase) * 0.9,
      harmonicPhase: harmonicPhase + motion.phase * 0.23,
      absorbStrength: absorbStrength * (0.70 + 0.35 * absorbPulse),
    });
  };

  if (hazard === 'thunder') {
    add(3.0 + shear * 1.0 - splitPulse * 0.7, -0.5 + Math.sin(phase) * 0.25, 0.48 + pulse * 0.10, 0.90, 0.55, 5, 0.7, 0.50);
    if (front >= 0.7 && instability >= 0.55) add(-3.0 - shear * 0.6 + absorbPulse * 0.6, 0.7, 0.35 + splitPulse * 0.08, 0.75, 0.22, -7, 1.4, 0.36);
    if (peakProb >= 0.7 && front >= 0.7) add(4.5 + shear * 0.6 - absorbPulse * 1.0, 0.7, 0.28 + pulse * 0.08, 0.70, 0.18, 8, 2.0, 0.30);
  } else if (hazard === 'hail') {
    add(-1.6 - shear * 0.7 + absorbPulse * 0.5, 0.5, 0.45 + pulse * 0.10, 0.78, 0.60, -8, 0.9, 0.42);
    if ((ing?.stormMode === 'discrete' || ing?.stormMode === 'mixed') && instability >= 0.65) {
      add(2.2 + shear * 0.6 - splitPulse * 0.6, -0.5, 0.30 + splitPulse * 0.08, 0.70, 0.38, 7, 1.8, 0.30);
    }
  } else if (hazard === 'wind') {
    add(2.4 + shear * 1.0 - absorbPulse * 0.7, -0.35, 0.42 + pulse * 0.10, 1.10 * modeStretch, 0.55, 6, 1.1, 0.45);
    if (front >= 0.7 || ing?.stormMode === 'linear') add(4.2 + shear * 0.6 - splitPulse * 1.0, -0.6, 0.26 + splitPulse * 0.08, 1.15, 0.30, 8, 1.9, 0.28);
  } else if (hazard === 'tornado') {
    add(-1.1 - shear * 0.5 + absorbPulse * 0.4, 0.4, 0.32 + pulse * 0.07, 0.70, 0.55, -7, 1.2, 0.36);
    if ((ing?.srh01 ?? 0) >= 140 && (ing?.stormMode === 'discrete' || ing?.stormMode === 'mixed')) {
      add(1.7 + shear * 0.4 - splitPulse * 0.5, -0.3, 0.22 + splitPulse * 0.06, 0.65, 0.38, 6, 2.1, 0.24);
    }
  }

  return lobes;
}

/**
 * Build probability bands for one hazard around a focus region.
 *
 * Each band is the area where the hazard probability >= threshold but <
 * the next threshold up. Bands render as annular rings (outer + reversed
 * inner hole) so each color shows distinctly without over-stacking.
 *
 * Radius model: linear falloff from peak at center to 0 at base radius.
 * radius(t) = base * (1 - t / peak)
 */
export function buildHazardBands(
  region: Region,
  hazard: OutlookHazardKey,
  peakProb: number,
  ingredients?: Ingredients,
  forecastHour = 0,
): ProbBand[] {
  if (peakProb <= 0) return [];
  const cfg = HAZARD_CONFIGS[hazard];
  const activationFloor = 0.82;
  const active = cfg.thresholds
    .map((t, i) => ({ t, c: cfg.colors[i], l: cfg.labels[i] }))
    .filter(({ t }) => peakProb >= t * activationFloor);
  if (active.length === 0) return [];

  const n = 80;
  const sigActive = cfg.sigThreshold !== undefined && peakProb >= cfg.sigThreshold;
  const motion = motionProfile(region, hazard);
  const dynamicHarmonics = ingredients
    ? morphHarmonics(cfg.harmonics, hazard, ingredients, forecastHour, motion)
    : cfg.harmonics;
  const dynamicAspect = ingredientAspect(cfg.aspect, ingredients);
  const dynamicTilt = ingredientTilt(cfg.tilt, ingredients, forecastHour, motion);

  // Build bands as STACKED FILLED DISKS in order from largest radius (lowest
  // threshold) to smallest (highest threshold). When rendered in array order,
  // the smaller, higher-probability disks paint on top of the larger ones,
  // producing distinct concentric color bands without relying on SVG
  // polygon-with-holes (which proved fragile with d3-geo's projected winding).
  const lobes = hazardLobes(region, hazard, peakProb, ingredients, forecastHour, dynamicTilt, motion);
  const lobeEntries = lobes
    .map((lobe, lobeIndex) => ({ lobe, lobeIndex }))
    .sort((a, b) => a.lobeIndex - b.lobeIndex);
  const bands: ProbBand[] = [];
  const organizedShrinkByLobe = new Map<number, number>();
  const continuingSplitLobes = new Set<number>();
  for (const { t: thr, c, l } of active) {
    const candidates: Array<{ lobeIndex: number; band: ProbBand }> = [];

    lobeEntries.forEach(({ lobe, lobeIndex }) => {
      const lobePeak = peakProb * lobe.probabilityScale;
      const activation = clamp((lobePeak - thr * activationFloor) / Math.max(thr * (1 - activationFloor), 0.001), 0, 1);
      if (activation <= 0) return;
      const thresholdRatio = Math.min(0.985, thr / Math.max(lobePeak, thr * 1.01));
      const transitionFloor = 0.018 + activation * 0.025;
      const rLat = cfg.baseLatRadius * lobe.radiusScale * Math.pow(Math.max(1 - thresholdRatio, transitionFloor) * activation, 0.82);
      if (rLat <= 0.08) return;
      const rLon = rLat * dynamicAspect * lobe.aspectScale;
      const bandCenter = { lat: lobe.centerLat, lon: lobe.centerLon };
      const harmonics = dynamicHarmonics.map((h, idx) => ({
        ...h,
        amp: h.amp * (1 + lobe.absorbStrength * (0.08 + idx * 0.03)),
        phase: h.phase + lobe.harmonicPhase + forecastHour * 0.004 * motion.rate * (idx + 1) + motion.phase * 0.19,
      }));
      const bulges = lobeIndex === 0
        ? lobes.slice(1).map((other): ShapeBulge => ({
          angle: Math.atan2(other.cross / Math.max(rLat, 0.1), other.along / Math.max(rLon, 0.1)),
          amp: other.absorbStrength * other.probabilityScale * 0.42,
          width: 0.30 + other.radiusScale * 0.14,
        }))
        : [
          {
            angle: Math.atan2(-lobe.cross / Math.max(rLat, 0.1), -lobe.along / Math.max(rLon, 0.1)),
            amp: lobe.absorbStrength * 0.26,
            width: 0.38,
          },
        ];
      const organized = isOrganizedSevereThreshold(hazard, thr);
      const [centerLon, centerLat] = organized
        ? clipOrganizedSevereCenter(bandCenter.lon, bandCenter.lat)
        : [bandCenter.lon, bandCenter.lat];
      const tilt = dynamicTilt + lobe.tiltOffset;
      const safe = organized
        ? coastalSafeBlobPoints(
          centerLat,
          centerLon,
          rLat,
          rLon,
          tilt,
          n,
          harmonics,
          bulges,
          organizedShrinkByLobe.get(lobeIndex) ?? 1,
        )
        : { coords: blobPoints(centerLat, centerLon, rLat, rLon, tilt, n, harmonics, bulges), shrink: 1 };
      if (organized && !organizedShrinkByLobe.has(lobeIndex)) {
        organizedShrinkByLobe.set(lobeIndex, safe.shrink);
      }
      candidates.push({
        lobeIndex,
        band: {
          threshold: thr,
          coords: safe.coords,
          color: c,
          label: lobeIndex === 0 ? l : `${l} satellite`,
        },
      });
    });

    const primary = candidates.find((candidate) => candidate.lobeIndex === 0);
    if (!primary) continue;
    const splitLobes = candidates.filter((candidate) => (
      candidate.lobeIndex !== 0 &&
      (thr === active[0].t || continuingSplitLobes.has(candidate.lobeIndex)) &&
      shouldDrawSplitLobe(candidate.band.coords, primary.band.coords)
    ));
    splitLobes.forEach((candidate) => continuingSplitLobes.add(candidate.lobeIndex));
    bands.push(...splitLobes.map((candidate) => candidate.band), primary.band);
  }

  // Add a small SIG core when peak exceeds the significance threshold.
  // Rendered last, on top, as a darker fill (no SVG pattern - more reliable).
  // The SIG centroid is OFFSET from the primary lobe via SIG_LOBE_OFFSETS so
  // it has its own location and doesn't sit perfectly on top of the ENH+
  // band centroid - mirroring how SPC outlooks draw the SIG hatch as a
  // distinct shape inside the higher-probability area, not centered on it.
  //
  // Morph is driven by `sigIngredientMorph` — the SAME research-grounded
  // coefficient set used by the artifact-driven SIG path
  // (`buildArtifactSigBlob`), so both code paths reflect the same
  // meteorological response to the underlying ingredients (STP/SCP-driven
  // displacement, mode-flip tilt and aspect, CAPE/cap-driven size,
  // front-driven offset reach).
  if (sigActive && cfg.sigThreshold !== undefined) {
    const primaryLobe = lobes[0];
    const morph = sigIngredientMorph(hazard, ingredients);
    // Pre-clamp SIG radius (peak-prob intensity × lobe scale × ingredient
    // size factor) and offset magnitude. These are the values that would
    // be used WITHOUT the MRGL containment.
    const preClampSigR = cfg.baseLatRadius * primaryLobe.radiusScale *
      Math.pow(1 - cfg.sigThreshold / Math.max(peakProb, cfg.sigThreshold), 0.75) * 0.35 *
      morph.sizeFactor;
    const sigOffset = SIG_LOBE_OFFSETS[hazard];
    const preClampOffsetMag = Math.hypot(sigOffset.along, sigOffset.cross) * morph.offsetReachMul;
    // Containment scale — keeps SIG inside the MRGL boundary. The SIG
    // can extend through any inner bands (MRGL → SLGT → ENH → ...) but
    // never beyond the MRGL outline (SPC convention).
    const containScale = clampSigToMRGL(hazard, peakProb, preClampSigR, preClampOffsetMag, primaryLobe.radiusScale);
    const sigR = preClampSigR * containScale;
    if (sigR > 0.08) {
      // Tilt: bypass `dynamicTilt` (which has the helper's modest mode
      // kick) and use cfg.tilt + lobe.tiltOffset as the SIG baseline,
      // then add the SIG-specific mode-flip kick and shear / composite-
      // index lean. This keeps the SIG tilt in sync with the artifact
      // SIG path and avoids double-counting the helper's small mode
      // contribution.
      const sigTilt = cfg.tilt + primaryLobe.tiltOffset
        + morph.modeTiltKick + morph.shearTilt - morph.hazardIndexTilt;
      // Offset: scaled by the SIG-specific reach multiplier × containment
      // scale so strong front + high STP/SCP environments push the SIG
      // farther from the peak lobe (within MRGL bounds), weak forcing
      // keeps it close.
      const liveAlong = sigOffset.along * morph.offsetReachMul * containScale;
      const liveCross = sigOffset.cross * morph.offsetReachMul * containScale;
      const offsetCenter = offsetPoint(
        primaryLobe.centerLat,
        primaryLobe.centerLon,
        liveAlong,
        liveCross,
        sigTilt,
      );
      const [centerLon, centerLat] = clipOrganizedSevereCenter(offsetCenter.lon, offsetCenter.lat);
      const harmonics = dynamicHarmonics.map((h, idx) => ({
        ...h,
        amp: h.amp * (1 + primaryLobe.absorbStrength * (0.08 + idx * 0.03)),
        phase: h.phase + primaryLobe.harmonicPhase + forecastHour * 0.004 * motion.rate * (idx + 1) + motion.phase * 0.19,
      }));
      // Asymmetric bulge in the SIG offset direction — leans the rule-
      // based SIG toward the meteorologically favored side, mirroring
      // the artifact SIG path. Empty bulge array is the previous
      // behavior (no asymmetry).
      const bulgeAxisLength = Math.hypot(liveAlong, liveCross);
      const bulges: ShapeBulge[] = bulgeAxisLength > 0.05
        ? [{
            angle: Math.atan2(liveCross, liveAlong) + Math.PI,
            amp: morph.bulgeAmp,
            width: morph.bulgeWidth,
          }]
        : [];
      const { coords } = coastalSafeBlobPoints(
        centerLat,
        centerLon,
        sigR,
        sigR * dynamicAspect * primaryLobe.aspectScale * morph.aspectKick,
        sigTilt,
        n,
        harmonics,
        bulges,
        organizedShrinkByLobe.get(0) ?? 1,
      );
      bands.push({
        threshold: cfg.sigThreshold,
        coords,
        color: '#1a1a1a',
        label: 'SIG',
        significant: true,
      });
    }
  }
  return bands;
}

/**
 * Research-grounded ingredient morph coefficients for the SIG (significant
 * severe) polygon. Both the artifact-driven SIG path
 * (`buildArtifactSigBlob`) and the rule-based SIG core inside
 * `buildHazardBands` consume the SAME set of coefficients so the two
 * code paths produce identical meteorological response to the underlying
 * ingredients.
 *
 * Citations (all inline-justified at the assignment site below):
 *   • STP/SCP thresholds: Thompson, Edwards, Mead (2003, 2004)
 *   • Bunkers right-mover deviation: Bunkers et al. (2000)
 *   • QLCS shear orientation: Weisman & Rotunno (1988)
 *   • Cap suppression form: SPC STP (200 + MLCIN)/150 term
 *   • CAPE thresholds: SPC mesoanalysis operational ranges
 */
interface SigMorphCoeffs {
  // Raw normalized ingredients (kept on the coeffs object for any
  // path-specific tweaks that need access to them).
  shear: number;
  instability: number;
  cap: number;
  frontPush: number;
  hazardIndex: number;
  isLinear: boolean;
  isDiscrete: boolean;
  isMixed: boolean;
  // Derived geometric coefficients applied uniformly across SIG paths.
  aspectKick: number;       // multiplier for SIG aspect on top of helper output
  modeTiltKick: number;     // degrees added to cfg.tilt for storm mode
  shearTilt: number;        // degrees added for shear-driven lean
  hazardIndexTilt: number;  // degrees subtracted for composite-index lean
  sizeFactor: number;       // multiplier for SIG radius
  offsetReachMul: number;   // multiplier for SIG offset displacement
  bulgeAmp: number;         // SIG-asymmetry bulge amplitude
  bulgeWidth: number;       // SIG-asymmetry bulge angular width
}

/**
 * Universal 5% (brown band) containment boundary for the SIG polygon.
 * The SIG can extend through any inner band (5% brown → 15% yellow →
 * 30% red → 45% magenta → 60% purple) but never beyond the brown
 * 5% boundary. For tornado, this is intentionally TIGHTER than
 * `cfg.thresholds[0]` (which is the 2% green MRGL band) because SIG
 * tornado hatches in SPC outlooks never extend into the 2% green halo
 * — they live inside the 5% SLGT brown band and higher.
 *
 * Mapping per hazard:
 *   • hail   thresholds [0.05, 0.15, 0.30, 0.45, 0.60] — 5% = thresholds[0] (brown)
 *   • wind   thresholds [0.05, 0.15, 0.30, 0.45, 0.60] — 5% = thresholds[0] (brown)
 *   • tornado thresholds [0.02, 0.05, 0.10, 0.15, ...] — 5% = thresholds[1] (brown)
 *
 * Hardcoding the 0.05 boundary universally ensures the SIG never spills
 * out of the brown band regardless of how many bands precede it.
 */
const SIG_CONTAINMENT_THRESHOLD = 0.05;

/**
 * Clamp the SIG's effective radius and offset so its total geographic
 * extent (offset distance + radius) stays INSIDE the 5% (brown band)
 * boundary across all severe hazards (hail, wind, tornado).
 *
 * SPC convention: the SIG hatched area is always rendered INSIDE the
 * categorical risk area. Per user spec, the universal SIG boundary is
 * the 5% / brown band — the SIG can extend through any inner bands
 * (15%, 30%, etc.) but never beyond the brown outline.
 *
 * Without this clamp, extreme ingredient scenarios (very strong front +
 * high STP/SCP + extreme CAPE + weak cap) could otherwise inflate the
 * SIG beyond the brown boundary because:
 *   • Band radii are peak-probability-driven (smaller bands at lower peak)
 *   • SIG radius/offset are ingredient-driven (uncorrelated with peak)
 *
 * The 0.88 safety margin keeps the SIG comfortably inside the band edge
 * rather than touching it, mirroring how SPC SIG hatches sit visibly
 * inside the brown outline.
 *
 * Returns the scale factor (≤ 1) to multiply BOTH the SIG radius and the
 * offset by. The scale is 1 when no clamp is needed, < 1 when the SIG
 * would otherwise extend beyond the brown boundary.
 */
function clampSigToMRGL(
  hazard: OutlookHazardKey,
  peakProb: number,
  preClampSigR: number,
  preClampOffsetMag: number,
  lobeRadiusScale = 1,
): number {
  const cfg = HAZARD_CONFIGS[hazard];
  if (peakProb <= SIG_CONTAINMENT_THRESHOLD) return 0;
  // R_outer is the geographic radius of the 5% (brown) band given the
  // current peak probability — i.e. how far the brown outline extends
  // from the band anchor. The SIG must fit entirely inside this radius.
  const R_outer = cfg.baseLatRadius * lobeRadiusScale *
    Math.pow(Math.max(1 - SIG_CONTAINMENT_THRESHOLD / peakProb, 0.01), 0.82) * 0.88;
  const totalExtent = preClampSigR + preClampOffsetMag;
  if (totalExtent <= R_outer || totalExtent <= 1e-6) return 1;
  return R_outer / totalExtent;
}

function sigIngredientMorph(
  hazard: OutlookHazardKey,
  ingredients: Ingredients | undefined,
): SigMorphCoeffs {
  // Shear (0–6 km) — 35 kt rough lower bound for organized supercell
  // environments (Thompson et al. 2003); 50 kt is "strong / outbreak-
  // level" deep-layer shear. Saturate at 50 kt, clamp at 75 kt.
  const shear = clamp((ingredients?.shear06Kt ?? 35) / 50, 0.20, 1.50);

  // Instability (MUCAPE / MLCAPE) — 2500 J/kg = SPC "strong severe"
  // threshold; >3000 = "extreme". Beyond 2500 mostly drives intensity
  // (handled separately by intensityScale), not SIG geometry.
  const instability = clamp(
    Math.max(ingredients?.mucape ?? 1200, ingredients?.mlcape ?? 1200) / 2500,
    0.15,
    1.60,
  );

  // Cap suppression — mirrors SPC's STP (200+MLCIN)/150 inhibition term.
  // Strong cap takes 0.45 size hit so the SIG visibly contracts.
  const cap =
    ingredients?.capStrength === 'strong' ? 0.45 :
    ingredients?.capStrength === 'moderate' ? 0.22 :
    ingredients?.capStrength === 'weak' ? 0.08 : 0.02;

  // Frontal forcing — sharp fronts elongate SIG along the boundary and
  // push it ~3x farther from the peak than a weak front.
  const frontPush =
    ingredients?.frontSignal === 'strong' ? 1.20 :
    ingredients?.frontSignal === 'moderate' ? 0.78 :
    ingredients?.frontSignal === 'weak' ? 0.36 : 0.15;

  // STP (Thompson 2003/04): ≥1 is sig-tor threshold, ≥3 outbreak.
  // Saturate at STP=3 → 1.0, allow STP=4.5 → 1.5 for extreme cases.
  const stp = clamp((ingredients?.stp ?? 0) / 3, 0, 1.5);
  // SCP (Thompson 2003): ≥1 favorable supercell env, ≥6 outbreak.
  // Saturate at SCP=6 → 1.0.
  const scp = clamp((ingredients?.scp ?? 0) / 6, 0, 1.5);
  const hazardIndex = hazard === 'tornado' ? stp : scp;

  const isLinear = ingredients?.stormMode === 'linear';
  const isDiscrete = ingredients?.stormMode === 'discrete';
  const isMixed = ingredients?.stormMode === 'mixed';

  // Aspect kick (calibrated to observed SIG hatch aspect ratios):
  //   discrete supercell ~1.5–2:1, QLCS/bow ~3–5:1.
  //   shear^1.4 gives non-linear response near outbreak shear.
  const aspectKick = 1
    + Math.pow(shear, 1.4) * 0.50
    + (isLinear ? 0.40 : isDiscrete ? -0.12 : isMixed ? 0.15 : 0)
    + hazardIndex * 0.20
    + frontPush * 0.10;

  // Mode tilt kick (Bunkers right-mover deviation 15–25° → -12° for
  // discrete; QLCS perpendicular to shear → +8° for linear).
  const modeTiltKick = isLinear ? 8 : isDiscrete ? -12 : isMixed ? 2 : 0;
  const shearTilt = shear * 6;
  const hazardIndexTilt = hazardIndex * 6;

  // Size factor — instability inflates, cap suppresses (cap coefficient
  // dominant per SPC inhibition handling). Strong cap shrinks SIG to
  // ~0.40× of neutral.
  const sizeFactor = clamp(0.55 + instability * 0.45 - cap * 1.20, 0.40, 1.70);

  // Offset reach — front + composite drive displacement from peak.
  // Weak forcing → ~0.43 reach, strong forcing + outbreak composite →
  // ~2.0 reach (≈3x dynamic range, matches SPC SIG hatch placement).
  const offsetReachMul = 0.30 + 0.85 * frontPush + 0.45 * hazardIndex;

  // Asymmetric bulge magnitude — high STP/SCP environments produce
  // more pronounced SIG lean toward the favored side. Linear mode
  // widens the bulge to express the elongated character of QLCS.
  const bulgeAmp = 0.15 + 0.22 * hazardIndex;
  const bulgeWidth = 0.32 + (isLinear ? 0.15 : isDiscrete ? -0.04 : 0);

  return {
    shear, instability, cap, frontPush, hazardIndex,
    isLinear, isDiscrete, isMixed,
    aspectKick, modeTiltKick, shearTilt, hazardIndexTilt,
    sizeFactor, offsetReachMul, bulgeAmp, bulgeWidth,
  };
}

function morphHarmonics(
  base: ShapeHarmonic[],
  hazard: OutlookHazardKey,
  ing: Ingredients,
  forecastHour: number,
  motion: { phase: number; rate: number; wobble: number },
): ShapeHarmonic[] {
  const hour = forecastHour * 0.018 * motion.rate + motion.phase * 0.29;
  const shear = Math.min(ing.shear06Kt / 60, 1.4);
  const instability = Math.min(Math.max(ing.mucape, ing.mlcape) / 3000, 1.3);
  const cap = ing.capStrength === 'strong' ? 0.26 : ing.capStrength === 'moderate' ? 0.16 : ing.capStrength === 'weak' ? 0.08 : 0.02;
  const mode =
    ing.stormMode === 'linear' ? 0.16 :
    ing.stormMode === 'discrete' ? -0.08 :
    ing.stormMode === 'multicell' ? 0.05 : 0.10;
  const hazardBias =
    hazard === 'wind' ? 0.12 :
    hazard === 'tornado' ? 0.18 :
    hazard === 'hail' ? 0.09 :
    hazard === 'thunder' ? 0.04 : 0.06;

  return base.map((h, idx) => {
    const timePulse = Math.sin(forecastHour * 0.025 * motion.rate + idx * 1.3 + shear + motion.phase);
    return {
      ...h,
      amp: Math.max(0.02, h.amp + hazardBias * (idx === 0 ? 0.5 : 0.25) + mode * 0.6 - cap * (idx === 1 ? 0.35 : 0.15) + instability * 0.015 + timePulse * 0.008),
      phase: h.phase + hour + shear * 0.14 * (idx + 1) - cap + Math.sin(forecastHour * 0.012 * motion.wobble + idx + motion.phase * 0.43) * 0.06,
    };
  });
}

/**
 * Build a synthetic SIG blob anchored at the supplied peak-probability
 * location, then offset along the per-hazard SIG_LOBE_OFFSETS so the SIG
 * has its own location instead of overlapping the ENH+ probability cells.
 *
 * The shape MORPHS from the peak-cell environmental ingredients (CAPE,
 * shear, capStrength, stormMode, frontSignal, STP/SCP) using the SAME
 * morphHarmonics + ingredientAspect + ingredientTilt logic that the
 * rule-based band path uses. When ingredients are absent, falls back to
 * a small forecast-hour-only motion clock so the polygon still drifts.
 *
 * Used by the artifact-driven hazard map (which has only a probability grid
 * and no lobe machinery) to render a single, smooth SIG polygon that
 * visually matches the rule-based map's SIG core.
 */
export function buildArtifactSigBlob(
  hazard: OutlookHazardKey,
  peakLat: number,
  peakLon: number,
  forecastHour = 0,
  peakProbability?: number,
  ingredients?: Ingredients,
  region?: Region,
  /**
   * Measured radius (in degrees lat/lon) of the actual artifact 5% band
   * around the peak cell, from `measureArtifactBandRadius`. When provided,
   * overrides the formula-based R_outer in the MRGL containment clamp so
   * the SIG is guaranteed to fit inside the REAL probability region drawn
   * on the map, not just the idealized elliptical shape the formula
   * predicts.
   */
  measuredBandRadius?: number,
): { coords: [number, number][] } | null {
  const cfg = HAZARD_CONFIGS[hazard];
  if (cfg.sigThreshold === undefined) return null;

  // Per-hazard motion seed — different hazards' SIG cores morph out of
  // phase with each other so the four panels read as distinct objects
  // through the loop instead of pulsing in lock-step. When a region is
  // supplied we stack the same stable per-region wave seed used by the
  // rule path so motion phases are consistent across the two paths.
  const hazardSeed =
    hazard === 'tornado' ? 1.70 :
    hazard === 'hail' ? 0.85 :
    hazard === 'wind' ? 2.45 : 0;
  // The motion.phase is a region-stable seed only — no synthetic per-hour
  // clock pulse is applied to size, aspect, reach, or bulge. Every visible
  // morph between forecast hours derives from the hour's actual ingredient
  // values (CAPE, shear, capStrength, stormMode, frontSignal, STP/SCP)
  // changing between hours, so the SIG morph is truly ingredient-driven.
  // Temporal evolution within a fixed environment comes only from
  // morphHarmonics' built-in shear/capStrength/instability phase term
  // (the same temporal model the rule-based bands use).
  const motion: { phase: number; rate: number; wobble: number } = region
    ? motionProfile(region, hazard)
    : { phase: hazardSeed, rate: 0.16, wobble: 1.0 };

  // Intensity scale — when peak ≈ sigThreshold, SIG core is small (~0.78×);
  // when peak is well above (e.g. 60% hail vs 30% threshold), it expands
  // toward ~1.45×. Falls back to neutral 1.0× if probability isn't supplied.
  const sigThreshold = cfg.sigThreshold;
  const peakAboveSig = peakProbability !== undefined
    ? Math.max(0, peakProbability - sigThreshold)
    : 0;
  const intensitySpan = Math.max(0.18, 1 - sigThreshold);
  const intensityScale = peakProbability !== undefined
    ? 0.78 + Math.min(peakAboveSig / intensitySpan, 1) * 0.65
    : 1.0;

  // ── Ingredient-driven morph (shared with rule-based SIG core) ───────
  // All ingredient response is consolidated in `sigIngredientMorph` so
  // both SIG paths (artifact-driven here, rule-based in buildHazardBands)
  // produce identical meteorological response. See the helper definition
  // above for the full citation list (Thompson 2003/04, Bunkers 2000,
  // Weisman & Rotunno 1988, SPC operational thresholds).
  const morph = sigIngredientMorph(hazard, ingredients);

  // Aspect — shared helper output × SIG-specific ingredient kick.
  const baseAspect = ingredientAspect(cfg.aspect, ingredients);
  const aspect = baseAspect * morph.aspectKick;

  // Tilt — cfg.tilt baseline + SIG-specific mode flip + shear/composite
  // lean. Bypasses ingredientTilt so the SIG-specific (bigger) mode kick
  // isn't double-counted with the helper's small kick.
  const tilt = cfg.tilt + morph.modeTiltKick + morph.shearTilt - morph.hazardIndexTilt;

  // Pre-clamp SIG radius and offset magnitude, then derive a containment
  // scale that keeps the SIG inside the MRGL band boundary. The SIG can
  // extend through any inner bands (MRGL → SLGT → ENH → ...) but never
  // beyond the MRGL outline (SPC convention).
  //
  // Two containment paths:
  //  1. If `measuredBandRadius` is provided (caller has actual artifact
  //     grid data), use it directly — this is the largest in-band circle
  //     around the peak cell from the real probability data, with a 0.88
  //     safety margin. Guarantees the SIG fits inside the actual rendered
  //     5% band even when the band is much smaller / asymmetric than
  //     the formula would predict.
  //  2. Otherwise fall back to the formula-based estimate
  //     (`clampSigToMRGL`).
  const sigOffset = SIG_LOBE_OFFSETS[hazard];
  const preClampSigR = cfg.baseLatRadius * 0.35 * intensityScale * morph.sizeFactor;
  const preClampOffsetMag = Math.hypot(sigOffset.along, sigOffset.cross) * morph.offsetReachMul;
  const formulaScale = peakProbability !== undefined
    ? clampSigToMRGL(hazard, peakProbability, preClampSigR, preClampOffsetMag)
    : 1;
  const measuredScale = (measuredBandRadius !== undefined && Number.isFinite(measuredBandRadius))
    ? (() => {
        const R_measured = measuredBandRadius * 0.88;
        const totalExtent = preClampSigR + preClampOffsetMag;
        if (totalExtent <= R_measured || totalExtent <= 1e-6) return 1;
        return R_measured / totalExtent;
      })()
    : 1;
  // Use the TIGHTER of the two scales — if the measured radius is smaller
  // than the formula predicts, we trust the measurement (it's the real
  // band geometry). If the measurement is missing or larger, the formula
  // applies as a defensive bound.
  const containScale = Math.min(formulaScale, measuredScale);

  // Offset reach — front + composite push SIG along its bias axis,
  // scaled down by containScale if needed to stay inside MRGL.
  const liveAlong = sigOffset.along * morph.offsetReachMul * containScale;
  const liveCross = sigOffset.cross * morph.offsetReachMul * containScale;
  const offsetCenter = offsetPoint(peakLat, peakLon, liveAlong, liveCross, tilt);
  const [centerLon, centerLat] = clipOrganizedSevereCenter(offsetCenter.lon, offsetCenter.lat);

  // Size — peak-probability intensity × ingredient size factor × MRGL
  // containment. Same proportional scale applied to offset above.
  const sigR = preClampSigR * containScale;
  if (sigR <= 0.08) return null;

  // Harmonics — pure ingredient morph via morphHarmonics, with a
  // moderate amplitude multiplier so the outline reads as a distinct
  // shape on a single small SIG polygon. No synthetic per-hour sin
  // pulse — temporal evolution comes from morphHarmonics' built-in
  // shear/capStrength/instability response and from ingredient values
  // changing between forecast hours.
  const harmonics = ingredients
    ? morphHarmonics(cfg.harmonics, hazard, ingredients, forecastHour, motion).map((h) => ({
        k: h.k,
        amp: Math.max(0.04, h.amp * 1.50),
        phase: h.phase,
      }))
    : cfg.harmonics.map((h) => ({
        k: h.k,
        amp: h.amp * 1.20,
        phase: h.phase + motion.phase * 0.29,
      }));

  // Asymmetric bulge in the direction of the live SIG offset axis —
  // leans the SIG toward the meteorologically favored side. Amplitude
  // grows with the hazard composite index (morph.bulgeAmp), width
  // widens for linear-mode storms (morph.bulgeWidth). Same coefficients
  // the rule-based SIG core consumes.
  const bulgeAxisLength = Math.hypot(liveAlong, liveCross);
  const bulges: ShapeBulge[] = bulgeAxisLength > 0.05
    ? [{
        angle: Math.atan2(liveCross, liveAlong) + Math.PI,
        amp: morph.bulgeAmp,
        width: morph.bulgeWidth,
      }]
    : [];

  const { coords } = coastalSafeBlobPoints(
    centerLat,
    centerLon,
    sigR,
    sigR * aspect,
    tilt,
    80,
    harmonics,
    bulges,
  );
  return { coords };
}

/**
 * Probability of any thunderstorm (general thunder outlook).
 * Function of CAPE, surface moisture, initiation conf, capping.
 */
export function thunderProbability(ing: Ingredients): number {
  const cape  = Math.sqrt(Math.min(Math.max(ing.mlcape, ing.mucape) / 2000, 1));
  const init  = ing.initiationConf;
  const moist = Math.min(Math.max(0, ing.sfcDewpointF - 55) / 17, 1);
  const capDrag =
    ing.capStrength === 'strong'   ? 0.30 :
    ing.capStrength === 'moderate' ? 0.60 :
    ing.capStrength === 'weak'     ? 0.85 : 1.0;
  const raw = cape * 0.25 + init * 0.55 + moist * 0.20;
  return Math.max(0, Math.min(1, raw)) * capDrag;
}
