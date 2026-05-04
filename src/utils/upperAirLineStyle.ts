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
  void snapshot;
  void lines;
  // TODO: add real deep-layer shear contours from backend U500/V500 minus U10/V10.
  return [];
}
