import { geoArea, type GeoPermissibleObjects } from 'd3-geo';
import type { RiskCategory, Ingredients, HazardKey, HourSnapshot } from '../types/forecast';
import type { ArtifactStatus } from '../hooks/useOutlookArtifacts';
import { lvlFromProb } from './hazardEngine';
import type {
  OutlookArtifactFeatureCollection,
  ArtifactRiskCategory,
  OutlookArtifacts,
  OutlookCigShapeFeatureCollection,
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

export interface ArtifactCigFeature {
  type: 'Feature';
  properties: {
    hazard: ArtifactHazardKey;
    cig: number;
    label: string;
    hatchPattern: string;
    sourceCellCount?: number;
    displayAreaKm2?: number;
    hatchGeometry?: ArtifactProbabilityFeature['geometry'];
  };
  geometry: { type: 'Polygon' | 'MultiPolygon'; coordinates: number[][][] | number[][][][] };
}

export interface ArtifactCigFeatureCollection {
  type: 'FeatureCollection';
  features: ArtifactCigFeature[];
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
const THUNDER_THRESHOLDS = [0.01, 0.10, 0.40, 0.70];
const THUNDER_LABELS = ['TSTM', '10%', '40%', '70%'];
const TORNADO_COLORS = ['#3b9b3b', '#a87d4f', '#d4ad7c', '#cf2727', '#c43eb1', '#6e0099', '#4b006b'];
const SEVERE_COLORS = ['#a87d4f', '#f6c842', '#cf2727', '#c43eb1', '#6e0099'];
const THUNDER_COLORS = ['#5baa58', '#c9a279', '#5cdde6', '#ef6055'];

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
  if (!collection || !Array.isArray(collection.features)) return undefined;
  const trainedThunderTstm = trainedThunderTstmFeature(tile);
  const hasTrainedThunderShapes = Array.isArray(tile?.hazardProbabilityShapes?.features);
  const features = collection.features
    .filter((feature) => feature.properties.category !== 'TSTM' || !hasTrainedThunderShapes || trainedThunderTstm)
    .flatMap((feature) => {
      const sourceGeometry = feature.properties.category === 'TSTM' && trainedThunderTstm
        ? trainedThunderTstm.geometry
        : feature.geometry;
      const geometry = normalizeArtifactGeometry(sourceGeometry);
      if (!geometry) return [];
      if (feature.properties.category !== 'TSTM' || !trainedThunderTstm) {
        return [{ ...feature, geometry }];
      }
      return [{
        ...feature,
        geometry,
        properties: {
          ...feature.properties,
          cellCount: trainedThunderTstm.properties.cellCount,
          sourceCellCount: trainedThunderTstm.properties.sourceCellCount,
          componentCount: trainedThunderTstm.properties.componentCount,
          displayAreaKm2: trainedThunderTstm.properties.displayAreaKm2,
          vectorization: {
            ...(feature.properties.vectorization ?? {}),
            supportSource: 'trained_thunder_probability',
            trainedThunderBucket: trainedThunderTstm.properties.bucket,
            trainedThunderLabel: trainedThunderTstm.properties.label,
          },
        },
      }];
    });
  return {
    ...collection,
    features: features.sort((a, b) => categoryOrdinal(a.properties.category) - categoryOrdinal(b.properties.category)),
  };
}

export function artifactProbabilityShapesToFeatureCollection(
  tile: OutlookProbabilityTile | undefined,
  hazard: GeneratedArtifactHazardKey,
): ArtifactProbabilityFeatureCollection | undefined {
  const collection: OutlookProbabilityShapeFeatureCollection | undefined = tile?.hazardProbabilityShapes;
  if (!collection || !Array.isArray(collection.features)) return undefined;
  const features = collection.features
    .filter((feature) => normalizeHazardName(feature.properties.hazard) === hazard)
    .flatMap((feature): ArtifactProbabilityFeature[] => {
      const geometry = normalizeArtifactGeometry(feature.geometry);
      if (!geometry) return [];
      return [{
        type: 'Feature',
        geometry,
        properties: {
          hazard,
          probability: Number(feature.properties.probability),
          bucket: Number(feature.properties.bucket),
          label: feature.properties.label,
          color: feature.properties.color,
        },
      }];
    })
    .sort((a, b) => a.properties.bucket - b.properties.bucket);
  return { type: 'FeatureCollection', features };
}

export function artifactCigShapesToFeatureCollection(
  tile: OutlookProbabilityTile | undefined,
  hazard?: ArtifactHazardKey,
): ArtifactCigFeatureCollection | undefined {
  const collection: OutlookCigShapeFeatureCollection | undefined = tile?.cigShapes;
  if (!collection || !Array.isArray(collection.features)) return undefined;
  const candidates = collection.features
    .filter((feature) => {
      const normalized = normalizeHazardName(String(feature.properties.hazard));
      return normalized !== 'thunder' && (!hazard || normalized === hazard);
    })
    .filter((feature) => shouldRenderCigFeature(feature));
  const features = selectSingleCigFeatures(candidates)
    .flatMap((feature): ArtifactCigFeature[] => {
      const geometry = normalizeArtifactGeometry(feature.geometry);
      if (!geometry) return [];
      const normalized = normalizeHazardName(String(feature.properties.hazard)) as ArtifactHazardKey;
      return [{
        type: 'Feature',
        geometry,
        properties: {
          hazard: normalized,
          cig: Number(feature.properties.cig),
          label: 'CIG',
          hatchPattern: 'solidDiagonal',
          sourceCellCount: numericProperty(feature.properties.sourceCellCount),
          displayAreaKm2: numericProperty(feature.properties.displayAreaKm2),
        },
      }];
    })
    .sort((a, b) => {
      if (a.properties.hazard !== b.properties.hazard) return a.properties.hazard.localeCompare(b.properties.hazard);
      return a.properties.cig - b.properties.cig;
    });
  return { type: 'FeatureCollection', features };
}

export function getArtifactHazardGrid(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
  hazard: ArtifactHazardKey,
): number[][] | undefined {
  const grid = getArtifactHourTile(artifacts, forecastHour)?.probabilities?.[hazard];
  return isGrid(grid) ? grid : undefined;
}

export function getArtifactHazardPeak(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
  hazard: ArtifactHazardKey,
): number | undefined {
  return getArtifactHazardPeakLocation(artifacts, forecastHour, hazard)?.probability;
}

export function getArtifactThunderPeak(tile: OutlookProbabilityTile | undefined): number | undefined {
  const grid = tile?.probabilities?.thunder;
  if (!tile || !isGrid(grid)) return undefined;
  let peak: number | undefined;
  grid.forEach((row, rowIndex) => row.forEach((value, colIndex) => {
    const probability = Number(value);
    const lat = Number(tile.lats[rowIndex]?.[colIndex]);
    const lon = Number(tile.lons[rowIndex]?.[colIndex]);
    if (!Number.isFinite(probability) || !Number.isFinite(lat) || !Number.isFinite(lon)) return;
    peak = peak === undefined ? probability : Math.max(peak, probability);
  }));
  return peak;
}

export function getArtifactHazardPeakLocation(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
  hazard: ArtifactHazardKey,
): ArtifactHazardPeakLocation | undefined {
  const tile = getArtifactHourTile(artifacts, forecastHour);
  if (!tile) return undefined;
  const grid = tile.probabilities?.[hazard];
  if (isGrid(grid) && grid.length > 0) {
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
    if (best) return best;
  }
  // Fallback: the static production export strips the dense probability grid
  // from the merged tile but keeps the hazard probability band shapes, so the
  // peak is recovered from the highest-probability band.
  return hazardPeakFromShapes(tile, hazard);
}

function hazardPeakFromShapes(
  tile: OutlookProbabilityTile,
  hazard: ArtifactHazardKey,
): ArtifactHazardPeakLocation | undefined {
  const fc = tile.hazardProbabilityShapes;
  if (!fc || !Array.isArray(fc.features) || fc.features.length === 0) return undefined;
  let best: ArtifactHazardPeakLocation | undefined;
  for (const feature of fc.features) {
    if (normalizeHazardName(String(feature.properties.hazard)) !== hazard) continue;
    const probability = Number(feature.properties.probability);
    if (!Number.isFinite(probability)) continue;
    if (!best || probability > best.probability) {
      const centroid = geometryCentroid(feature.geometry);
      best = { probability, lat: centroid.lat, lon: centroid.lon, row: -1, col: -1 };
    }
  }
  return best;
}

function geometryCentroid(
  geometry: { type: string; coordinates: unknown } | null | undefined,
): { lat: number; lon: number } {
  let sumLat = 0;
  let sumLon = 0;
  let count = 0;
  const add = (lon: number, lat: number) => {
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      sumLat += lat;
      sumLon += lon;
      count += 1;
    }
  };
  if (geometry?.type === 'Polygon') {
    (geometry.coordinates as number[][][]).forEach((ring) => ring.forEach((p) => add(Number(p[0]), Number(p[1]))));
  } else if (geometry?.type === 'MultiPolygon') {
    (geometry.coordinates as number[][][][]).forEach((poly) => poly.forEach((ring) => ring.forEach((p) => add(Number(p[0]), Number(p[1])))));
  }
  return count > 0 ? { lat: sumLat / count, lon: sumLon / count } : { lat: NaN, lon: NaN };
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
  const grid = tile.probabilities?.[hazard];
  if (!isGrid(grid)) return Infinity;
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

export function getArtifactHazardLevel(
  hazard: ArtifactHazardKey,
  probability: number,
  ing?: Ingredients,
): RiskCategory {
  return lvlFromProb(hazard, probability, ing);
}

/**
 * Resolves the "effective" hazard estimate that the Hazard Probability Board
 * actually renders for a given hazard. For tornado/hail/wind this prefers the
 * trained artifact (ML) peak probability when the artifact is ready/loading,
 * falling back to the rule-engine snapshot value otherwise. Flood always uses
 * the snapshot value (no artifact surface is trained for it).
 *
 * This is the single source of truth so the board, the forecast discussion,
 * and any other narrative descriptions stay linked to the same numbers.
 */
export interface ResolvedHazardEstimate {
  probability: number;
  level: RiskCategory;
  /** true when the value originates from the trained artifact surface. */
  isArtifact: boolean;
  /** true when an artifact was expected for this hour but is offline. */
  artifactUnavailable: boolean;
  peakLocation?: ArtifactHazardPeakLocation;
}

export function resolveHazardEstimate(
  hazardKey: HazardKey,
  snapshot: HourSnapshot | null,
  artifacts: OutlookArtifacts | null,
  artifactStatus?: ArtifactStatus,
): ResolvedHazardEstimate {
  const hz = snapshot?.hazards?.[hazardKey];
  const artifactHazard: ArtifactHazardKey | null =
    hazardKey === 'tornado' || hazardKey === 'hail' || hazardKey === 'wind'
      ? hazardKey
      : null;

  const tile = getArtifactHourTile(artifacts, snapshot?.forecastHour);
  const tileHour = tile?.forecastHour ?? snapshot?.forecastHour;
  const canUseArtifact = Boolean(
    artifactHazard && tile && (artifactStatus === 'ready' || artifactStatus === 'loading'),
  );

  const peakLocation = artifactHazard && canUseArtifact
    ? getArtifactHazardPeakLocation(artifacts, tileHour, artifactHazard)
    : undefined;
  const artifactPeak = peakLocation?.probability;

  const artifactUnavailable = Boolean(
    artifactHazard
      && artifactStatus
      && artifactStatus !== 'missing'
      && artifactStatus !== 'ready'
      && !tile,
  );

  const isArtifact = artifactHazard !== null && artifactPeak !== undefined;
  const probability = artifactPeak ?? (artifactUnavailable ? 0 : hz?.probability ?? 0);
  const level: RiskCategory = isArtifact
    ? getArtifactHazardLevel(artifactHazard as ArtifactHazardKey, artifactPeak as number, snapshot?.ingredients ?? undefined)
    : artifactUnavailable
      ? 'TSTM'
      : hz?.level ?? 'TSTM';

  return { probability, level, isArtifact, artifactUnavailable, peakLocation };
}

export function artifactProbabilityToFeatureCollection(
  tile: OutlookProbabilityTile | undefined,
  hazard: ArtifactHazardKey,
): ArtifactProbabilityFeatureCollection {
  if (!tile) return { type: 'FeatureCollection', features: [] };
  const grid = tile.probabilities?.[hazard];
  if (!isGrid(grid)) return { type: 'FeatureCollection', features: [] };
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
  if (!isGrid(tile.categoryOrdinal)) return { type: 'FeatureCollection', features: [] };
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
  if (!isGrid(tile.categoryOrdinal)) return undefined;
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
  return validCells > 0 ? thunderCells / validCells : undefined;
}

export function artifactRiskToFeatureCollection(
  tile: OutlookProbabilityTile | undefined,
): OutlookArtifactFeatureCollection {
  if (!tile) return { type: 'FeatureCollection', features: [] };
  if (!isGrid(tile.categoryOrdinal)) return { type: 'FeatureCollection', features: [] };
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
  if (tile && isGrid(tile.categoryOrdinal) && tile.categoryOrdinal.length > 0) {
    let maxOrdinal = 0;
    tile.categoryOrdinal.forEach((row) => row.forEach((value) => {
      if (Number.isFinite(value)) maxOrdinal = Math.max(maxOrdinal, Number(value));
    }));
    return (['NONE', 'TSTM', 'MRGL', 'SLGT', 'ENH', 'MDT', 'HIGH'][maxOrdinal] ?? 'NONE') as ArtifactRiskCategory;
  }
  // Fallback: the static production export strips the merged tile's category
  // grid, so derive the highest category from the exported risk polygons.
  return maxCategoryFromRiskPolygons(artifacts);
}

function maxCategoryFromRiskPolygons(
  artifacts: OutlookArtifacts | null,
): ArtifactRiskCategory | undefined {
  const fc = artifacts?.riskPolygons;
  if (!fc || !Array.isArray(fc.features) || fc.features.length === 0) return undefined;
  let best: ArtifactRiskCategory | undefined;
  let bestRank = 0;
  for (const feature of fc.features) {
    const category = String(feature.properties?.category ?? 'NONE') as ArtifactRiskCategory;
    const rank = categoryOrdinal(category);
    if (rank > bestRank) {
      bestRank = rank;
      best = category;
    }
  }
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

function shouldRenderCigFeature(feature: OutlookCigShapeFeatureCollection['features'][number]): boolean {
  const cig = Number(feature.properties.cig);
  const sourceCellCount = numericProperty(feature.properties.sourceCellCount);
  if (sourceCellCount !== undefined) {
    return sourceCellCount >= minimumSourceCellsForCig(cig);
  }

  const displayAreaKm2 = numericProperty(feature.properties.displayAreaKm2);
  if (displayAreaKm2 !== undefined) {
    return displayAreaKm2 >= minimumDisplayAreaForCig(cig);
  }

  return true;
}

function selectSingleCigFeatures(
  features: OutlookCigShapeFeatureCollection['features'],
): OutlookCigShapeFeatureCollection['features'] {
  const byHazard = new Map<ArtifactHazardKey, OutlookCigShapeFeatureCollection['features'][number]>();
  features.forEach((feature) => {
    const normalized = normalizeHazardName(String(feature.properties.hazard));
    if (normalized === 'thunder') return;
    const hazard = normalized as ArtifactHazardKey;
    const current = byHazard.get(hazard);
    if (!current || cigSelectionRank(feature) < cigSelectionRank(current)) {
      byHazard.set(hazard, feature);
    }
  });
  return [...byHazard.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([, feature]) => feature);
}

function cigSelectionRank(feature: OutlookCigShapeFeatureCollection['features'][number]): number {
  const cig = Number(feature.properties.cig);
  return Number.isFinite(cig) ? cig : 99;
}

function minimumSourceCellsForCig(cig: number): number {
  if (cig >= 3) return 24;
  if (cig === 2) return 18;
  return 12;
}

function minimumDisplayAreaForCig(cig: number): number {
  if (cig >= 3) return 5_000;
  if (cig === 2) return 3_500;
  return 2_000;
}

function numericProperty(value: unknown): number | undefined {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : undefined;
}

function trainedThunderTstmFeature(
  tile: OutlookProbabilityTile | undefined,
): OutlookProbabilityShapeFeatureCollection['features'][number] | undefined {
  const features = tile?.hazardProbabilityShapes?.features;
  if (!Array.isArray(features)) return undefined;
  return features
    .filter((feature) => normalizeHazardName(feature.properties.hazard) === 'thunder')
    .filter((feature) => {
      const probability = Number(feature.properties.probability);
      const bucket = Number(feature.properties.bucket);
      return probability >= THUNDER_THRESHOLDS[0] || bucket === 0;
    })
    .sort((a, b) => Number(a.properties.bucket) - Number(b.properties.bucket))[0];
}

function normalizeHazardName(hazard: string): GeneratedArtifactHazardKey {
  return hazard === 'thunderstorm' ? 'thunder' : hazard as GeneratedArtifactHazardKey;
}

function normalizeArtifactGeometry(
  geometry: OutlookProbabilityShapeFeatureCollection['features'][number]['geometry'],
): ArtifactProbabilityFeature['geometry'] | undefined {
  const polygons = geometry.type === 'Polygon'
    ? [normalizePolygonRings(geometry.coordinates as number[][][])]
    : (geometry.coordinates as number[][][][]).map((polygon) => normalizePolygonRings(polygon));
  const renderablePolygons = polygons.filter(isRenderablePolygon);
  if (renderablePolygons.length === 0) return undefined;
  if (renderablePolygons.length === 1) {
    return { type: 'Polygon', coordinates: renderablePolygons[0] };
  }
  return { type: 'MultiPolygon', coordinates: renderablePolygons };
}

function isRenderablePolygon(rings: number[][][]): boolean {
  if (!Array.isArray(rings) || rings.length === 0 || rings[0].length < 4) return false;
  const area = geoArea({ type: 'Polygon', coordinates: rings } as GeoPermissibleObjects);
  return Number.isFinite(area) && area > 1e-12 && area < 2 * Math.PI;
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

function isGrid(grid: unknown): grid is number[][] {
  return Array.isArray(grid) && grid.every((row) => Array.isArray(row));
}

function cellRing(tile: OutlookProbabilityTile, row: number, col: number): number[][] {
  const latRow = Array.isArray(tile.lats[row]) ? tile.lats[row] : [];
  const lonRow = Array.isArray(tile.lons[row]) ? tile.lons[row] : [];
  const prevLatRow = Array.isArray(tile.lats[Math.max(0, row - 1)])
    ? tile.lats[Math.max(0, row - 1)]
    : [];
  const nextLatRow = Array.isArray(tile.lats[Math.min(tile.lats.length - 1, row + 1)])
    ? tile.lats[Math.min(tile.lats.length - 1, row + 1)]
    : [];
  const lat = Number(tile.lats[row]?.[col]);
  const lon = Number(tile.lons[row]?.[col]);
  const prevLon = Number(lonRow[Math.max(0, col - 1)]);
  const nextLon = Number(lonRow[Math.min(Math.max(0, lonRow.length - 1), col + 1)]);
  const prevLat = Number(prevLatRow[col]);
  const nextLat = Number(nextLatRow[col]);
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
