import { useMemo } from 'react';
import { ComposableMap, Geographies, Geography, Marker } from 'react-simple-maps';
import type { HourSnapshot, Ingredients, RiskCategory, UpperAirVector } from '../types/forecast';
import { clipOrganizedSevereCenter, clipOrganizedSeverePolygon, isOrganizedSevereCategory } from '../utils/coastalClip';
import { map500mbLines } from '../utils/upperAirLines';
import { map500mbWindVectors } from '../utils/upperAirWind';
import { buildUpperAirIntensitySegments, upperAirLineVisualStyle } from '../utils/upperAirLineStyle';
import { displayOutlookAreas } from '../utils/outlookAreaMotion';

const STATES_URL = '/us-states-10m.json';

interface SpcLevelOutlookMapProps {
  snapshot: HourSnapshot | null;
}

interface LevelBand {
  category: RiskCategory;
  coords: [number, number][];
}

interface LevelFeature {
  type: 'Feature';
  properties: { idx: number; featureKey: string; category: RiskCategory; fill: string; stroke: string };
  geometry: { type: 'Polygon'; coordinates: [number, number][][] };
}

interface UpperAirFeature {
  type: 'Feature';
  properties: {
    idx: number;
    value: number;
    stroke: string;
    strokeWidth: number;
    strokeOpacity: number;
    haloWidth: number;
    haloOpacity: number;
  };
  geometry: { type: 'LineString'; coordinates: [number, number][] };
}

interface UpperAirStreakFeature {
  type: 'Feature';
  properties: {
    idx: number;
    stroke: string;
    strokeWidth: number;
    strokeOpacity: number;
  };
  geometry: { type: 'LineString'; coordinates: [number, number][] };
}

const LEVEL_STYLE: Record<RiskCategory, { fill: string; stroke: string; label: string }> = {
  TSTM: { fill: '#c9efc6', stroke: '#5f7f5f', label: 'TSTM' },
  MRGL: { fill: '#6fc36a', stroke: '#2e6f36', label: 'MRGL' },
  SLGT: { fill: '#fff45c', stroke: '#f5a400', label: 'SLGT' },
  ENH:  { fill: '#d9b57b', stroke: '#8a6a35', label: 'ENH'  },
  MOD:  { fill: '#df7777', stroke: '#b52c2c', label: 'MDT'  },
  HIGH: { fill: '#e16ce5', stroke: '#9a249f', label: 'HIGH' },
};

const CATEGORY_RAMP: RiskCategory[] = ['TSTM', 'MRGL', 'SLGT', 'ENH', 'MOD', 'HIGH'];

function interpAnchor(x: number, anchors: [number, number][]): number {
  for (let i = 0; i < anchors.length - 1; i++) {
    const [x0, y0] = anchors[i];
    const [x1, y1] = anchors[i + 1];
    if (x >= x0 && x <= x1) {
      const w = (x - x0) / Math.max(0.0001, x1 - x0);
      return y0 + (y1 - y0) * w;
    }
  }
  return anchors[x < anchors[0][0] ? 0 : anchors.length - 1][1];
}

function southernBorderLat(lon: number): number | null {
  if (lon < -124 || lon > -95) return null;
  return interpAnchor(lon, [
    [-124.0, 32.5],
    [-117.0, 32.5],
    [-114.5, 32.0],
    [-111.0, 31.3],
    [-108.0, 31.3],
    [-106.3, 31.7],
    [-104.5, 30.2],
    [-103.0, 29.4],
    [-101.0, 28.9],
    [-99.5, 27.2],
    [-97.4, 25.9],
    [-95.0, 28.7],
  ]);
}

function clipOrganizedLevelPoint(lon: number, lat: number): [number, number] {
  let [clippedLon, clippedLat] = clipOrganizedSeverePolygon([[lon, lat]])[0];
  const border = southernBorderLat(clippedLon);
  if (border !== null && clippedLat < border + 0.2) clippedLat = border + 0.2;
  return [clippedLon, clippedLat];
}

function clipOrganizedLevelPolygon(points: [number, number][]): [number, number][] {
  return points.map(([lon, lat]) => clipOrganizedLevelPoint(lon, lat));
}

// ── Chaikin curve subdivision: smooth jagged polygons into organic SPC-like contours ──
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

// ── Per-tier center offset: higher categories displace downshear toward ingredient bullseye ──
// Real SPC outlooks are NOT concentric.  The MOD/HIGH core sits at the
// intersection of best kinematics + thermodynamics, which is typically
// downshear and south-of-center (toward moisture return) relative to the
// broader TSTM/MRGL envelope.
interface TierCenter {
  lat: number;
  lon: number;
  rLat: number;
  rLon: number;
  tiltDeg: number;
}

function tierCenter(
  region: { centerLat: number; centerLon: number },
  ing: Ingredients,
  tierOrd: number,      // 0=TSTM … 5=HIGH
  totalTiers: number,
  forecastHour: number,
  baseLat: number,
  baseElongation: number,
  baseTilt: number,
  surfaceBoundary: HourSnapshot['surfaceBoundary'],
): TierCenter {
  // Fractional depth into the risk stack (0 = outermost, 1 = innermost core)
  const depth = totalTiers <= 1 ? 0 : tierOrd / (totalTiers - 1);
  const frac = totalTiers <= 1 ? 1 : (totalTiers - tierOrd) / totalTiers;

  // ── Radius: steep power law so TSTM is MASSIVE and inner tiers are tight ─
  // Real SPC proportions: TSTM ~6° lat, MRGL ~3.5°, SLGT ~1.5°, ENH ~0.75°
  // This needs an aggressive exponent (~1.6) to reproduce the huge TSTM-to-core gap
  const organizedCore = tierOrd >= 2;
  const rLat = Math.max(0.30, baseLat * Math.pow(frac, 1.55) * (organizedCore ? 0.55 : 1));

  // ── Elongation INCREASES for inner tiers ──────────────────────────
  // Real SPC: TSTM aspect ~2.1, MRGL ~2.9, SLGT ~4.5, ENH ~3.3
  // Inner tiers become narrow strips along the forcing axis
  const tierElongation = baseElongation + depth * (organizedCore ? 0.75 : 1.6);
  const rLon = rLat * tierElongation;

  // ── Shear-vector displacement: higher tiers shift downshear ───────
  const shearAngle =
    ing.stormMode === 'linear' ? -8 :
    ing.stormMode === 'discrete' ? -22 :
    ing.stormMode === 'mixed' ? -14 : -12;
  const shearRad = (shearAngle * Math.PI) / 180;
  const shearStrength = Math.min(ing.shear06Kt / 50, 1.4);
  const offsetMag = depth * (organizedCore ? (0.25 + shearStrength * 0.35) : (1.5 + shearStrength * 1.0));
  const offsetLon = offsetMag * Math.cos(shearRad);
  const offsetLat = offsetMag * Math.sin(shearRad);

  // ── Moisture pull: TSTM/MRGL extend toward Gulf moisture ──────────
  const moistPull = Math.min(Math.max(ing.pwatIn - 1.0, 0) / 1.3, 1);
  const moistLat = -moistPull * (organizedCore ? 0.22 : 0.6) * (1 - depth * 0.5);
  const moistLon = moistPull * (organizedCore ? 0.12 : 0.3) * (1 - depth * 0.4);

  // ── Frontal forcing: shifts core toward forcing axis ──────────────
  const frontPull =
    ing.frontSignal === 'strong' ? 0.8 :
    ing.frontSignal === 'moderate' ? 0.45 :
    ing.frontSignal === 'weak' ? 0.15 : 0;
  const frontLon = frontPull * (organizedCore ? 0.18 : 0.5) * depth;
  const frontLat = frontPull * (organizedCore ? -0.10 : -0.2) * depth;

  // ── Forecast-hour drift: very gentle positional evolution ───────────
  // Real SPC outlooks are valid for a fixed window, NOT a propagating
  // storm-motion track.  We allow a very small drift (≤2° total) that
  // saturates quickly so late hours don't push shapes off CONUS.
  const driftMax = 2.0;                // max degrees of translation
  const driftRate = 0.06;              // degrees per forecast hour (gentle)
  const rawDrift = forecastHour * driftRate;
  const clampedDrift = driftMax * (1 - Math.exp(-rawDrift / driftMax));
  const hourLon = clampedDrift * (organizedCore ? 0.12 : 0.85); // mostly eastward
  const hourLat = clampedDrift * (organizedCore ? 0.02 : 0.15); // slight northward

  // ── Tilt: mostly east-west (like real SPC), inner tiers follow shear ──
  const tiltDeg = baseTilt + depth * (shearStrength * 6 - 2) +
    (ing.stormMode === 'linear' ? 5 + depth * 4 : ing.stormMode === 'discrete' ? -3 : 0);

  // ── CONUS geographic clamping: keep centers on land ─────────────────
  // Prevents shapes from drifting into the Atlantic, Gulf, or off CONUS
  const boundaryWeight = organizedCore && surfaceBoundary && surfaceBoundary.confidence >= 0.4
    ? Math.min(0.62, 0.34 + surfaceBoundary.confidence * 0.26)
    : 0;
  const anchorLat = boundaryWeight > 0
    ? region.centerLat * (1 - boundaryWeight) + surfaceBoundary!.lat * boundaryWeight
    : region.centerLat;
  const anchorLon = boundaryWeight > 0
    ? region.centerLon * (1 - boundaryWeight) + surfaceBoundary!.lon * boundaryWeight
    : region.centerLon;
  let rawLat = anchorLat + offsetLat + moistLat + frontLat + hourLat;
  let rawLon = anchorLon + offsetLon + moistLon + frontLon + hourLon;
  if (tierOrd >= 2) {
    [rawLon, rawLat] = clipOrganizedSevereCenter(rawLon, rawLat);
    [rawLon, rawLat] = clipOrganizedLevelPoint(rawLon, rawLat);
  }
  return {
    lat: Math.max(25, Math.min(49, rawLat)),
    lon: Math.max(-124, Math.min(-72, rawLon)),
    rLat,
    rLon,
    tiltDeg,
  };
}

// ── Multi-harmonic organic contour generator ────────────────────────
// Produces an SPC-like organic outline by combining:
//   - Base harmonic wobble (gives each tier a distinct organic shape)
//   - Directional asymmetry: downstream bulge, upstream notch
//   - Moisture tongue extending toward the warm sector
//   - Cap notch carving the upstream/west flank
//   - Frontal ridge extending along the boundary
//   - Linear mode extreme elongation along the shear axis
function organicContour(
  center: TierCenter,
  n: number,
  deform: {
    tierOrd: number;
    totalTiers: number;
    modePinch: number;       // linear stretching / discrete rounding
    forcingBulge: number;    // how much the downstream flank bulges
    capNotch: number;        // how deep the upstream notch cuts
    moistureTail: number;    // southward extension toward moisture
    hourPhase: number;       // time evolution
    shearStrength: number;   // 0..~1.4
  },
): [number, number][] {
  const { tiltDeg, rLat, rLon } = center;
  const tilt = (tiltDeg * Math.PI) / 180;
  const cosT = Math.cos(tilt);
  const sinT = Math.sin(tilt);
  const depth = deform.totalTiers <= 1 ? 0 : deform.tierOrd / (deform.totalTiers - 1);
  const out: [number, number][] = [];

  for (let i = 0; i < n; i++) {
    const t = -(i / n) * Math.PI * 2;

    // Directional masks (cosine lobes centered on compass headings in shape-local space)
    const downstream  = Math.max(0, Math.cos(t));                      // east / along-shear
    const upstream    = Math.max(0, Math.cos(t - Math.PI));            // west / rear
    const southFlank  = Math.max(0, Math.sin(t + Math.PI * 0.5));     // south
    const northFlank  = Math.max(0, Math.sin(t - Math.PI * 0.5));     // north
    const seFanOut    = Math.max(0, Math.cos(t - Math.PI * 0.25));    // SE quadrant

    // ── Multi-harmonic wobble (gives organic non-elliptical outline) ──
    // Scale wobble up for inner tiers (they're smaller so need proportionally
    // more perturbation to look organic) and keep outer tiers smoother.
    const seed = deform.tierOrd * 0.73 + 0.4;
    const hp = deform.hourPhase;
    const wobScale = 0.6 + depth * 0.6; // outer=0.6, inner=1.2
    const wob =
      1 +
      wobScale * (0.10 + deform.modePinch * 0.15) * Math.sin(2 * t + 0.9 + seed + hp * 0.35) +
      wobScale * (0.07 + deform.forcingBulge * 0.12) * Math.sin(3 * t + 1.6 - hp * 0.25 + seed * 0.6) +
      wobScale * (0.04 + deform.moistureTail * 0.08) * Math.sin(5 * t + 0.7 + seed * 1.3) +
      wobScale * 0.025 * Math.sin(7 * t + 2.1 + seed * 0.8 + hp * 0.15) +
      wobScale * 0.015 * Math.sin(11 * t + 0.3 + seed * 1.7);

    // ── Directional asymmetry multipliers ────────────────────────────
    // Outer tiers get gentler deformation, inner tiers get stronger.
    const asymScale = 0.5 + depth * 0.7;

    // Downstream bulge: risk extends ahead of the system
    const forwardBulge = 1 + deform.forcingBulge * asymScale * 0.45 * downstream;

    // Upstream cap notch: capped rear flank carved out
    const rearNotch = 1 - deform.capNotch * asymScale * 0.40 * upstream;

    // Moisture tongue: extends south/SE toward Gulf moisture
    const mTail = 1 + deform.moistureTail * asymScale * 0.35 * southFlank +
                  deform.moistureTail * 0.15 * seFanOut;

    // Northern cutoff: sharp boundary on the cool side
    const northCut = 1 - Math.max(0, deform.modePinch) * asymScale * 0.25 * northFlank -
                     deform.capNotch * 0.10 * northFlank;

    // Linear mode along-shear elongation
    const linearStretch = deform.modePinch > 0.15
      ? 1 + deform.modePinch * asymScale * 0.30 * (downstream + upstream * 0.6)
      : 1;

    // SE protrusion for warm-sector bulge
    const shearProtrusion = 1 + deform.shearStrength * 0.08 * seFanOut * (1 - depth * 0.3);

    const combinedR = wob * forwardBulge * rearNotch * mTail * northCut * linearStretch * shearProtrusion;

    const ex = rLon * combinedR * Math.cos(t);
    const ey = rLat * combinedR * Math.sin(t);
    let lon = center.lon + (ex * cosT - ey * sinT);
    let lat = center.lat + (ex * sinT + ey * cosT);

    // Soft CONUS boundary clamping — compress points that extend over
    // water back toward land. Hard limits prevent any ocean plotting.
    lat = Math.max(24, Math.min(50, lat));
    lon = Math.max(-125, Math.min(-66, lon));
    out.push([lon, lat]);
  }
  return out;
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

function bandFeatureKey(category: RiskCategory, coords: [number, number][]): string {
  const [lon, lat] = bandCentroid(coords);
  return `${category}-${lon.toFixed(2)}-${lat.toFixed(2)}`;
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
  // D3's spherical GeoJSON fill follows ring winding. For these projected
  // lon/lat rings, the rendered exterior needs the opposite winding from the
  // planar helper's positive area; otherwise D3 paints the outside of the
  // contour and the whole map looks highlighted.
  return signedRingArea(coords) > 0 ? [...coords].reverse() : coords;
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

function sampleRing(coords: [number, number][], targetCount: number): [number, number][] {
  const step = Math.max(1, Math.floor(coords.length / targetCount));
  return coords.filter((_, index) => index % step === 0);
}

function bandContainsBand(parent: LevelBand, child: LevelBand): boolean {
  const parentBounds = bandBounds(parent.coords);
  const childBounds = bandBounds(child.coords);
  if (
    childBounds.minLon < parentBounds.minLon ||
    childBounds.maxLon > parentBounds.maxLon ||
    childBounds.minLat < parentBounds.minLat ||
    childBounds.maxLat > parentBounds.maxLat
  ) {
    return false;
  }

  const samples = [...sampleRing(child.coords, 48), bandCentroid(child.coords)];
  return samples.every((point) => pointInPolygon(point, parent.coords));
}

function centroidDistance(a: LevelBand, b: LevelBand): number {
  const [aLon, aLat] = bandCentroid(a.coords);
  const [bLon, bLat] = bandCentroid(b.coords);
  const meanLat = ((aLat + bLat) / 2) * Math.PI / 180;
  return Math.hypot((aLon - bLon) * Math.cos(meanLat), aLat - bLat);
}

function padRing(coords: [number, number][], padDeg: number): [number, number][] {
  const [centerLon, centerLat] = bandCentroid(coords);
  const lonScale = Math.max(0.5, Math.cos(centerLat * Math.PI / 180));
  return coords.map(([lon, lat]) => {
    const x = (lon - centerLon) * lonScale;
    const y = lat - centerLat;
    const r = Math.max(0.001, Math.hypot(x, y));
    const scale = (r + padDeg) / r;
    return [
      Math.max(-125, Math.min(-66, centerLon + (x * scale) / lonScale)),
      Math.max(24, Math.min(50, centerLat + y * scale)),
    ];
  });
}

function boundsGap(a: BandBounds, b: BandBounds): { lon: number; lat: number } {
  return {
    lon: Math.max(0, Math.max(a.minLon - b.maxLon, b.minLon - a.maxLon)),
    lat: Math.max(0, Math.max(a.minLat - b.maxLat, b.minLat - a.maxLat)),
  };
}

function categoryFluidConfig(category: RiskCategory): {
  gapLon: number;
  gapLat: number;
  maxDistance: number;
  maxSpanLon: number;
  maxSpanLat: number;
  pad: number;
} {
  switch (category) {
    case 'TSTM': return { gapLon: 1.6, gapLat: 1.1, maxDistance: 9, maxSpanLon: 22, maxSpanLat: 12, pad: 0.26 };
    case 'MRGL': return { gapLon: 1.2, gapLat: 0.8, maxDistance: 8, maxSpanLon: 18, maxSpanLat: 9, pad: 0.18 };
    case 'SLGT': return { gapLon: 0.8, gapLat: 0.55, maxDistance: 6, maxSpanLon: 14, maxSpanLat: 7, pad: 0.10 };
    case 'ENH':  return { gapLon: 0.9, gapLat: 0.7, maxDistance: 6, maxSpanLon: 14, maxSpanLat: 7, pad: 0.12 };
    case 'MOD':  return { gapLon: 0.7, gapLat: 0.55, maxDistance: 4.5, maxSpanLon: 10, maxSpanLat: 5, pad: 0.08 };
    case 'HIGH': return { gapLon: 0.55, gapLat: 0.45, maxDistance: 3.5, maxSpanLon: 8, maxSpanLat: 4, pad: 0.06 };
    default:     return { gapLon: 1.5, gapLat: 1.0, maxDistance: 8, maxSpanLon: 20, maxSpanLat: 10, pad: 0.18 };
  }
}

function shouldFluidMerge(a: LevelBand, b: LevelBand): boolean {
  if (a.category !== b.category) return false;
  const cfg = categoryFluidConfig(a.category);
  const aBounds = bandBounds(a.coords);
  const bBounds = bandBounds(b.coords);
  const mergedBounds = {
    minLon: Math.min(aBounds.minLon, bBounds.minLon),
    maxLon: Math.max(aBounds.maxLon, bBounds.maxLon),
    minLat: Math.min(aBounds.minLat, bBounds.minLat),
    maxLat: Math.max(aBounds.maxLat, bBounds.maxLat),
  };
  if (
    mergedBounds.maxLon - mergedBounds.minLon > cfg.maxSpanLon ||
    mergedBounds.maxLat - mergedBounds.minLat > cfg.maxSpanLat
  ) {
    return false;
  }

  const gap = boundsGap(aBounds, bBounds);
  if (gap.lon <= cfg.gapLon && gap.lat <= cfg.gapLat) return true;

  const [aLon, aLat] = bandCentroid(a.coords);
  const [bLon, bLat] = bandCentroid(b.coords);
  const meanLat = ((aLat + bLat) / 2) * Math.PI / 180;
  const dist = Math.hypot((aLon - bLon) * Math.cos(meanLat), aLat - bLat);
  return dist <= cfg.maxDistance && (gap.lon <= cfg.gapLon * 1.5 || gap.lat <= cfg.gapLat * 1.5);
}

function connectedBandGroups(bands: LevelBand[]): LevelBand[][] {
  const visited = new Set<number>();
  const groups: LevelBand[][] = [];

  for (let i = 0; i < bands.length; i++) {
    if (visited.has(i)) continue;
    const queue = [i];
    const group: LevelBand[] = [];
    visited.add(i);

    while (queue.length > 0) {
      const idx = queue.shift()!;
      group.push(bands[idx]);
      for (let j = 0; j < bands.length; j++) {
        if (visited.has(j)) continue;
        if (!shouldFluidMerge(bands[idx], bands[j])) continue;
        visited.add(j);
        queue.push(j);
      }
    }
    groups.push(group);
  }

  return groups;
}

function boundedFluidGroups(categoryBands: LevelBand[], category: RiskCategory): LevelBand[][] {
  const cfg = categoryFluidConfig(category);
  return connectedBandGroups(categoryBands).flatMap((group) => {
    const bounds = bandBounds(group.flatMap((band) => band.coords));
    if (
      bounds.maxLon - bounds.minLon <= cfg.maxSpanLon &&
      bounds.maxLat - bounds.minLat <= cfg.maxSpanLat
    ) {
      return [group];
    }
    return group.map((band) => [band]);
  });
}

function fluidEnvelope(group: LevelBand[]): [number, number][] {
  if (group.length === 1) return group[0].coords;

  const category = group[0].category;
  const cfg = categoryFluidConfig(category);
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
  const allCoords = group.flatMap((band) => band.coords);

  allCoords.forEach(([lon, lat]) => {
    const x = (lon - centerLon) * lonScale;
    const y = lat - centerLat;
    const angle = Math.atan2(y, x);
    const idx = Math.floor((((angle + Math.PI) / (Math.PI * 2)) * samples)) % samples;
    const r = Math.hypot(x, y) + cfg.pad;
    radii[idx] = Math.max(radii[idx], r);
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

  const envelope = smoothRadii.map((r, i): [number, number] => {
    const angle = (i / samples) * Math.PI * 2 - Math.PI;
    const lon = centerLon + (Math.cos(angle) * r) / lonScale;
    const lat = centerLat + Math.sin(angle) * r;
    return [Math.max(-125, Math.min(-66, lon)), Math.max(24, Math.min(50, lat))];
  });

  return chaikinSmooth(envelope, 1);
}

function mergeFluidLevelBands(bands: LevelBand[]): LevelBand[] {
  const merged = CATEGORY_RAMP.flatMap((category) => {
    const categoryBands = bands.filter((band) => band.category === category);
    return boundedFluidGroups(categoryBands, category).map((group) => ({
      category,
      coords: normalizeExteriorRing(fluidEnvelope(group)),
    }));
  });
  return enforceNestedLevelBands(merged);
}

function expandedParentBand(parent: LevelBand, child: LevelBand): LevelBand {
  const parentShell = { category: parent.category, coords: parent.coords };
  const childShell = { category: parent.category, coords: child.coords };
  const paddedChild = { category: parent.category, coords: padRing(child.coords, 0.16) };
  const expanded = {
    category: parent.category,
    coords: normalizeExteriorRing(fluidEnvelope([parentShell, childShell, paddedChild])),
  };

  if (bandContainsBand(expanded, child)) return expanded;
  return {
    category: parent.category,
    coords: normalizeExteriorRing(fluidEnvelope([parentShell, { ...paddedChild, coords: padRing(child.coords, 0.36) }])),
  };
}

function parentBandFromChild(parentCategory: RiskCategory, child: LevelBand): LevelBand {
  return {
    category: parentCategory,
    coords: normalizeExteriorRing(padRing(child.coords, 0.45)),
  };
}

function enforceNestedLevelBands(bands: LevelBand[]): LevelBand[] {
  const working = bands.map((band) => ({ category: band.category, coords: band.coords }));

  for (let childOrd = CATEGORY_RAMP.length - 1; childOrd > 0; childOrd--) {
    const childCategory = CATEGORY_RAMP[childOrd];
    const parentCategory = CATEGORY_RAMP[childOrd - 1];
    const children = working.filter((band) => band.category === childCategory);

    children.forEach((child) => {
      const parents = working
        .map((band, index) => ({ band, index }))
        .filter(({ band }) => band.category === parentCategory);
      if (parents.length === 0) {
        working.push(parentBandFromChild(parentCategory, child));
        return;
      }

      if (parents.some(({ band }) => bandContainsBand(band, child))) return;

      const nearest = parents.reduce((best, candidate) => (
        centroidDistance(candidate.band, child) < centroidDistance(best.band, child) ? candidate : best
      ));
      working[nearest.index] = expandedParentBand(nearest.band, child);
    });
  }

  return CATEGORY_RAMP.flatMap((category) => working.filter((band) => band.category === category));
}

export function levelBands(snapshot: HourSnapshot | null): LevelBand[] {
  if (!snapshot) return [];

  const areas = displayOutlookAreas(snapshot);
  const hasMultipleAreas = (snapshot.outlookAreas?.length ?? 0) > 0;

  const rawBands = areas.flatMap((area, areaIdx) => {
    const peakOrd = CATEGORY_RAMP.indexOf(area.category);
    const active = CATEGORY_RAMP.slice(0, peakOrd + 1);
    const region = area.region;
    const ing: Ingredients = { ...snapshot.ingredients, ...(area.ingredients ?? {}) };
    const surfaceBoundary = areaIdx === 0 && !snapshot.outlookAreas?.length ? snapshot.surfaceBoundary : undefined;

  // ── Global shape parameters derived from ingredients ───────────────
  // Real SPC outlooks are predominantly east-west oriented.
  // Base tilt is shallow (nearly horizontal) with slight SW-NE lean.
  const baseTilt =
    ing.stormMode === 'linear' ? -5 :
    ing.stormMode === 'discrete' ? -12 :
    ing.shear06Kt >= 45 ? -10 : -8;

  // Base lat radius must be large enough so TSTM tier covers a huge area
  // like real SPC. With power exponent 1.55:
  //   TSTM (frac=1.0)  → 8.0°  → ~16° lon  (covers ~20° of CONUS)
  //   MRGL (frac=0.75)  → ~4.7° → ~12° lon
  //   SLGT (frac=0.50)  → ~2.3° → ~8° lon
  //   ENH  (frac=0.25)  → ~0.8° → ~3° lon
  const capeScale = Math.min(1.0, Math.max(0.44, ing.mucape / 2600));
  const base = hasMultipleAreas
    ? (
      area.category === 'TSTM' ? 1.45 :
      area.category === 'MRGL' ? 2.05 :
      2.85
    ) * capeScale
    : area.category === 'TSTM' ? 4.2 : 5.2;

  // Base elongation: real SPC has ~2.0-2.5 aspect on outer tiers,
  // then inner tiers get +1.6 from tierCenter (up to ~4.5 for SLGT/ENH)
  const elongation =
    1.85 +
    Math.min(ing.shear06Kt / 100, 0.45) +
    (ing.stormMode === 'linear' ? 0.35 : ing.stormMode === 'discrete' ? -0.15 : 0.04);

  const modePinch =
    ing.stormMode === 'linear' ? 0.30 :
    ing.stormMode === 'discrete' ? -0.10 :
    ing.stormMode === 'multicell' ? 0.06 : 0.14;

  const forcingBulge =
    ing.frontSignal === 'strong' ? 0.35 :
    ing.frontSignal === 'moderate' ? 0.20 : 0.08;

  const capNotch =
    ing.capStrength === 'strong' ? 0.35 :
    ing.capStrength === 'moderate' ? 0.22 :
    ing.capStrength === 'weak' ? 0.10 : 0.02;

  const moistureTail = Math.min(Math.max(ing.pwatIn - 0.9, 0) / 1.1, 1) * 0.35;
  const hourPhase = snapshot.forecastHour * 0.19;
  const shearStrength = Math.min(ing.shear06Kt / 50, 1.4);

  return active.map((category, idx) => {
    const tc = tierCenter(region, ing, idx, active.length, snapshot.forecastHour, base, elongation, baseTilt, surfaceBoundary);
    const raw = organicContour(tc, 96, {
      tierOrd: idx,
      totalTiers: active.length,
      modePinch,
      forcingBulge,
      capNotch,
      moistureTail,
      hourPhase,
      shearStrength,
    });
    const smoothed = chaikinSmooth(raw, 2);
    return {
      category,
      coords: isOrganizedSevereCategory(category)
        ? clipOrganizedLevelPolygon(smoothed)
        : smoothed,
    };
  });
  });
  return mergeFluidLevelBands(rawBands);
}

export default function SpcLevelOutlookMap({ snapshot }: SpcLevelOutlookMapProps) {
  const bands = useMemo(() => levelBands(snapshot), [snapshot]);
  const featureCollection = useMemo(
    () => ({
      type: 'FeatureCollection' as const,
      features: bands.map((band, idx): LevelFeature => {
        const style = LEVEL_STYLE[band.category];
        return {
          type: 'Feature',
          properties: {
            idx,
            featureKey: bandFeatureKey(band.category, band.coords),
            category: band.category,
            fill: style.fill,
            stroke: style.stroke,
          },
          geometry: { type: 'Polygon', coordinates: [[...band.coords, band.coords[0]]] },
        };
      }),
    }),
    [bands],
  );
  const upperAirLineCollection = useMemo(
    () => {
      const lines = map500mbLines(snapshot);
      return {
        type: 'FeatureCollection' as const,
        features: lines
        .map((line, idx): UpperAirFeature => {
          const style = upperAirLineVisualStyle(snapshot, idx, lines.length);
          return {
          type: 'Feature',
          properties: { idx, value: line.value, ...style },
          geometry: { type: 'LineString', coordinates: line.coords },
          };
        }),
      };
    },
    [snapshot],
  );
  const upperAirStreakCollection = useMemo(
    () => ({
      type: 'FeatureCollection' as const,
      features: buildUpperAirIntensitySegments(snapshot, map500mbLines(snapshot))
        .map((segment, idx): UpperAirStreakFeature => ({
          type: 'Feature',
          properties: {
            idx,
            stroke: segment.stroke,
            strokeWidth: segment.strokeWidth,
            strokeOpacity: segment.strokeOpacity,
          },
          geometry: { type: 'LineString', coordinates: segment.coords },
        })),
    }),
    [snapshot],
  );
  const windVectors = useMemo(() => map500mbWindVectors(snapshot), [snapshot]);

  return (
    <div className="border-[3px] border-ink bg-paper shadow-retro flex flex-col">
      <header className="border-b-[2px] border-ink bg-ink text-paper px-3 py-1.5 flex items-center justify-between gap-2">
        <span className="min-w-0 font-display font-extrabold uppercase text-[13px] leading-tight tracking-wider">
          SPC Levels Outlook
        </span>
        <span className="font-mono text-[10px] uppercase tracking-widest text-paper/70 shrink-0">
          CAT {snapshot?.outlook.category ?? '--'}
        </span>
      </header>

      <div className="aspect-[16/9] xl:aspect-[2/1] relative overflow-hidden bg-[#fbfbf8]">
        <ComposableMap
          projection="geoAlbers"
          width={900}
          height={520}
          projectionConfig={{
            rotate: [96, 0, 0],
            center: [0, 38],
            parallels: [29.5, 45.5],
            scale: 1000,
          }}
          style={{ width: '100%', height: '100%' }}
        >
          <Geographies geography={STATES_URL}>
            {({ geographies }) =>
              geographies.map((geo) => (
                <Geography
                  key={geo.rsmKey}
                  geography={geo}
                  style={{
                    default: { fill: '#ffffff', stroke: '#b8b8b8', strokeWidth: 0.65, outline: 'none' },
                    hover:   { fill: '#ffffff', stroke: '#b8b8b8', strokeWidth: 0.65, outline: 'none' },
                    pressed: { fill: '#ffffff', stroke: '#b8b8b8', strokeWidth: 0.65, outline: 'none' },
                  }}
                />
              ))
            }
          </Geographies>

          {upperAirLineCollection.features.length > 0 && (
            <Geographies geography={upperAirLineCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`spc-h500-${geo.rsmKey ?? index}`}
                    geography={geo}
                    style={{
                      default: { fill: 'none', stroke: '#7e858b', strokeWidth: 0.75, strokeOpacity: 0.28, outline: 'none' },
                      hover:   { fill: 'none', stroke: '#7e858b', strokeWidth: 0.75, strokeOpacity: 0.28, outline: 'none' },
                      pressed: { fill: 'none', stroke: '#7e858b', strokeWidth: 0.75, strokeOpacity: 0.28, outline: 'none' },
                    }}
                  />
                ))
              }
            </Geographies>
          )}

          {featureCollection.features.length > 0 && (
            <Geographies geography={featureCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const featureProps = featureCollection.features[index]?.properties;
                  const fill = featureProps?.fill ?? LEVEL_STYLE.TSTM.fill;
                  const stroke = featureProps?.stroke ?? LEVEL_STYLE.TSTM.stroke;
                  return (
                  <Geography
                    key={`spc-level-${featureProps?.featureKey ?? geo.rsmKey ?? index}`}
                    geography={geo}
                    tabIndex={-1}
                    style={{
                      default: {
                        fill,
                        fillOpacity: 0.48,
                        stroke,
                        strokeWidth: 2.2,
                        outline: 'none',
                        pointerEvents: 'none',
                      },
                      hover: {
                        fill,
                        fillOpacity: 0.48,
                        stroke,
                        strokeWidth: 2.2,
                        outline: 'none',
                        pointerEvents: 'none',
                      },
                      pressed: {
                        fill,
                        fillOpacity: 0.48,
                        stroke,
                        strokeWidth: 2.2,
                        outline: 'none',
                        pointerEvents: 'none',
                      },
                    }}
                  />
                  );
                })
              }
            </Geographies>
          )}

          {/* Redraw state borders above translucent outlook fills so the base
              map remains readable, closer to the rawinsonde/SPC presentation. */}
          <Geographies geography={STATES_URL}>
            {({ geographies }) =>
              geographies.map((geo) => (
                <Geography
                  key={`state-outline-${geo.rsmKey}`}
                  geography={geo}
                  style={{
                    default: { fill: 'none', stroke: '#8f8f8f', strokeWidth: 0.7, strokeOpacity: 0.85, outline: 'none' },
                    hover:   { fill: 'none', stroke: '#8f8f8f', strokeWidth: 0.7, strokeOpacity: 0.85, outline: 'none' },
                    pressed: { fill: 'none', stroke: '#8f8f8f', strokeWidth: 0.7, strokeOpacity: 0.85, outline: 'none' },
                  }}
                />
              ))
            }
          </Geographies>

          {upperAirLineCollection.features.length > 0 && (
            <Geographies geography={upperAirLineCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`spc-h500-halo-${geo.rsmKey ?? index}`}
                    geography={geo}
                    style={{
                      default: {
                        fill: 'none',
                        stroke: '#ffffff',
                        strokeWidth: geo.properties.haloWidth as number,
                        strokeOpacity: geo.properties.haloOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      hover: {
                        fill: 'none',
                        stroke: '#ffffff',
                        strokeWidth: geo.properties.haloWidth as number,
                        strokeOpacity: geo.properties.haloOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      pressed: {
                        fill: 'none',
                        stroke: '#ffffff',
                        strokeWidth: geo.properties.haloWidth as number,
                        strokeOpacity: geo.properties.haloOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                    }}
                  />
                ))
              }
            </Geographies>
          )}

          {upperAirLineCollection.features.length > 0 && (
            <Geographies geography={upperAirLineCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`spc-h500-intensity-${geo.rsmKey ?? index}`}
                    geography={geo}
                    style={{
                      default: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      hover: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      pressed: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                    }}
                  />
                ))
              }
            </Geographies>
          )}

          {upperAirStreakCollection.features.length > 0 && (
            <Geographies geography={upperAirStreakCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`spc-h500-streak-${geo.rsmKey ?? index}`}
                    geography={geo}
                    style={{
                      default: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      hover: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      pressed: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                    }}
                  />
                ))
              }
            </Geographies>
          )}

          {windVectors.map((vector, idx) => (
            <Marker key={`spc-wind-vector-top-${idx}`} coordinates={[vector.lon, vector.lat]}>
              <WindBarb vector={vector} top />
            </Marker>
          ))}
        </ComposableMap>

        <div className="absolute bottom-2 left-2 border-[2px] border-ink bg-paper px-2.5 py-2 shadow-retro-sm">
          <div className="font-mono text-[9px] uppercase tracking-[0.22em] text-ink/70 leading-none mb-1.5">
            Probability of occurrence within 40km
          </div>
          <div className="grid grid-cols-3 gap-x-2 gap-y-1">
            {CATEGORY_RAMP.map((category) => (
              <div key={category} className="flex items-center gap-1 font-mono text-[10px] font-bold leading-none">
                <span
                  className="inline-block h-3 w-3 border-[1.5px] border-ink"
                  style={{ backgroundColor: LEVEL_STYLE[category].fill }}
                  aria-hidden
                />
                <span>{LEVEL_STYLE[category].label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function WindBarb({ vector, top = false }: { vector: UpperAirVector; top?: boolean }) {
  if (!top || vector.speedKt < 22) return null;
  const length = 12;
  const featherCount = Math.max(1, Math.min(4, Math.round(vector.speedKt / 22)));
  const angleDeg = (Math.atan2(-vector.vKt, vector.uKt) * 180 / Math.PI) + 180;
  const opacity = 0.82;
  const stroke = '#50565c';
  const halo = '#ffffff';
  const feathers = (prefix: string) => Array.from({ length: featherCount }, (_, i) => {
    const x = length - i * 3.0;
    return <path key={`${prefix}-${i}`} d={`M ${x} 0 L ${x - 4.0} 5.2`} />;
  });

  return (
    <g transform={`rotate(${angleDeg})`} opacity={opacity} strokeLinecap="square">
      <g stroke={halo} strokeWidth={3.2} fill="none" opacity={0.72}>
        <path d={`M ${-length} 0 L ${length} 0`} />
        {feathers('halo')}
      </g>
      <g stroke={stroke} strokeWidth={1.7} fill="none">
        <path d={`M ${-length} 0 L ${length} 0`} />
        {feathers('main')}
      </g>
    </g>
  );
}
