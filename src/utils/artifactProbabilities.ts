import type { RiskCategory } from '../types/forecast';
import type {
  OutlookArtifactFeatureCollection,
  ArtifactRiskCategory,
  OutlookArtifacts,
  OutlookProbabilityTile,
} from '../types/outlookArtifacts';

export type ArtifactHazardKey = 'tornado' | 'hail' | 'wind';
export type GeneratedArtifactHazardKey = ArtifactHazardKey | 'thunder';

export interface ArtifactProbabilityFeature {
  type: 'Feature';
  properties: {
    hazard: GeneratedArtifactHazardKey;
    probability: number;
    bucket: number;
    label: string;
    color: string;
  };
  geometry: { type: 'Polygon'; coordinates: number[][][] };
}

export interface ArtifactProbabilityFeatureCollection {
  type: 'FeatureCollection';
  features: ArtifactProbabilityFeature[];
}

export interface ArtifactHazardPeakLocation {
  probability: number;
  lat: number;
  lon: number;
  row: number;
  col: number;
}

const TORNADO_THRESHOLDS = [0.02, 0.05, 0.10, 0.15, 0.30];
const SEVERE_THRESHOLDS = [0.05, 0.15, 0.30, 0.45, 0.60];
const TORNADO_LABELS = ['2%', '5%', '10%', '15%', '30%'];
const SEVERE_LABELS = ['5%', '15%', '30%', '45%', '60%'];
const THUNDER_THRESHOLDS = [0.10, 0.40, 0.70];
const THUNDER_LABELS = ['10%', '40%', '70%'];
const TORNADO_COLORS = ['#3b9b3b', '#a87d4f', '#d4ad7c', '#cf2727', '#c43eb1'];
const SEVERE_COLORS = ['#a87d4f', '#f6c842', '#cf2727', '#c43eb1', '#6e0099'];
const THUNDER_COLORS = ['#c9a279', '#5cdde6', '#ef6055'];

export function getArtifactHourTile(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
): OutlookProbabilityTile | undefined {
  if (forecastHour === undefined) return undefined;
  return artifacts?.probabilityTiles?.hours.find((hour) => hour.forecastHour === forecastHour)?.tile;
}

export function getArtifactHazardGrid(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
  hazard: ArtifactHazardKey,
): number[][] | undefined {
  return getArtifactHourTile(artifacts, forecastHour)?.probabilities[hazard];
}

export function getArtifactHazardPeak(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
  hazard: ArtifactHazardKey,
): number | undefined {
  return getArtifactHazardPeakLocation(artifacts, forecastHour, hazard)?.probability;
}

export function getArtifactHazardPeakLocation(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
  hazard: ArtifactHazardKey,
): ArtifactHazardPeakLocation | undefined {
  const tile = getArtifactHourTile(artifacts, forecastHour);
  const grid = tile?.probabilities[hazard];
  if (!tile || !grid) return undefined;
  let best: ArtifactHazardPeakLocation | undefined;
  grid.forEach((row, rowIndex) => row.forEach((value, colIndex) => {
    const probability = Number(value);
    const lat = Number(tile.lats[rowIndex]?.[colIndex]);
    const lon = Number(tile.lons[rowIndex]?.[colIndex]);
    if (!Number.isFinite(probability) || !Number.isFinite(lat) || !Number.isFinite(lon)) return;
    if (!best || probability > best.probability) {
      best = { probability, lat, lon, row: rowIndex, col: colIndex };
    }
  }));
  return best;
}

export function getArtifactHazardLevel(hazard: ArtifactHazardKey, probability: number): RiskCategory {
  const thresholds = hazardThresholds(hazard);
  if (probability >= thresholds[4]) return 'HIGH';
  if (probability >= thresholds[3]) return 'MOD';
  if (probability >= thresholds[2]) return 'ENH';
  if (probability >= thresholds[1]) return 'SLGT';
  if (probability >= thresholds[0]) return 'MRGL';
  return 'TSTM';
}

export function artifactProbabilityToFeatureCollection(
  tile: OutlookProbabilityTile | undefined,
  hazard: ArtifactHazardKey,
): ArtifactProbabilityFeatureCollection {
  if (!tile) return { type: 'FeatureCollection', features: [] };
  const grid = tile.probabilities[hazard];
  const thresholds = hazardThresholds(hazard);
  const labels = hazardLabels(hazard);
  const colors = hazardColors(hazard);
  const features: ArtifactProbabilityFeature[] = [];

  for (let row = 0; row < grid.length; row += 1) {
    for (let col = 0; col < grid[row].length; col += 1) {
      const probability = Number(grid[row][col]);
      const bucket = probabilityBucket(probability, thresholds);
      if (bucket < 0) continue;
      const lat = Number(tile.lats[row]?.[col]);
      const lon = Number(tile.lons[row]?.[col]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const ring = cellRing(tile, row, col);
      if (ring.length < 4) continue;
      features.push({
        type: 'Feature',
        properties: {
          hazard,
          probability,
          bucket,
          label: labels[bucket],
          color: colors[bucket],
        },
        geometry: { type: 'Polygon', coordinates: [ring] },
      });
    }
  }
  return { type: 'FeatureCollection', features };
}

export function artifactThunderToFeatureCollection(
  tile: OutlookProbabilityTile | undefined,
): ArtifactProbabilityFeatureCollection {
  if (!tile) return { type: 'FeatureCollection', features: [] };
  const features: ArtifactProbabilityFeature[] = [];

  for (let row = 0; row < tile.categoryOrdinal.length; row += 1) {
    for (let col = 0; col < tile.categoryOrdinal[row].length; col += 1) {
      const ordinal = Number(tile.categoryOrdinal[row][col]);
      if (!Number.isFinite(ordinal) || ordinal < 1) continue;
      const lat = Number(tile.lats[row]?.[col]);
      const lon = Number(tile.lons[row]?.[col]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const ring = cellRing(tile, row, col);
      if (ring.length < 4) continue;
      const bucket = thunderBucketFromOrdinal(ordinal);
      features.push({
        type: 'Feature',
        properties: {
          hazard: 'thunder',
          probability: THUNDER_THRESHOLDS[bucket],
          bucket,
          label: THUNDER_LABELS[bucket],
          color: THUNDER_COLORS[bucket],
        },
        geometry: { type: 'Polygon', coordinates: [ring] },
      });
    }
  }
  return { type: 'FeatureCollection', features };
}

export function getArtifactThunderCoverage(tile: OutlookProbabilityTile | undefined): number | undefined {
  if (!tile) return undefined;
  let validCells = 0;
  let thunderCells = 0;
  tile.categoryOrdinal.forEach((row, rowIndex) => row.forEach((value, colIndex) => {
    const ordinal = Number(value);
    const lat = Number(tile.lats[rowIndex]?.[colIndex]);
    const lon = Number(tile.lons[rowIndex]?.[colIndex]);
    if (!Number.isFinite(ordinal) || !Number.isFinite(lat) || !Number.isFinite(lon)) return;
    validCells += 1;
    if (ordinal >= 1) thunderCells += 1;
  }));
  return validCells > 0 ? thunderCells / validCells : 0;
}

export function artifactRiskToFeatureCollection(
  tile: OutlookProbabilityTile | undefined,
): OutlookArtifactFeatureCollection {
  if (!tile) return { type: 'FeatureCollection', features: [] };
  const features: OutlookArtifactFeatureCollection['features'] = [];
  for (let row = 0; row < tile.categoryOrdinal.length; row += 1) {
    for (let col = 0; col < tile.categoryOrdinal[row].length; col += 1) {
      const ordinal = Number(tile.categoryOrdinal[row][col]);
      const category = tile.categoryLabel[row]?.[col] ?? 'NONE';
      if (!Number.isFinite(ordinal) || ordinal <= 0 || category === 'NONE') continue;
      const lat = Number(tile.lats[row]?.[col]);
      const lon = Number(tile.lons[row]?.[col]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const ring = cellRing(tile, row, col);
      if (ring.length < 4) continue;
      features.push({
        type: 'Feature',
        geometry: { type: 'Polygon', coordinates: [ring] },
        properties: {
          category,
          ordinal,
          forecastHour: tile.forecastHour,
          validTimeISO: tile.validTimeISO,
        },
      });
    }
  }
  return { type: 'FeatureCollection', features };
}

export function getArtifactMaxCategory(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
): ArtifactRiskCategory | undefined {
  const tile = getArtifactHourTile(artifacts, forecastHour);
  if (!tile) return undefined;
  let maxOrdinal = 0;
  tile.categoryOrdinal.forEach((row) => row.forEach((value) => {
    if (Number.isFinite(value)) maxOrdinal = Math.max(maxOrdinal, Number(value));
  }));
  return (['NONE', 'TSTM', 'MRGL', 'SLGT', 'ENH', 'MDT', 'HIGH'][maxOrdinal] ?? 'NONE') as ArtifactRiskCategory;
}

export function getArtifactRiskPolygonMaxCategory(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
): ArtifactRiskCategory | undefined {
  if (forecastHour === undefined) return undefined;
  const features = artifacts?.riskPolygons.features ?? [];
  let best: ArtifactRiskCategory | undefined;
  features.forEach((feature) => {
    if (feature.properties.forecastHour !== forecastHour) return;
    const category = feature.properties.category;
    if (category === 'NONE') return;
    if (!best || categoryOrdinal(category) > categoryOrdinal(best)) {
      best = category;
    }
  });
  return best;
}

export function getArtifactMainHazard(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
): ArtifactHazardKey | undefined {
  const peaks = (['tornado', 'hail', 'wind'] as ArtifactHazardKey[])
    .map((hazard) => ({ hazard, peak: getArtifactHazardPeak(artifacts, forecastHour, hazard) ?? 0 }))
    .sort((a, b) => b.peak - a.peak);
  return peaks[0]?.peak > 0 ? peaks[0].hazard : undefined;
}

function hazardThresholds(hazard: ArtifactHazardKey): number[] {
  return hazard === 'tornado' ? TORNADO_THRESHOLDS : SEVERE_THRESHOLDS;
}

function hazardLabels(hazard: ArtifactHazardKey): string[] {
  return hazard === 'tornado' ? TORNADO_LABELS : SEVERE_LABELS;
}

function hazardColors(hazard: ArtifactHazardKey): string[] {
  return hazard === 'tornado' ? TORNADO_COLORS : SEVERE_COLORS;
}

function probabilityBucket(probability: number, thresholds: number[]): number {
  let bucket = -1;
  thresholds.forEach((threshold, idx) => {
    if (probability >= threshold) bucket = idx;
  });
  return bucket;
}

function thunderBucketFromOrdinal(ordinal: number): number {
  if (ordinal >= 4) return 2;
  if (ordinal >= 2) return 1;
  return 0;
}

function categoryOrdinal(category: ArtifactRiskCategory): number {
  if (category === 'NONE') return 0;
  if (category === 'TSTM') return 1;
  if (category === 'MRGL') return 2;
  if (category === 'SLGT') return 3;
  if (category === 'ENH') return 4;
  if (category === 'MDT' || category === 'MOD') return 5;
  return 6;
}

function cellRing(tile: OutlookProbabilityTile, row: number, col: number): number[][] {
  const lat = Number(tile.lats[row]?.[col]);
  const lon = Number(tile.lons[row]?.[col]);
  const prevLon = Number(tile.lons[row]?.[Math.max(0, col - 1)]);
  const nextLon = Number(tile.lons[row]?.[Math.min(tile.lons[row].length - 1, col + 1)]);
  const prevLat = Number(tile.lats[Math.max(0, row - 1)]?.[col]);
  const nextLat = Number(tile.lats[Math.min(tile.lats.length - 1, row + 1)]?.[col]);
  const dx = Math.max(0.05, Math.abs((Number.isFinite(nextLon) ? nextLon : lon) - (Number.isFinite(prevLon) ? prevLon : lon)) / 2);
  const dy = Math.max(0.05, Math.abs((Number.isFinite(nextLat) ? nextLat : lat) - (Number.isFinite(prevLat) ? prevLat : lat)) / 2);
  const minLon = lon - dx / 2;
  const maxLon = lon + dx / 2;
  const minLat = lat - dy / 2;
  const maxLat = lat + dy / 2;
  return [
    [minLon, minLat],
    [minLon, maxLat],
    [maxLon, maxLat],
    [maxLon, minLat],
    [minLon, minLat],
  ].map(([x, y]) => [roundCoord(x), roundCoord(y)]);
}

function roundCoord(value: number): number {
  return Math.round(value * 10000) / 10000;
}
