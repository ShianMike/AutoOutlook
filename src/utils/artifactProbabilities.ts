import type { RiskCategory } from '../types/forecast';
import type {
  OutlookArtifactFeatureCollection,
  ArtifactRiskCategory,
  OutlookArtifacts,
  OutlookProbabilityShapeFeatureCollection,
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
  geometry: { type: 'Polygon' | 'MultiPolygon'; coordinates: number[][][] | number[][][][] };
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

const TORNADO_THRESHOLDS = [0.02, 0.05, 0.10, 0.15, 0.30, 0.45, 0.60];
const SEVERE_THRESHOLDS = [0.05, 0.15, 0.30, 0.45, 0.60];
const TORNADO_LABELS = ['2%', '5%', '10%', '15%', '30%', '45%', '60%'];
const SEVERE_LABELS = ['5%', '15%', '30%', '45%', '60%'];
const THUNDER_THRESHOLDS = [0.10, 0.40, 0.70];
const THUNDER_LABELS = ['10%', '40%', '70%'];
const TORNADO_COLORS = ['#3b9b3b', '#a87d4f', '#d4ad7c', '#cf2727', '#c43eb1', '#6e0099', '#4b006b'];
const SEVERE_COLORS = ['#a87d4f', '#f6c842', '#cf2727', '#c43eb1', '#6e0099'];
const THUNDER_COLORS = ['#c9a279', '#5cdde6', '#ef6055'];

export function getArtifactHourTile(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
): OutlookProbabilityTile | undefined {
  if (forecastHour === undefined) return undefined;
  return artifacts?.probabilityTiles?.hours.find((hour) => hour.forecastHour === forecastHour)?.tile;
}

export function artifactRiskShapesToFeatureCollection(
  tile: OutlookProbabilityTile | undefined,
): OutlookArtifactFeatureCollection | undefined {
  const collection = tile?.riskShapes;
  if (!collection) return undefined;
  return {
    ...collection,
    features: [...collection.features].sort((a, b) => categoryOrdinal(a.properties.category) - categoryOrdinal(b.properties.category)),
  };
}

export function artifactProbabilityShapesToFeatureCollection(
  tile: OutlookProbabilityTile | undefined,
  hazard: GeneratedArtifactHazardKey,
): ArtifactProbabilityFeatureCollection | undefined {
  const collection: OutlookProbabilityShapeFeatureCollection | undefined = tile?.hazardProbabilityShapes;
  if (!collection) return undefined;
  const features = collection.features
    .filter((feature) => normalizeHazardName(feature.properties.hazard) === hazard)
    .map((feature): ArtifactProbabilityFeature => ({
      type: 'Feature',
      geometry: normalizeArtifactGeometry(feature.geometry),
      properties: {
        hazard,
        probability: Number(feature.properties.probability),
        bucket: Number(feature.properties.bucket),
        label: feature.properties.label,
        color: feature.properties.color,
      },
    }))
    .sort((a, b) => a.properties.bucket - b.properties.bucket);
  return { type: 'FeatureCollection', features };
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

/**
 * Measure the radius of the largest circle around the peak cell that's
 * entirely INSIDE the given probability threshold's region in the
 * artifact probability grid.
 *
 * Used by the artifact-driven SIG path to clamp the SIG polygon so it
 * never extends beyond the 5% (brown band) boundary of the actual model
 * output. The formula-based clamp in `hazardProbabilityBands.ts` assumes
 * an idealized elliptical band geometry, but the artifact's real
 * probability region can be much smaller or asymmetric — this function
 * gives the actual measured extent.
 *
 * Returns the minimum distance (in lat/lon degrees) from the peak cell
 * to any cell where probability < threshold. Returns Infinity if the
 * grid is unavailable or no sub-threshold cell is found (effectively no
 * containment needed). Returns 0 if the peak cell itself is below threshold.
 *
 * Note: this is intentionally CONSERVATIVE — it uses the WORST direction
 * (closest sub-threshold cell in any compass direction), so the SIG is
 * guaranteed to fit inside the band even if the band is asymmetric.
 */
export function measureArtifactBandRadius(
  tile: OutlookProbabilityTile | undefined,
  hazard: ArtifactHazardKey,
  peakLat: number,
  peakLon: number,
  threshold: number,
): number {
  if (!tile) return Infinity;
  const grid = tile.probabilities[hazard];
  if (!grid) return Infinity;
  let minDistToOutside = Infinity;
  for (let r = 0; r < grid.length; r += 1) {
    for (let c = 0; c < grid[r].length; c += 1) {
      const prob = Number(grid[r][c]);
      if (!Number.isFinite(prob) || prob >= threshold) continue;
      const lat = Number(tile.lats[r]?.[c]);
      const lon = Number(tile.lons[r]?.[c]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const d = Math.hypot(lat - peakLat, lon - peakLon);
      if (d < minDistToOutside) minDistToOutside = d;
    }
  }
  return minDistToOutside;
}

export function getArtifactHazardLevel(hazard: ArtifactHazardKey, probability: number): RiskCategory {
  if (hazard === 'tornado') {
    if (probability >= 0.45) return 'HIGH';
    if (probability >= 0.30) return 'MOD';
    if (probability >= 0.10) return 'ENH';
    if (probability >= 0.05) return 'SLGT';
    if (probability >= 0.02) return 'MRGL';
    return 'TSTM';
  }
  if (probability >= 0.60) return 'MOD';
  if (probability >= 0.30) return 'ENH';
  if (probability >= 0.15) return 'SLGT';
  if (probability >= 0.05) return 'MRGL';
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

  const thresholds = THUNDER_THRESHOLDS;
  const labels = THUNDER_LABELS;

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
          probability: thresholds[bucket] ?? THUNDER_THRESHOLDS[bucket],
          bucket,
          label: labels[bucket] ?? THUNDER_LABELS[bucket],
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

function normalizeHazardName(hazard: string): GeneratedArtifactHazardKey {
  return hazard === 'thunderstorm' ? 'thunder' : hazard as GeneratedArtifactHazardKey;
}

function normalizeArtifactGeometry(
  geometry: OutlookProbabilityShapeFeatureCollection['features'][number]['geometry'],
): ArtifactProbabilityFeature['geometry'] {
  if (geometry.type === 'Polygon') {
    return {
      ...geometry,
      coordinates: normalizePolygonRings(geometry.coordinates as number[][][]),
    };
  }
  return {
    ...geometry,
    coordinates: (geometry.coordinates as number[][][][]).map((polygon) => normalizePolygonRings(polygon)),
  };
}

function normalizePolygonRings(rings: number[][][]): number[][][] {
  if (!Array.isArray(rings) || rings.length === 0) return rings;
  return rings.map((ring, index) => (index === 0 ? normalizeExteriorRing(ring) : normalizeInteriorRing(ring)));
}

function normalizeExteriorRing(ring: number[][]): number[][] {
  if (!Array.isArray(ring) || ring.length < 4) return ring;
  const open = samePoint(ring[0], ring[ring.length - 1]) ? ring.slice(0, -1) : [...ring];
  if (signedRingArea(open) > 0) open.reverse();
  return samePoint(open[0], open[open.length - 1]) ? open : [...open, open[0]];
}

function normalizeInteriorRing(ring: number[][]): number[][] {
  if (!Array.isArray(ring) || ring.length < 4) return ring;
  const open = samePoint(ring[0], ring[ring.length - 1]) ? ring.slice(0, -1) : [...ring];
  if (signedRingArea(open) < 0) open.reverse();
  return samePoint(open[0], open[open.length - 1]) ? open : [...open, open[0]];
}

function samePoint(a: number[] | undefined, b: number[] | undefined): boolean {
  return Boolean(a && b && a.length >= 2 && b.length >= 2 && a[0] === b[0] && a[1] === b[1]);
}

function signedRingArea(coords: number[][]): number {
  let area = 0;
  for (let i = 0; i < coords.length; i += 1) {
    const [x0, y0] = coords[i];
    const [x1, y1] = coords[(i + 1) % coords.length];
    area += x0 * y1 - x1 * y0;
  }
  return area / 2;
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
