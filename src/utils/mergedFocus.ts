// Derives a focus region for the MERGED (multi-cycle Day 1) outlook from the
// merged artifact itself, rather than reusing the hourly scrubber snapshot's
// region. In merged mode the selected forecast hour's region (e.g. Southeast)
// has nothing to do with where the merged Day 1 risk actually maxes out, so the
// banner/discussion would otherwise mislabel an Illinois ENH area as "Southeast".
//
// The peak location is taken from the merged tile's main-hazard probability
// peak (falling back to the centroid of the highest categorical cells), then
// snapped to the nearest well-known severe-weather region for a readable label.

import type { Region } from '../types/forecast';
import type { OutlookArtifacts } from '../types/outlookArtifacts';
import {
  getArtifactHourTile,
  getArtifactMainHazard,
  getArtifactHazardPeakLocation,
} from './artifactProbabilities';

interface NamedRegion {
  name: string;
  lat: number;
  lon: number;
  states: string[];
}

// Coarse CONUS region table. The first five mirror the labels the hourly
// provider already uses (see openMeteoProvider CONUS_SAMPLE_POINTS); the rest
// add coverage so far-flung peaks still snap to a sensible name.
const NAMED_REGIONS: NamedRegion[] = [
  { name: 'Central Plains', lat: 36.0, lon: -98.0, states: ['OK', 'KS', 'TX'] },
  { name: 'Mid-South', lat: 35.0, lon: -90.0, states: ['AR', 'TN', 'MS'] },
  { name: 'Southern Plains', lat: 32.5, lon: -98.0, states: ['TX', 'LA'] },
  { name: 'Midwest', lat: 41.5, lon: -89.0, states: ['IL', 'IA', 'MO'] },
  { name: 'Southeast', lat: 33.0, lon: -85.0, states: ['AL', 'GA', 'FL'] },
  { name: 'Northern Plains', lat: 44.5, lon: -100.0, states: ['SD', 'ND', 'NE'] },
  { name: 'Ohio Valley', lat: 39.0, lon: -85.0, states: ['IN', 'OH', 'KY'] },
  { name: 'Northeast', lat: 42.0, lon: -75.0, states: ['NY', 'PA', 'NJ'] },
  { name: 'High Plains', lat: 39.0, lon: -103.0, states: ['CO', 'KS', 'NE'] },
  { name: 'Gulf Coast', lat: 30.0, lon: -92.0, states: ['LA', 'TX', 'MS'] },
  { name: 'Southwest', lat: 33.5, lon: -106.0, states: ['NM', 'AZ'] },
  { name: 'Northwest', lat: 45.0, lon: -118.0, states: ['OR', 'WA', 'ID'] },
];

const MERGED_TILE_HOUR = 0;

// Categorical ranking used to pick the highest-risk polygon for the focus
// centroid fallback (works in production, where the merged probability tile
// grids are stripped from the static export but the risk polygons are kept).
const CATEGORY_RANK: Record<string, number> = {
  NONE: 0, TSTM: 1, MRGL: 2, SLGT: 3, ENH: 4, MDT: 5, MOD: 5, HIGH: 6,
};

function forEachPosition(
  geometry: { type: string; coordinates: unknown } | null | undefined,
  cb: (lon: number, lat: number) => void,
): void {
  if (!geometry) return;
  if (geometry.type === 'Polygon') {
    (geometry.coordinates as number[][][]).forEach((ring) =>
      ring.forEach((pos) => cb(Number(pos[0]), Number(pos[1]))));
  } else if (geometry.type === 'MultiPolygon') {
    (geometry.coordinates as number[][][][]).forEach((poly) =>
      poly.forEach((ring) => ring.forEach((pos) => cb(Number(pos[0]), Number(pos[1])))));
  }
}

function peakFromRiskPolygons(artifacts: OutlookArtifacts): { lat: number; lon: number } | null {
  const fc = artifacts.riskPolygons;
  if (!fc || !Array.isArray(fc.features) || fc.features.length === 0) return null;

  let bestRank = 0;
  let bestFeatures: typeof fc.features = [];
  for (const feature of fc.features) {
    const rank = CATEGORY_RANK[String(feature.properties?.category ?? 'NONE')] ?? 0;
    if (rank > bestRank) {
      bestRank = rank;
      bestFeatures = [feature];
    } else if (rank === bestRank && rank > 0) {
      bestFeatures.push(feature);
    }
  }
  if (bestRank <= 0 || bestFeatures.length === 0) return null;

  let sumLat = 0;
  let sumLon = 0;
  let count = 0;
  for (const feature of bestFeatures) {
    forEachPosition(feature.geometry, (lon, lat) => {
      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        sumLat += lat;
        sumLon += lon;
        count += 1;
      }
    });
  }
  if (count === 0) return null;
  return { lat: sumLat / count, lon: sumLon / count };
}

function nearestRegion(lat: number, lon: number): NamedRegion {
  let best = NAMED_REGIONS[0];
  let bestDistance = Number.POSITIVE_INFINITY;
  const lonScale = Math.cos((lat * Math.PI) / 180);
  for (const region of NAMED_REGIONS) {
    const dLat = lat - region.lat;
    const dLon = (lon - region.lon) * lonScale;
    const distance = dLat * dLat + dLon * dLon;
    if (distance < bestDistance) {
      bestDistance = distance;
      best = region;
    }
  }
  return best;
}

function peakLatLon(artifacts: OutlookArtifacts): { lat: number; lon: number } | null {
  // 1. Precise: main-hazard probability peak from the merged tile (dev / live
  //    backend, where the full probability grids are present).
  const mainHazard = getArtifactMainHazard(artifacts, MERGED_TILE_HOUR);
  if (mainHazard) {
    const peak = getArtifactHazardPeakLocation(artifacts, MERGED_TILE_HOUR, mainHazard);
    if (peak && Number.isFinite(peak.lat) && Number.isFinite(peak.lon)) {
      return { lat: peak.lat, lon: peak.lon };
    }
  }

  // 2. Production-safe: centroid of the highest-category risk polygon. The
  //    static API export strips the tile's probability/category grids but
  //    keeps the risk polygons, so this path keeps the focus correct in prod.
  const polygonPeak = peakFromRiskPolygons(artifacts);
  if (polygonPeak) return polygonPeak;

  // 3. Last resort: centroid of the highest categorical cells in the merged tile.
  const tile = getArtifactHourTile(artifacts, MERGED_TILE_HOUR);
  if (!tile || !Array.isArray(tile.categoryOrdinal) || tile.categoryOrdinal.length === 0) return null;

  let maxOrdinal = 0;
  tile.categoryOrdinal.forEach((row) => row.forEach((value) => {
    if (Number.isFinite(value)) maxOrdinal = Math.max(maxOrdinal, Number(value));
  }));
  if (maxOrdinal <= 0) return null;

  let sumLat = 0;
  let sumLon = 0;
  let count = 0;
  tile.categoryOrdinal.forEach((row, r) => row.forEach((value, c) => {
    if (Number(value) !== maxOrdinal) return;
    const lat = Number(tile.lats[r]?.[c]);
    const lon = Number(tile.lons[r]?.[c]);
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      sumLat += lat;
      sumLon += lon;
      count += 1;
    }
  }));
  if (count === 0) return null;
  return { lat: sumLat / count, lon: sumLon / count };
}

/**
 * Build a synthetic Region centered on the merged outlook's peak risk, labeled
 * with the nearest named severe-weather region. Returns null when no merged
 * risk is available (so callers can fall back to the hourly snapshot region).
 */
export function mergedRegionFromArtifacts(artifacts: OutlookArtifacts | null): Region | null {
  if (!artifacts) return null;
  const peak = peakLatLon(artifacts);
  if (!peak) return null;
  const region = nearestRegion(peak.lat, peak.lon);
  return {
    label: region.name,
    centerLat: peak.lat,
    centerLon: peak.lon,
    bbox: [peak.lon - 5, peak.lat - 3, peak.lon + 5, peak.lat + 3],
    states: region.states,
  };
}
