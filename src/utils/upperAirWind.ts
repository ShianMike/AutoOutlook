import type { HourSnapshot, UpperAirVector } from '../types/forecast';

const CONUS_LON_MIN = -130;
const CONUS_LON_MAX = -60;
const CONUS_LAT_MIN = 20;
const CONUS_LAT_MAX = 55;

export function map500mbWindVectors(snapshot: HourSnapshot | null): UpperAirVector[] {
  if (!snapshot) return [];
  if (snapshot.upperAirOverlay?.domain !== 'CONUS' || snapshot.upperAirOverlay.level !== '500mb') return [];
  if (!snapshot.upperAirOverlay.hasWindVectors) return [];
  const modelVectors = snapshot.upperAirVectors;
  if (!Array.isArray(modelVectors) || modelVectors.length === 0) return [];
  return modelVectors.filter(isValid500mbVector);
}

function isValid500mbVector(vector: UpperAirVector): boolean {
  return vector.level === '500mb' &&
    Number.isFinite(vector.lon) &&
    Number.isFinite(vector.lat) &&
    Number.isFinite(vector.uKt) &&
    Number.isFinite(vector.vKt) &&
    Number.isFinite(vector.speedKt) &&
    vector.speedKt >= 0 &&
    vector.lon >= CONUS_LON_MIN &&
    vector.lon <= CONUS_LON_MAX &&
    vector.lat >= CONUS_LAT_MIN &&
    vector.lat <= CONUS_LAT_MAX;
}
