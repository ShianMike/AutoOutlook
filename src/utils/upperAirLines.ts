import type { HourSnapshot, UpperAirLine } from '../types/forecast';
import { get500mbAxisLat, get500mbFlowAngleDeg, get500mbMotion } from './upperAirMotion';

const MIN_MAP_LON_SPAN = 46;
const MIN_MAP_LAT_SPAN = 18;

export function map500mbLines(snapshot: HourSnapshot | null): UpperAirLine[] {
  if (!snapshot) return [];
  const modelLines = snapshot.upperAirLines ?? [];
  if (coversFullMap(modelLines)) return selectReadableLines(modelLines);
  return buildFullMap500mbLines(snapshot);
}

function coversFullMap(lines: UpperAirLine[]): boolean {
  let minLon = Infinity;
  let maxLon = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;
  let points = 0;

  for (const line of lines) {
    for (const [lon, lat] of line.coords) {
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
      minLon = Math.min(minLon, lon);
      maxLon = Math.max(maxLon, lon);
      minLat = Math.min(minLat, lat);
      maxLat = Math.max(maxLat, lat);
      points += 1;
    }
  }

  if (points < 24) return false;
  return (maxLon - minLon) >= MIN_MAP_LON_SPAN && (maxLat - minLat) >= MIN_MAP_LAT_SPAN;
}

function buildFullMap500mbLines(snapshot: HourSnapshot): UpperAirLine[] {
  const lines: UpperAirLine[] = [];
  const region = snapshot.region;
  const ing = snapshot.ingredients;
  const shear = Math.max(18, Math.min(85, ing.shear06Kt));
  const motion = get500mbMotion(snapshot);
  const sourceLonStart = -132 - motion.advectionLon - 6;
  const sourceLonEnd = -58 - motion.advectionLon + 6;

  for (let i = 0; i < 9; i++) {
    const value = 5340 + i * 60;
    const startLat = 20.5 + i * 3.8;
    let lat = startLat;
    const coords: [number, number][] = [];
    let lastSourceLon = sourceLonStart;

    for (let step = 0; step <= 72; step++) {
      const t = step / 72;
      const sourceLon = sourceLonStart + t * (sourceLonEnd - sourceLonStart);
      if (step > 0) {
        const midSourceLon = (sourceLon + lastSourceLon) * 0.5;
        const axis = get500mbAxisLat(snapshot, midSourceLon, motion);
        const distance = lat - axis;
        const jet = Math.exp(-(distance * distance) / 26);
        const angleDeg = get500mbFlowAngleDeg(midSourceLon, lat, axis);
        const dLon = sourceLon - lastSourceLon;
        lat += Math.tan((angleDeg * Math.PI) / 180) * dLon * 0.62;
        lat += (jet * shear / 85) * 0.015 * Math.sin((midSourceLon - region.centerLon) / 4);
      }
      coords.push([
        sourceLon + motion.advectionLon,
        Math.max(18, Math.min(54, lat + motion.advectionLat)),
      ]);
      lastSourceLon = sourceLon;
    }

    lines.push({ level: '500mb', value, coords });
  }

  return lines;
}

function selectReadableLines(lines: UpperAirLine[]): UpperAirLine[] {
  const sorted = [...lines].sort((a, b) => a.value - b.value);
  const targetCount = 9;
  if (sorted.length <= targetCount) return sorted;
  const stride = Math.max(1, Math.ceil(sorted.length / targetCount));
  return sorted.filter((_, idx) => idx % stride === 0).slice(0, targetCount);
}
