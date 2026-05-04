import type { HourSnapshot, UpperAirVector } from '../types/forecast';
import { get500mbAxisLat, get500mbFlowAngleDeg, get500mbMotion } from './upperAirMotion';

const MIN_VECTOR_POINTS = 48;
const MIN_VECTOR_LON_SPAN = 42;
const MIN_VECTOR_LAT_SPAN = 16;

export function map500mbWindVectors(snapshot: HourSnapshot | null): UpperAirVector[] {
  if (!snapshot) return [];
  const modelVectors = snapshot.upperAirVectors ?? [];
  if (coversFullMap(modelVectors)) return modelVectors;
  return buildFullMap500mbWindVectors(snapshot);
}

function coversFullMap(vectors: UpperAirVector[]): boolean {
  if (vectors.length < MIN_VECTOR_POINTS) return false;
  let minLon = Infinity;
  let maxLon = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;

  for (const vector of vectors) {
    if (!Number.isFinite(vector.lon) || !Number.isFinite(vector.lat)) continue;
    minLon = Math.min(minLon, vector.lon);
    maxLon = Math.max(maxLon, vector.lon);
    minLat = Math.min(minLat, vector.lat);
    maxLat = Math.max(maxLat, vector.lat);
  }

  return (maxLon - minLon) >= MIN_VECTOR_LON_SPAN && (maxLat - minLat) >= MIN_VECTOR_LAT_SPAN;
}

function buildFullMap500mbWindVectors(snapshot: HourSnapshot): UpperAirVector[] {
  const vectors: UpperAirVector[] = [];
  const region = snapshot.region;
  const ing = snapshot.ingredients;
  const shear = Math.max(18, Math.min(85, ing.shear06Kt));
  const motion = get500mbMotion(snapshot);
  const lonSpacing = 5.1;
  const latSpacing = 3.1;
  const sourceLonStart = -124 - motion.advectionLon - lonSpacing;
  const sourceLonEnd = -67 - motion.advectionLon + lonSpacing;
  const sourceLatStart = 25.5 - motion.advectionLat - latSpacing;
  const sourceLatEnd = 49.5 - motion.advectionLat + latSpacing;

  for (let lat = sourceLatStart; lat <= sourceLatEnd; lat += latSpacing) {
    for (let lon = sourceLonStart; lon <= sourceLonEnd; lon += lonSpacing) {
      const axis = get500mbAxisLat(snapshot, lon, motion);
      const distance = lat - axis;
      const jet = Math.exp(-(distance * distance) / 26);
      const downstream = Math.exp(-Math.pow((lon - (region.centerLon + 8)) / 22, 2));
      const base = 18 + shear * 0.32;
      const speedKt = Math.max(12, Math.min(118, base + 42 * jet + 18 * downstream * jet));
      const angleDeg = get500mbFlowAngleDeg(lon, lat, axis);
      const rad = (angleDeg * Math.PI) / 180;
      const plottedLon = lon + motion.advectionLon;
      const plottedLat = lat + motion.advectionLat;
      if (plottedLon < -126 || plottedLon > -65 || plottedLat < 22 || plottedLat > 52) continue;
      vectors.push({
        level: '500mb',
        lon: plottedLon,
        lat: plottedLat,
        uKt: speedKt * Math.cos(rad),
        vKt: speedKt * Math.sin(rad),
        speedKt,
      });
    }
  }

  return vectors;
}
