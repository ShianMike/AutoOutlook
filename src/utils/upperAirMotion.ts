import type { HourSnapshot } from '../types/forecast';

export interface UpperAirMotion {
  phase: number;
  advectionLon: number;
  advectionLat: number;
}

export function get500mbMotion(snapshot: HourSnapshot): UpperAirMotion {
  return {
    phase: snapshot.forecastHour * 0.18,
    advectionLon: snapshot.forecastHour * 0.28,
    advectionLat: 0.45 * Math.sin(snapshot.forecastHour * 0.16),
  };
}

export function get500mbAxisLat(snapshot: HourSnapshot, sourceLon: number, motion: UpperAirMotion): number {
  return snapshot.region.centerLat - 2.3 + 0.10 * (sourceLon - snapshot.region.centerLon) +
    1.8 * Math.sin((sourceLon + 99) / 9 + motion.phase);
}

export function get500mbFlowAngleDeg(sourceLon: number, sourceLat: number, axisLat: number): number {
  const distance = sourceLat - axisLat;
  return -8 + 13 * Math.sin((sourceLon + 90) / 14) - 10 * Math.tanh(distance / 5);
}
