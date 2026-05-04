import type { HourSnapshot } from '../types/forecast';

export interface TriplePointBoundary {
  kind: 'cold' | 'warm' | 'dryline';
  coords: [number, number][];
  symbolCoords: [number, number][];
}

export interface TriplePointSignal {
  lat: number;
  lon: number;
  confidence: number;
  label: string;
  boundaries: TriplePointBoundary[];
}

function lineFrom(
  lon: number,
  lat: number,
  angleDeg: number,
  lengthDeg: number,
  steps = 18,
): [number, number][] {
  const a = (angleDeg * Math.PI) / 180;
  const dx = Math.cos(a) * lengthDeg;
  const dy = Math.sin(a) * lengthDeg;
  const startLon = lon - dx * 0.08;
  const startLat = lat - dy * 0.08;
  const out: [number, number][] = [];

  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const wob = Math.sin(t * Math.PI * 2) * 0.18;
    out.push([
      startLon + dx * t,
      startLat + dy * t + wob,
    ]);
  }

  return out;
}

function symbolsAlong(coords: [number, number][], count: number): [number, number][] {
  if (coords.length === 0) return [];
  const out: [number, number][] = [];
  for (let i = 1; i <= count; i++) {
    const idx = Math.min(coords.length - 1, Math.round((i / (count + 1)) * (coords.length - 1)));
    out.push(coords[idx]);
  }
  return out;
}

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

function signalScore(strength: string): number {
  return strength === 'strong' ? 1 : strength === 'moderate' ? 0.72 : strength === 'weak' ? 0.38 : 0;
}

function capScore(strength: string): number {
  return strength === 'strong' ? 1 : strength === 'moderate' ? 0.62 : strength === 'weak' ? 0.28 : 0;
}

function clampToRegion(snapshot: HourSnapshot, lon: number, lat: number): { lat: number; lon: number } {
  const [minLon, minLat, maxLon, maxLat] = snapshot.region.bbox;
  const validBbox = Number.isFinite(minLon) && Number.isFinite(minLat) && Number.isFinite(maxLon) && Number.isFinite(maxLat) &&
    minLon < maxLon && minLat < maxLat;
  const bboxLonPad = validBbox ? Math.min(0.7, Math.max(0.25, (maxLon - minLon) * 0.06)) : 0;
  const bboxLatPad = validBbox ? Math.min(0.6, Math.max(0.20, (maxLat - minLat) * 0.06)) : 0;
  const lonMin = validBbox ? Math.max(-125, minLon + bboxLonPad) : -125;
  const lonMax = validBbox ? Math.min(-66, maxLon - bboxLonPad) : -66;
  const latMin = validBbox ? Math.max(24, minLat + bboxLatPad) : 24;
  const latMax = validBbox ? Math.min(50, maxLat - bboxLatPad) : 50;

  return {
    lon: clamp(lon, lonMin, lonMax),
    lat: clamp(lat, latMin, latMax),
  };
}

function isDrylineSupported(snapshot: HourSnapshot): boolean {
  const { centerLon, centerLat, states } = snapshot.region;
  const drylineStates = new Set(['TX', 'OK', 'KS', 'NE', 'SD', 'ND', 'CO', 'NM', 'WY', 'MT']);
  const plainsState = states.some((state) => drylineStates.has(state));
  const westEnough = centerLon <= -92 && centerLat >= 27.5 && centerLat <= 46;
  const warmSectorMoisture = snapshot.ingredients.sfcDewpointF >= 58;
  const boundarySignal =
    snapshot.ingredients.capStrength !== 'none' ||
    signalScore(snapshot.ingredients.frontSignal) >= 0.72 ||
    snapshot.ingredients.cin <= -25;

  return plainsState && westEnough && warmSectorMoisture && boundarySignal;
}

function boundaryAnchor(snapshot: HourSnapshot, drylineSupported: boolean): { lat: number; lon: number } {
  const analyzed = snapshot.surfaceBoundary;
  if (analyzed && analyzed.confidence >= 0.38) {
    return clampToRegion(snapshot, analyzed.lon, analyzed.lat);
  }

  const ing = snapshot.ingredients;
  const shearNorm = clamp((ing.shear06Kt - 25) / 40, 0, 1);
  const frontNorm = signalScore(ing.frontSignal);
  const capNorm = capScore(ing.capStrength);
  const discreteBias = ing.stormMode === 'discrete' ? 1 : ing.stormMode === 'mixed' ? 0.6 : 0;
  const linearBias = ing.stormMode === 'linear' ? 1 : 0;
  const hourNorm = clamp(snapshot.forecastHour / 48, 0, 1);

  // The outlook center is generally in the warm sector. Keep this diagnostic
  // near the effective boundary intersection, not several degrees upstream.
  const westOffset = drylineSupported
    ? clamp(
      1.35 + capNorm * 0.55 + shearNorm * 0.38 + discreteBias * 0.30 - linearBias * 0.20 - hourNorm * 0.25,
      1.25,
      3.20,
    )
    : clamp(0.70 + frontNorm * 0.55 + shearNorm * 0.25 + linearBias * 0.18, 0.65, 2.10);
  const northOffset = drylineSupported
    ? clamp(
      0.30 + frontNorm * 0.32 + discreteBias * 0.35 + shearNorm * 0.18 - linearBias * 0.20 - hourNorm * 0.15,
      0.25,
      1.55,
    )
    : clamp(0.18 + frontNorm * 0.18 + shearNorm * 0.10 - linearBias * 0.05, 0.10, 0.85);

  return clampToRegion(snapshot, snapshot.region.centerLon - westOffset, snapshot.region.centerLat + northOffset);
}

export function deriveTriplePoint(snapshot: HourSnapshot): TriplePointSignal | null {
  const ing = snapshot.ingredients;
  const frontScore = signalScore(ing.frontSignal);
  const instabilityScore = Math.min(Math.max(ing.mlcape, ing.mucape) / 1800, 1);
  const shearScore = Math.min(ing.shear06Kt / 40, 1);
  const lowLevelScore = Math.min(Math.max(ing.srh01, 0) / 150, 1);
  const moistureScore = Math.min(Math.max(ing.sfcDewpointF - 55, 0) / 15, 1);
  const initiationScore = ing.initiationConf;
  const confidence = Math.max(0, Math.min(1,
    0.28 * frontScore +
    0.20 * instabilityScore +
    0.20 * shearScore +
    0.14 * lowLevelScore +
    0.10 * moistureScore +
    0.08 * initiationScore,
  ));

  if (confidence < 0.42) return null;

  const analyzedBoundary = snapshot.surfaceBoundary && snapshot.surfaceBoundary.confidence >= 0.38
    ? snapshot.surfaceBoundary
    : null;
  const drylineSupported = analyzedBoundary
    ? analyzedBoundary.kind !== 'frontal'
    : isDrylineSupported(snapshot);
  const frontalSupported = frontScore >= 0.72 && (ing.shear06Kt >= 30 || ing.srh03 >= 140);
  if (!analyzedBoundary && drylineSupported && (frontScore < 0.38 || ing.initiationConf < 0.35)) return null;
  if (!drylineSupported && !frontalSupported && !analyzedBoundary) return null;

  const { lon, lat } = boundaryAnchor(snapshot, drylineSupported);
  const length = 4.0 + Math.min(ing.shear06Kt, 60) / 20;
  const modeTilt =
    ing.stormMode === 'linear' ? -8 :
    ing.stormMode === 'discrete' ? -18 :
    ing.stormMode === 'multicell' ? 4 : -12;
  const cold = lineFrom(lon, lat, 220 + modeTilt, length * 0.9);
  const warm = lineFrom(lon, lat, 32 + modeTilt, length * 1.05);
  const dryline = drylineSupported
    ? lineFrom(lon, lat, 270 + modeTilt * 0.5, length * 0.8)
    : null;

  return {
    lat,
    lon,
    confidence,
    label: drylineSupported ? (confidence >= 0.65 ? 'TRIPLE POINT' : 'POSSIBLE TP') : 'BOUNDARY FOCUS',
    boundaries: [
      { kind: 'cold', coords: cold, symbolCoords: symbolsAlong(cold, 4) },
      { kind: 'warm', coords: warm, symbolCoords: symbolsAlong(warm, 4) },
      ...(dryline ? [{ kind: 'dryline' as const, coords: dryline, symbolCoords: symbolsAlong(dryline, 3) }] : []),
    ],
  };
}
