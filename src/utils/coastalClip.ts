import type { RiskCategory } from '../types/forecast';

const CATEGORY_ORD: RiskCategory[] = ['TSTM', 'MRGL', 'SLGT', 'ENH', 'MOD', 'HIGH'];

export function isOrganizedSevereCategory(category: RiskCategory): boolean {
  return CATEGORY_ORD.indexOf(category) >= CATEGORY_ORD.indexOf('SLGT');
}

export function isOrganizedSevereThreshold(hazard: string, threshold: number): boolean {
  if (hazard === 'thunder') return false;
  if (hazard === 'tornado') return threshold >= 0.05;
  return threshold >= 0.15;
}

export function clipOrganizedSeverePoint(lon: number, lat: number): [number, number] {
  return clipToCoastalLand(lon, lat, 0.65);
}

export function clipOrganizedSevereCenter(lon: number, lat: number): [number, number] {
  return clipToCoastalLand(lon, lat, 1.05);
}

function clipToCoastalLand(lon: number, lat: number, inlandMargin: number): [number, number] {
  if (lon > 0) {
    return [lon, lat];
  }
  let clippedLon = Math.max(-125, Math.min(-66, lon));
  let clippedLat = Math.max(24, Math.min(50, lat));

  const gulf = gulfMinLandLat(clippedLon);
  if (gulf !== null && clippedLat < gulf + inlandMargin) clippedLat = gulf + inlandMargin;

  const atlantic = atlanticMaxLandLon(clippedLat);
  if (atlantic !== null && clippedLon > atlantic - inlandMargin) clippedLon = atlantic - inlandMargin;

  return [clippedLon, clippedLat];
}

export function clipOrganizedSeverePolygon(points: [number, number][]): [number, number][] {
  return points.map(([lon, lat]) => clipOrganizedSeverePoint(lon, lat));
}

function gulfMinLandLat(lon: number): number | null {
  if (lon < -98.5 || lon > -81.0) return null;
  const anchors: [number, number][] = [
    [-98.5, 26.7],
    [-96.0, 28.2],
    [-94.0, 29.2],
    [-91.0, 29.4],
    [-88.5, 30.0],
    [-86.0, 30.2],
    [-84.0, 29.9],
    [-82.0, 28.5],
    [-81.0, 27.0],
  ];
  return interpolateAnchors(lon, anchors);
}

function atlanticMaxLandLon(lat: number): number | null {
  if (lat < 25.0 || lat > 46.0) return null;
  const anchors: [number, number][] = [
    [25.0, -80.1],
    [27.0, -80.1],
    [29.0, -80.7],
    [30.5, -81.1],
    [32.0, -80.2],
    [34.0, -78.4],
    [36.0, -75.5],
    [38.0, -74.5],
    [40.0, -73.7],
    [42.0, -70.1],
    [44.0, -69.0],
    [46.0, -67.5],
  ];
  return interpolateAnchors(lat, anchors);
}

function interpolateAnchors(x: number, anchors: [number, number][]): number {
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
