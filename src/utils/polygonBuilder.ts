// polygonBuilder: produces stepped risk-area rings around a region's
// center for map rendering. Used by mock + Open-Meteo providers; the
// Python backend can supply real contour polygons instead.
//
// Shapes use multi-harmonic organic contours with Chaikin smoothing
// so the rings look like SPC hand-drawn outlook areas, not simple
// ellipses. Each tier is slightly offset from center to mimic the
// non-concentric nesting seen in real SPC products.

import type { Region, RiskCategory, RiskPolygon } from '../types/forecast';
import { categoryRamp } from './outlookEngine';

interface BuildOpts {
  // semi-axis scaling per category (degrees of lat). Higher categories sit inside lower ones.
  baseLatRadius?: number;
  // ratio of lon to lat radius (account for cosine compression at higher latitudes)
  aspect?: number;
  // tilt in degrees, clockwise from east
  tiltDeg?: number;
  // number of points per ring
  resolution?: number;
}

// Multi-harmonic organic shape: perturbs a base ellipse with several
// sinusoidal harmonics so each ring looks irregular and natural.
function organicPoints(
  centerLat: number,
  centerLon: number,
  rLat: number,
  rLon: number,
  tiltDeg: number,
  n: number,
  seed: number,
  bbox?: [number, number, number, number],
): [number, number][] {
  const tilt = (tiltDeg * Math.PI) / 180;
  const cos = Math.cos(tilt);
  const sin = Math.sin(tilt);
  const out: [number, number][] = [];
  const minLon = bbox ? bbox[0] : -125;
  const minLat = bbox ? bbox[1] : 24;
  const maxLon = bbox ? bbox[2] : -66;
  const maxLat = bbox ? bbox[3] : 50;
  for (let i = 0; i < n; i++) {
    const t = (i / n) * Math.PI * 2;
    const wob =
      1 +
      0.13 * Math.sin(2 * t + seed * 0.7 + 0.9) +
      0.09 * Math.sin(3 * t + seed * 1.3 + 1.5) +
      0.05 * Math.sin(5 * t + seed * 0.4 + 0.6) +
      0.03 * Math.sin(7 * t + seed * 1.8 + 2.2);
    const ex = rLon * wob * Math.cos(t);
    const ey = rLat * wob * Math.sin(t);
    const lon = Math.max(minLon, Math.min(maxLon, centerLon + (ex * cos - ey * sin)));
    const lat = Math.max(minLat, Math.min(maxLat, centerLat + (ex * sin + ey * cos)));
    out.push([lon, lat]);
  }
  return out;
}

// Chaikin curve subdivision for smooth, organic contours.
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

export function buildRiskPolygons(
  region: Region,
  category: RiskCategory,
  opts: BuildOpts = {},
): RiskPolygon[] {
  const baseLat = opts.baseLatRadius ?? 2.4;
  const aspect  = opts.aspect ?? 2.2;
  const tilt    = opts.tiltDeg ?? -8;
  const n       = opts.resolution ?? 72;

  if (category === 'TSTM') {
    const rLat = baseLat * 2.2;
    const rLon = rLat * aspect;
    return [
      {
        category: 'TSTM',
        coords: chaikinSmooth(organicPoints(region.centerLat, region.centerLon, rLat, rLon, tilt, n, 0, region.bbox), 2),
      },
    ];
  }

  const ramp = categoryRamp(category);
  return ramp.map((cat, idx) => {
    const frac = ramp.length <= 1 ? 1 : (ramp.length - idx) / ramp.length;
    // Steep power curve: TSTM huge, inner tiers tight (matches real SPC)
    const rLat = Math.max(0.3, baseLat * 2.2 * Math.pow(frac, 1.55));
    // Inner tiers more elongated (narrow strips along forcing axis)
    const depth = ramp.length <= 1 ? 0 : idx / (ramp.length - 1);
    const tierAspect = aspect + depth * 1.2;
    const rLon = rLat * tierAspect;
    // Per-tier offset: higher tiers shift ENE (downshear)
    const offsetLon = depth * 1.0;
    const offsetLat = depth * -0.3;
    const cLat = region.centerLat + offsetLat;
    const cLon = region.centerLon + offsetLon;
    const outer = chaikinSmooth(organicPoints(cLat, cLon, rLat, rLon, tilt, n, idx * 1.3, region.bbox), 2);
    let hole: [number, number][] | undefined;
    if (idx < ramp.length - 1) {
      const innerFrac = ramp.length <= 1 ? 1 : (ramp.length - (idx + 1)) / ramp.length;
      const innerRLat = Math.max(0.3, baseLat * 2.2 * Math.pow(innerFrac, 1.55));
      const innerDepth = ramp.length <= 1 ? 0 : (idx + 1) / (ramp.length - 1);
      const innerAspect = aspect + innerDepth * 1.2;
      const innerRLon = innerRLat * innerAspect;
      const innerCLat = region.centerLat + innerDepth * -0.3;
      const innerCLon = region.centerLon + innerDepth * 1.0;
      hole = chaikinSmooth(organicPoints(innerCLat, innerCLon, innerRLat, innerRLon, tilt, n, (idx + 1) * 1.3, region.bbox), 2).reverse();
    }
    return { category: cat, coords: outer, hole };
  });
}
