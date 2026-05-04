import type { HourSnapshot, UpperAirLine } from '../types/forecast';

export interface UpperAirLineVisualStyle {
  stroke: string;
  strokeWidth: number;
  strokeOpacity: number;
  haloWidth: number;
  haloOpacity: number;
}

export interface UpperAirIntensitySegment {
  coords: [number, number][];
  stroke: string;
  strokeWidth: number;
  strokeOpacity: number;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export function upperAirLineVisualStyle(
  snapshot: HourSnapshot | null,
  idx: number,
  total: number,
): UpperAirLineVisualStyle {
  void snapshot;
  void idx;
  void total;

  return {
    stroke: '#7e858b',
    strokeWidth: 0.75,
    strokeOpacity: 0.34,
    haloWidth: 0,
    haloOpacity: 0,
  };
}

export function buildUpperAirIntensitySegments(
  snapshot: HourSnapshot | null,
  lines: UpperAirLine[],
): UpperAirIntensitySegment[] {
  if (!snapshot || lines.length === 0) return [];

  const shear = snapshot.ingredients.shear06Kt;
  const shearNorm = clamp01((shear - 28) / 34);
  if (shearNorm <= 0) return [];

  const region = snapshot.region;
  const focusLon = region.centerLon + 4.5 + Math.min(2.5, snapshot.forecastHour * 0.05);
  const focusLat = region.centerLat - 0.7 + 0.25 * Math.sin(snapshot.forecastHour * 0.16);
  const lonScale = 7.5 + shearNorm * 3.5;
  const latScale = 2.7 + shearNorm * 1.2;
  const threshold = 0.38;
  const segments: UpperAirIntensitySegment[] = [];

  for (const line of lines) {
    let current: [number, number][] = [];
    let scoreSum = 0;
    let scoreCount = 0;

    const flush = () => {
      if (current.length < 2 || scoreCount === 0) {
        current = [];
        scoreSum = 0;
        scoreCount = 0;
        return;
      }

      const meanScore = scoreSum / scoreCount;
      segments.push({
        coords: current,
        stroke: '#4f555b',
        strokeWidth: 0.9 + shearNorm * 0.65 + meanScore * 1.15,
        strokeOpacity: 0.44 + shearNorm * 0.16 + meanScore * 0.18,
      });
      current = [];
      scoreSum = 0;
      scoreCount = 0;
    };

    for (const coord of line.coords) {
      const [lon, lat] = coord;
      const dx = (lon - focusLon) / lonScale;
      const dy = (lat - focusLat) / latScale;
      const score = Math.exp(-(dx * dx + dy * dy));

      if (score >= threshold) {
        current.push(coord);
        scoreSum += score;
        scoreCount += 1;
      } else {
        flush();
      }
    }
    flush();
  }

  return segments
    .sort((a, b) => b.strokeWidth - a.strokeWidth)
    .slice(0, 18);
}
