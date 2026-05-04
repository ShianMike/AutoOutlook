import type { HourSnapshot, UpperAirLine } from '../types/forecast';

const CONUS_LON_MIN = -130;
const CONUS_LON_MAX = -60;
const CONUS_LAT_MIN = 20;
const CONUS_LAT_MAX = 55;

export function map500mbLines(snapshot: HourSnapshot | null): UpperAirLine[] {
  if (!snapshot) return [];
  if (snapshot.upperAirOverlay?.domain !== 'CONUS' || snapshot.upperAirOverlay.level !== '500mb') return [];
  if (!snapshot.upperAirOverlay.hasHeightContours) return [];
  const modelLines = snapshot.upperAirLines;
  if (!Array.isArray(modelLines) || modelLines.length === 0) return [];
  return modelLines
    .map(sanitize500mbLine)
    .filter((line): line is UpperAirLine => line !== null)
    .sort((a, b) => a.value - b.value);
}

function sanitize500mbLine(line: UpperAirLine): UpperAirLine | null {
  if (line.level !== '500mb' || !Number.isFinite(line.value) || !Array.isArray(line.coords)) {
    return null;
  }

  const coords = line.coords.filter(isConusCoord);
  if (coords.length < 2) return null;

  return { level: '500mb', value: line.value, coords };
}

function isConusCoord(coord: [number, number]): boolean {
  const [lon, lat] = coord;
  return Number.isFinite(lon) &&
    Number.isFinite(lat) &&
    lon >= CONUS_LON_MIN &&
    lon <= CONUS_LON_MAX &&
    lat >= CONUS_LAT_MIN &&
    lat <= CONUS_LAT_MAX;
}
