import { useEffect, useMemo, useState } from 'react';
import { ComposableMap, Geographies, Geography, Marker } from 'react-simple-maps';
import type { ActiveRegion, HourSnapshot, RiskCategory, UpperAirVector } from '../types/forecast';
import type {
  OutlookArtifacts,
  OutlookArtifactFeatureCollection,
  ArtifactRiskCategory,
  SpcCategoryFeatureCollection,
  SpcStormReport,
} from '../types/outlookArtifacts';
import {
  artifactRiskShapesToFeatureCollection,
  artifactRiskToFeatureCollection,
  getArtifactHourTile,
  getArtifactMaxCategory,
} from '../utils/artifactProbabilities';
import { map500mbLines } from '../utils/upperAirLines';
import { map500mbWindVectors } from '../utils/upperAirWind';
import { buildUpperAirIntensitySegments, upperAirLineVisualStyle } from '../utils/upperAirLineStyle';
import { apiUrl } from '../utils/apiBase';

const STATES_URL = '/us-states-10m.json';

export type SpcComparisonMode = 'auto' | 'spc' | 'overlay';

type ArtifactState = 'loading' | 'ready' | 'missing' | 'error' | 'pending' | 'failed';

interface GeneratedOutlookMapProps {
  snapshot: HourSnapshot | null;
  status: ArtifactState;
  artifacts: OutlookArtifacts | null;
  message: string | null;
  activeRegion?: ActiveRegion;
  comparisonMode?: SpcComparisonMode;
  stormReportsMode?: 'none' | 'all' | 'tornado' | 'hail' | 'wind';
  stormReports?: SpcStormReport[];
  spcDay1Override?: SpcCategoryFeatureCollection | null;
}

function generatedModelLabel(activeRegion: ActiveRegion, _artifacts: OutlookArtifacts | null): string {
  void activeRegion;
  return 'HRRR';
}

interface UpperAirFeature {
  type: 'Feature';
  properties: {
    idx: number;
    value: number;
    stroke: string;
    strokeWidth: number;
    strokeOpacity: number;
    haloWidth: number;
    haloOpacity: number;
  };
  geometry: { type: 'LineString'; coordinates: [number, number][] };
}

interface UpperAirStreakFeature {
  type: 'Feature';
  properties: {
    idx: number;
    stroke: string;
    strokeWidth: number;
    strokeOpacity: number;
  };
  geometry: { type: 'LineString'; coordinates: [number, number][] };
}

const LEVEL_STYLE: Record<Exclude<ArtifactRiskCategory, 'NONE' | 'MOD'>, { fill: string; stroke: string; label: string }> = {
  TSTM: { fill: '#c9efc6', stroke: '#5f7f5f', label: 'TSTM' },
  MRGL: { fill: '#6fc36a', stroke: '#2e6f36', label: 'MRGL' },
  SLGT: { fill: '#fff45c', stroke: '#f5a400', label: 'SLGT' },
  ENH:  { fill: '#d9b57b', stroke: '#8a6a35', label: 'ENH'  },
  MDT:  { fill: '#df7777', stroke: '#b52c2c', label: 'MDT'  },
  HIGH: { fill: '#e16ce5', stroke: '#9a249f', label: 'HIGH' },
};

const CATEGORY_RAMP: Array<Exclude<ArtifactRiskCategory, 'NONE' | 'MOD'>> = ['TSTM', 'MRGL', 'SLGT', 'ENH', 'MDT', 'HIGH'];
const CATEGORY_ORD: Record<ArtifactRiskCategory, number> = {
  NONE: 0,
  TSTM: 1,
  MRGL: 2,
  SLGT: 3,
  ENH: 4,
  MDT: 5,
  MOD: 5,
  HIGH: 6,
};

// Minimum planar area (deg^2) below which a feature is demoted one risk
// category. Tiny ML/contour artifacts (e.g., a single-cell ENH dot) end
// up rendering as a meaningless speck inside the surrounding band; we
// collapse them down so the visualization matches what SPC would draw.
const MIN_FEATURE_AREA_DEG2: Partial<Record<ArtifactRiskCategory, number>> = {
  ENH: 0.04,
  MDT: 0.04,
  MOD: 0.04,
  HIGH: 0.04,
};

function ringArea(ring: number[][]): number {
  let area = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const [x0, y0] = ring[i];
    const [x1, y1] = ring[(i + 1) % ring.length];
    area += x0 * y1 - x1 * y0;
  }
  return Math.abs(area / 2);
}

function polygonArea(rings: number[][][]): number {
  if (!rings || rings.length === 0) return 0;
  const exterior = ringArea(rings[0]);
  const holes = rings.slice(1).reduce((sum, hole) => sum + ringArea(hole), 0);
  return Math.max(0, exterior - holes);
}

function geometryArea(geometry: { type: 'Polygon' | 'MultiPolygon'; coordinates: number[][][] | number[][][][] } | undefined): number {
  if (!geometry) return 0;
  if (geometry.type === 'Polygon') {
    return polygonArea(geometry.coordinates as number[][][]);
  }
  return (geometry.coordinates as number[][][][]).reduce(
    (total, polygon) => total + polygonArea(polygon),
    0,
  );
}

function demoteOnce(category: ArtifactRiskCategory): ArtifactRiskCategory {
  if (category === 'HIGH') return 'MDT';
  if (category === 'MOD' || category === 'MDT') return 'ENH';
  if (category === 'ENH') return 'SLGT';
  return category;
}

function demoteSmallFeatures(collection: OutlookArtifactFeatureCollection): OutlookArtifactFeatureCollection {
  return {
    ...collection,
    features: collection.features.map((feature) => {
      const area = geometryArea(feature.geometry);
      let category = feature.properties.category;
      let safety = 0;
      while (safety < 6) {
        const min = MIN_FEATURE_AREA_DEG2[category];
        if (min === undefined || area >= min) break;
        const next = demoteOnce(category);
        if (next === category) break;
        category = next;
        safety += 1;
      }
      if (category === feature.properties.category) return feature;
      return { ...feature, properties: { ...feature.properties, category } };
    }),
  };
}

function normalizeCategory(category: ArtifactRiskCategory): Exclude<ArtifactRiskCategory, 'NONE' | 'MOD'> {
  return category === 'MOD' ? 'MDT' : category === 'NONE' ? 'TSTM' : category;
}

function displayCategory(category: ArtifactRiskCategory | RiskCategory | undefined): string {
  if (!category) return '--';
  return category === 'MOD' ? 'MDT' : category;
}

function spcCategory(feature: SpcCategoryFeatureCollection['features'][number] | undefined): Exclude<ArtifactRiskCategory, 'NONE' | 'MOD'> {
  const label = String(feature?.properties?.LABEL || '').toUpperCase();
  if (label === 'HIGH') return 'HIGH';
  if (label === 'MDT' || label === 'MOD') return 'MDT';
  if (label === 'ENH') return 'ENH';
  if (label === 'SLGT') return 'SLGT';
  if (label === 'MRGL') return 'MRGL';
  return 'TSTM';
}

function maxCategory(collection: OutlookArtifactFeatureCollection): ArtifactRiskCategory | undefined {
  return collection.features.reduce<ArtifactRiskCategory | undefined>((best, feature) => {
    const category = feature.properties.category;
    if (!best || CATEGORY_ORD[category] > CATEGORY_ORD[best]) return category;
    return best;
  }, undefined);
}

function maxSpcCategory(collection: SpcCategoryFeatureCollection): ArtifactRiskCategory | undefined {
  return collection.features.reduce<ArtifactRiskCategory | undefined>((best, feature) => {
    const category = spcCategory(feature);
    if (!best || CATEGORY_ORD[category] > CATEGORY_ORD[best]) return category;
    return best;
  }, undefined);
}

function formatGeneratedAt(iso: string | undefined): string {
  if (!iso) return 'artifact time unavailable';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${String(date.getUTCMonth() + 1).padStart(2, '0')}/${String(date.getUTCDate()).padStart(2, '0')} ${String(date.getUTCHours()).padStart(2, '0')}${String(date.getUTCMinutes()).padStart(2, '0')}Z`;
}

function normalizeArtifactCollection(collection: OutlookArtifactFeatureCollection | undefined): OutlookArtifactFeatureCollection | undefined {
  if (!collection || !Array.isArray(collection.features)) return undefined;
  const demoted = demoteSmallFeatures(collection);
  return {
    ...demoted,
    features: [...demoted.features].sort((a, b) => CATEGORY_ORD[a.properties.category] - CATEGORY_ORD[b.properties.category]).map((feature) => {
      if (feature.geometry.type === 'Polygon') {
        const coordinates = normalizePolygonRings(feature.geometry.coordinates as number[][][]);
        return { ...feature, geometry: { ...feature.geometry, coordinates } };
      }
      const coordinates = (feature.geometry.coordinates as number[][][][]).map((polygon) =>
        normalizePolygonRings(polygon),
      );
      return { ...feature, geometry: { ...feature.geometry, coordinates } };
    }),
  };
}

function normalizeSpcCollection(collection: SpcCategoryFeatureCollection | null): SpcCategoryFeatureCollection | null {
  if (!collection || !Array.isArray(collection.features)) return null;
  return {
    ...collection,
    features: [...collection.features].sort((a, b) => CATEGORY_ORD[spcCategory(a)] - CATEGORY_ORD[spcCategory(b)]).map((feature) => {
      if (feature.geometry.type === 'Polygon') {
        const coordinates = normalizePolygonRings(feature.geometry.coordinates as number[][][]);
        return { ...feature, geometry: { ...feature.geometry, coordinates } };
      }
      const coordinates = (feature.geometry.coordinates as number[][][][]).map((polygon) =>
        normalizePolygonRings(polygon),
      );
      return { ...feature, geometry: { ...feature.geometry, coordinates } };
    }),
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

function signedRingArea(coords: number[][]): number {
  let area = 0;
  for (let i = 0; i < coords.length; i += 1) {
    const [x0, y0] = coords[i];
    const [x1, y1] = coords[(i + 1) % coords.length];
    area += x0 * y1 - x1 * y0;
  }
  return area / 2;
}

function samePoint(a: number[] | undefined, b: number[] | undefined): boolean {
  return Boolean(a && b && a.length >= 2 && b.length >= 2 && a[0] === b[0] && a[1] === b[1]);
}

function riskPolygonsForHour(
  collection: OutlookArtifactFeatureCollection | undefined,
  forecastHour: number | undefined,
): OutlookArtifactFeatureCollection | undefined {
  if (!collection || forecastHour === undefined || !Array.isArray(collection.features)) return undefined;
  const features = collection.features.filter((feature) => feature.properties.forecastHour === forecastHour);
  if (features.length === 0) return undefined;
  return { ...collection, features };
}

function comparisonTitle(mode: SpcComparisonMode, modelLabel: string): string {
  if (mode === 'spc') return 'Official SPC Day 1 Risk Boundaries';
  if (mode === 'overlay') return `${modelLabel} / SPC Overlay Comparison`;
  return `${modelLabel}/XGBoost Risk Levels`;
}

export default function GeneratedOutlookMap({
  snapshot,
  status,
  artifacts,
  message,
  activeRegion = 'conus',
  comparisonMode = 'auto',
  stormReportsMode = 'none',
  stormReports = [],
  spcDay1Override = null,
}: GeneratedOutlookMapProps) {
  const [spcDay1, setSpcDay1] = useState<SpcCategoryFeatureCollection | null>(null);
  const [spcStatus, setSpcStatus] = useState<'idle' | 'loading' | 'ready' | 'missing' | 'error'>('idle');
  const modelLabel = generatedModelLabel(activeRegion, artifacts);
  const effectiveComparisonMode: SpcComparisonMode = comparisonMode;
  const geoUrl = STATES_URL;
  const projection = 'geoAlbers';
  const projectionConfig = {
    rotate: [96, 0, 0] as [number, number, number],
    center: [0, 38] as [number, number],
    parallels: [29.5, 45.5] as [number, number],
    scale: 1000,
  };
  const selectedForecastHour = snapshot?.forecastHour;
  const spcVerification = artifacts?.metadata?.spcVerification ?? null;
  const showAutoLayer = effectiveComparisonMode === 'auto' || effectiveComparisonMode === 'overlay';
  const showSpcLayer = effectiveComparisonMode === 'spc' || effectiveComparisonMode === 'overlay';
  const isOverlayMode = effectiveComparisonMode === 'overlay';
  const effectiveSpcDay1 = spcDay1Override ?? spcDay1;

  useEffect(() => {
    if (spcDay1Override) {
      setSpcStatus('ready');
      return;
    }
    if (!showSpcLayer) {
      if (!spcDay1) setSpcStatus('idle');
      return;
    }
    if (spcDay1) {
      setSpcStatus('ready');
      return;
    }

    const controller = new AbortController();
    setSpcStatus('loading');
    fetch(apiUrl('/api/outlook/spc-day1-category'), {
      signal: controller.signal,
      cache: 'no-store',
    })
      .then((response) => {
        if (response.status === 404) {
          setSpcStatus('missing');
          return null;
        }
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<SpcCategoryFeatureCollection>;
      })
      .then((payload) => {
        if (!payload) return;
        setSpcDay1(payload);
        setSpcStatus('ready');
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setSpcStatus('error');
      });

    return () => controller.abort();
  }, [showSpcLayer, spcDay1, spcDay1Override]);

  const selectedTile = useMemo(
    () => getArtifactHourTile(artifacts, selectedForecastHour),
    [artifacts, selectedForecastHour],
  );
  const artifactRiskCollection = useMemo(
    () => normalizeArtifactCollection(riskPolygonsForHour(artifacts?.riskPolygons, selectedForecastHour)),
    [artifacts, selectedForecastHour],
  );
  const aggregateRiskCollection = useMemo(
    () => normalizeArtifactCollection(riskPolygonsForHour(artifacts?.aggregateRiskPolygons, selectedForecastHour)),
    [artifacts, selectedForecastHour],
  );
  const tileVectorRiskCollection = useMemo(
    () => normalizeArtifactCollection(artifactRiskShapesToFeatureCollection(selectedTile)),
    [selectedTile],
  );
  const tileRiskCollection = useMemo(
    () => normalizeArtifactCollection(artifactRiskToFeatureCollection(selectedTile)),
    [selectedTile],
  );
  const selectedCollection = useMemo(
    () => artifactRiskCollection && artifactRiskCollection.features.length > 0
      ? artifactRiskCollection
      : aggregateRiskCollection && aggregateRiskCollection.features.length > 0
        ? aggregateRiskCollection
        : tileVectorRiskCollection && tileVectorRiskCollection.features.length > 0
          ? tileVectorRiskCollection
          : tileRiskCollection ?? { type: 'FeatureCollection' as const, features: [] },
    [artifactRiskCollection, aggregateRiskCollection, tileVectorRiskCollection, tileRiskCollection],
  );
  const usingTileRisk = selectedCollection === tileRiskCollection;
  const renderedCollection = selectedCollection;
  const hasGeneratedLayer = renderedCollection.features.length > 0;
  const normalizedSpcDay1 = useMemo(() => normalizeSpcCollection(effectiveSpcDay1), [effectiveSpcDay1]);
  const hasSpcLayer = Boolean(normalizedSpcDay1?.features.length);
  const renderedMax = maxCategory(renderedCollection);
  const spcMax = normalizedSpcDay1 ? maxSpcCategory(normalizedSpcDay1) : undefined;
  const tileMax = getArtifactMaxCategory(artifacts, selectedForecastHour);
  const hasGeneratedArtifact = Boolean(selectedTile || artifactRiskCollection?.features.length || aggregateRiskCollection?.features.length);
  const mapCategory = effectiveComparisonMode === 'spc'
    ? spcMax
    : hasGeneratedArtifact
      ? renderedMax ?? tileMax
      : undefined;

  const upperAirLines = useMemo(() => map500mbLines(snapshot), [snapshot]);

  const upperAirLineCollection = useMemo(
    () => {
      return {
        type: 'FeatureCollection' as const,
        features: upperAirLines.map((line, idx): UpperAirFeature => {
          const style = upperAirLineVisualStyle(snapshot, idx, upperAirLines.length);
          return {
            type: 'Feature',
            properties: { idx, value: line.value, ...style },
            geometry: { type: 'LineString', coordinates: line.coords },
          };
        }),
      };
    },
    [snapshot, upperAirLines],
  );

  const upperAirStreakCollection = useMemo(
    () => ({
      type: 'FeatureCollection' as const,
      features: buildUpperAirIntensitySegments(snapshot, upperAirLines)
        .map((segment, idx): UpperAirStreakFeature => ({
          type: 'Feature',
          properties: {
            idx,
            stroke: segment.stroke,
            strokeWidth: segment.strokeWidth,
            strokeOpacity: segment.strokeOpacity,
          },
          geometry: { type: 'LineString', coordinates: segment.coords },
        })),
    }),
    [snapshot, upperAirLines],
  );

  const windVectors = useMemo(() => map500mbWindVectors(snapshot, 150), [snapshot]);
  const hasUpperAirOverlay = snapshot?.upperAirOverlay?.domain === 'CONUS' && snapshot.upperAirOverlay.level === '500mb';

  return (
    <div className="outlook-export-map-card border-[3px] border-ink bg-paper shadow-retro flex flex-col">
      <header className="min-h-[40px] border-b-[2px] border-ink bg-ink text-paper px-3 py-2 flex items-center justify-between gap-3 overflow-visible">
        <span className="shrink-0 whitespace-nowrap pr-3 font-display font-extrabold uppercase text-[13px] leading-none tracking-normal">
          {comparisonTitle(effectiveComparisonMode, modelLabel)}
        </span>
        <div className="flex shrink-0 items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-widest text-paper/70">
            {effectiveComparisonMode === 'overlay'
              ? `${Math.round((spcVerification?.agreementFraction ?? 0) * 100)}% AGREE`
              : `CAT ${displayCategory(mapCategory)}`}
          </span>
        </div>
      </header>

      <div className="outlook-export-map-frame aspect-[16/9] xl:aspect-[2/1] relative overflow-hidden bg-[#fbfbf8]">
        <ComposableMap
          projection={projection}
          width={900}
          height={520}
          projectionConfig={projectionConfig}
          style={{ width: '100%', height: '100%' }}
        >
          <Geographies geography={geoUrl}>
            {({ geographies }) =>
              geographies.map((geo) => (
                <Geography
                  key={geo.rsmKey}
                  geography={geo}
                  style={{
                    default: { fill: '#ffffff', stroke: '#b8b8b8', strokeWidth: 0.65, outline: 'none' },
                    hover:   { fill: '#ffffff', stroke: '#b8b8b8', strokeWidth: 0.65, outline: 'none' },
                    pressed: { fill: '#ffffff', stroke: '#b8b8b8', strokeWidth: 0.65, outline: 'none' },
                  }}
                />
              ))
            }
          </Geographies>

          {hasUpperAirOverlay && upperAirLineCollection.features.length > 0 && (
            <Geographies geography={upperAirLineCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`gen-h500-${geo.rsmKey ?? index}`}
                    geography={geo}
                    style={{
                      default: { fill: 'none', stroke: '#7e858b', strokeWidth: 0.75, strokeOpacity: 0.28, outline: 'none' },
                      hover:   { fill: 'none', stroke: '#7e858b', strokeWidth: 0.75, strokeOpacity: 0.28, outline: 'none' },
                      pressed: { fill: 'none', stroke: '#7e858b', strokeWidth: 0.75, strokeOpacity: 0.28, outline: 'none' },
                    }}
                  />
                ))
              }
            </Geographies>
          )}

          {showAutoLayer && hasGeneratedLayer && (
            <Geographies geography={renderedCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const rawCategory = renderedCollection.features[index]?.properties.category ?? 'TSTM';
                  const category = normalizeCategory(rawCategory);
                  const style = LEVEL_STYLE[category];
                  const featureKey = `${geo.rsmKey ?? index}-${rawCategory}`;
                  return (
                    <Geography
                      key={`generated-risk-${featureKey}`}
                      geography={geo}
                      tabIndex={-1}
                      style={{
                        // Fill opacity raised to 0.80 / 0.88 so bands
                        // read as solid colors. Bands merge through color
                        // contrast alone since all boundary strokes removed.
                        default: {
                          fill: isOverlayMode ? 'none' : style.fill,
                          fillOpacity: isOverlayMode ? 0 : usingTileRisk ? 0.88 : 0.80,
                          stroke: isOverlayMode ? style.stroke : 'none',
                          strokeWidth: isOverlayMode ? 1.65 : 0,
                          strokeOpacity: isOverlayMode ? 0.9 : undefined,
                          strokeLinejoin: 'round',
                          strokeLinecap: 'round',
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        hover: {
                          fill: isOverlayMode ? 'none' : style.fill,
                          fillOpacity: isOverlayMode ? 0 : usingTileRisk ? 0.88 : 0.80,
                          stroke: isOverlayMode ? style.stroke : 'none',
                          strokeWidth: isOverlayMode ? 1.65 : 0,
                          strokeOpacity: isOverlayMode ? 0.9 : undefined,
                          strokeLinejoin: 'round',
                          strokeLinecap: 'round',
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        pressed: {
                          fill: isOverlayMode ? 'none' : style.fill,
                          fillOpacity: isOverlayMode ? 0 : usingTileRisk ? 0.88 : 0.80,
                          stroke: isOverlayMode ? style.stroke : 'none',
                          strokeWidth: isOverlayMode ? 1.65 : 0,
                          strokeOpacity: isOverlayMode ? 0.9 : undefined,
                          strokeLinejoin: 'round',
                          strokeLinecap: 'round',
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                      }}
                    />
                  );
                })
              }
            </Geographies>
          )}

          {showSpcLayer && hasSpcLayer && normalizedSpcDay1 && (
            <Geographies geography={normalizedSpcDay1}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const category = spcCategory(normalizedSpcDay1.features[index]);
                  const style = LEVEL_STYLE[category];
                  const spcFill = typeof geo.properties.fill === 'string' ? geo.properties.fill : style.fill;
                  const spcStroke = typeof geo.properties.stroke === 'string' ? geo.properties.stroke : style.stroke;
                  return (
                    <Geography
                      key={`official-spc-risk-${geo.rsmKey ?? index}-${category}`}
                      geography={geo}
                      tabIndex={-1}
                      style={{
                        default: {
                          fill: effectiveComparisonMode === 'spc' ? spcFill : 'none',
                          fillOpacity: effectiveComparisonMode === 'spc' ? 0.72 : 0,
                          stroke: effectiveComparisonMode === 'spc' ? 'none' : spcStroke,
                          strokeWidth: effectiveComparisonMode === 'spc' ? 0 : 2,
                          strokeOpacity: effectiveComparisonMode === 'spc' ? 0 : 0.92,
                          strokeDasharray: effectiveComparisonMode === 'spc' ? undefined : '7 4',
                          strokeLinejoin: 'round',
                          strokeLinecap: 'round',
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        hover: {
                          fill: effectiveComparisonMode === 'spc' ? spcFill : 'none',
                          fillOpacity: effectiveComparisonMode === 'spc' ? 0.72 : 0,
                          stroke: effectiveComparisonMode === 'spc' ? 'none' : spcStroke,
                          strokeWidth: effectiveComparisonMode === 'spc' ? 0 : 2,
                          strokeOpacity: effectiveComparisonMode === 'spc' ? 0 : 0.92,
                          strokeDasharray: effectiveComparisonMode === 'spc' ? undefined : '7 4',
                          strokeLinejoin: 'round',
                          strokeLinecap: 'round',
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        pressed: {
                          fill: effectiveComparisonMode === 'spc' ? spcFill : 'none',
                          fillOpacity: effectiveComparisonMode === 'spc' ? 0.72 : 0,
                          stroke: effectiveComparisonMode === 'spc' ? 'none' : spcStroke,
                          strokeWidth: effectiveComparisonMode === 'spc' ? 0 : 2,
                          strokeOpacity: effectiveComparisonMode === 'spc' ? 0 : 0.92,
                          strokeDasharray: effectiveComparisonMode === 'spc' ? undefined : '7 4',
                          strokeLinejoin: 'round',
                          strokeLinecap: 'round',
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                      }}
                    />
                  );
                })
              }
            </Geographies>
          )}

          <Geographies geography={geoUrl}>
            {({ geographies }) =>
              geographies.map((geo) => (
                <Geography
                  key={`generated-state-outline-${geo.rsmKey}`}
                  geography={geo}
                  style={{
                    default: { fill: 'none', stroke: '#8f8f8f', strokeWidth: 0.7, strokeOpacity: 0.85, outline: 'none' },
                    hover:   { fill: 'none', stroke: '#8f8f8f', strokeWidth: 0.7, strokeOpacity: 0.85, outline: 'none' },
                    pressed: { fill: 'none', stroke: '#8f8f8f', strokeWidth: 0.7, strokeOpacity: 0.85, outline: 'none' },
                  }}
                />
              ))
            }
          </Geographies>

          {hasUpperAirOverlay && upperAirLineCollection.features.length > 0 && (
            <Geographies geography={upperAirLineCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`gen-h500-intensity-${geo.rsmKey ?? index}`}
                    geography={geo}
                    style={{
                      default: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      hover: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      pressed: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                    }}
                  />
                ))
              }
            </Geographies>
          )}

          {hasUpperAirOverlay && upperAirStreakCollection.features.length > 0 && (
            <Geographies geography={upperAirStreakCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`gen-h500-streak-${geo.rsmKey ?? index}`}
                    geography={geo}
                    style={{
                      default: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      hover: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      pressed: {
                        fill: 'none',
                        stroke: geo.properties.stroke as string,
                        strokeWidth: geo.properties.strokeWidth as number,
                        strokeOpacity: geo.properties.strokeOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                    }}
                  />
                ))
              }
            </Geographies>
          )}

          {hasUpperAirOverlay && windVectors.map((vector, idx) => (
            <Marker key={`generated-wind-vector-top-${idx}`} coordinates={[vector.lon, vector.lat]}>
              <WindBarb vector={vector} top />
            </Marker>
          ))}

          {stormReportsMode && stormReportsMode !== 'none' && stormReports && stormReports
            .filter((report) => stormReportsMode === 'all' || report.type === stormReportsMode)
            .map((report, idx) => {
              let markerColor = '#e11d48'; // crimson (Tornado)
              let markerShape = 'triangle';
              if (report.type === 'hail') {
                markerColor = '#16a34a'; // forest green
                markerShape = 'circle';
              } else if (report.type === 'wind') {
                markerColor = '#2563eb'; // steel blue
                markerShape = 'square';
              }
              
              const reportTypeLabel = report.type.toUpperCase();
              const valLabel = report.value ? ` (${report.value})` : '';
              const timeLabel = report.time ? ` at ${report.time} UTC` : '';
              const locLabel = report.location ? `\nLocation: ${report.location}` : '';
              const commentLabel = report.comment ? `\nDescription: ${report.comment}` : '';
              
              const tooltipText = `${reportTypeLabel}${valLabel}${timeLabel}${locLabel}${commentLabel}`;
              
              return (
                <Marker key={`storm-report-${report.type}-${idx}`} coordinates={[report.lon, report.lat]}>
                  {markerShape === 'triangle' && (
                    <polygon
                      points="0,-6 5,3 -5,3"
                      fill={markerColor}
                      stroke="#ffffff"
                      strokeWidth={1}
                    >
                      <title>{tooltipText}</title>
                    </polygon>
                  )}
                  {markerShape === 'circle' && (
                    <circle
                      r={4}
                      fill={markerColor}
                      stroke="#ffffff"
                      strokeWidth={1}
                    >
                      <title>{tooltipText}</title>
                    </circle>
                  )}
                  {markerShape === 'square' && (
                    <rect
                      x={-3.5}
                      y={-3.5}
                      width={7}
                      height={7}
                      fill={markerColor}
                      stroke="#ffffff"
                      strokeWidth={1}
                    >
                      <title>{tooltipText}</title>
                    </rect>
                  )}
                </Marker>
              );
            })}
        </ComposableMap>

        {!hasGeneratedArtifact && effectiveComparisonMode !== 'spc' && (
          <div className="absolute inset-4 flex items-center justify-center">
            <div className="max-w-[520px] border-[3px] border-ink bg-paper p-4 shadow-retro">
              <div className="font-display text-[14px] font-extrabold uppercase tracking-wider">
                {status === 'loading' || status === 'pending'
                  ? 'Forecast hour unavailable'
                  : 'Generated outlook layer unavailable'}
              </div>
              <p className="mt-2 font-mono text-[11px] leading-relaxed text-ink/70">
                {status === 'loading'
                  ? 'Selected forecast hour is still fetching generated outlook artifacts.'
                  : status === 'pending'
                    ? message ?? 'Selected forecast hour is still generating.'
                    : status === 'failed'
                      ? message ?? 'Selected forecast hour failed to generate.'
                      : message ?? 'Run the HRRR/XGBoost artifact pipeline to publish risk polygons for this map.'}
              </p>
            </div>
          </div>
        )}

        {showSpcLayer && !hasSpcLayer && (
          <div className="absolute inset-4 flex items-center justify-center">
            <div className="max-w-[520px] border-[3px] border-ink bg-paper p-4 shadow-retro">
              <div className="font-display text-[14px] font-extrabold uppercase tracking-wider">
                SPC Day 1 layer unavailable
              </div>
              <p className="mt-2 font-mono text-[11px] leading-relaxed text-ink/70">
                {spcStatus === 'loading'
                  ? 'Fetching official SPC Day 1 category polygons.'
                  : spcStatus === 'missing'
                    ? 'The official SPC Day 1 GeoJSON artifact has not been published for this run.'
                    : 'The official SPC Day 1 GeoJSON artifact could not be loaded.'}
              </p>
            </div>
          </div>
        )}

        <div className="absolute bottom-2 left-2 border-[2px] border-ink bg-paper px-2.5 py-2 shadow-retro-sm">
          <div className="font-mono text-[9px] uppercase tracking-[0.22em] text-ink/70 leading-none mb-1.5">
            {isOverlayMode ? 'SPC comparison legend' : effectiveComparisonMode === 'spc' ? 'Official SPC categories' : 'Generated risk categories'}
          </div>
          {isOverlayMode ? (
            <div className="grid grid-cols-1 gap-y-1">
              <div className="flex items-center gap-1 font-mono text-[10px] font-bold leading-none">
                <span className="inline-block h-3 w-5 border-[1.5px] border-ink bg-[linear-gradient(90deg,transparent_0,transparent_45%,rgba(95,143,34,0.8)_45%,rgba(95,143,34,0.8)_55%,transparent_55%)]" aria-hidden />
                <span>AutoOutlook contours</span>
              </div>
              <div className="flex items-center gap-1 font-mono text-[10px] font-bold leading-none">
                <span className="inline-block h-3 w-5 border-[1.5px] border-ink bg-[repeating-linear-gradient(90deg,transparent_0,transparent_3px,rgba(38,102,42,0.9)_3px,rgba(38,102,42,0.9)_6px)]" aria-hidden />
                <span>SPC Day 1 contours</span>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-x-2 gap-y-1">
              {CATEGORY_RAMP.map((category) => (
                <div key={category} className="flex items-center gap-1 font-mono text-[10px] font-bold leading-none">
                  <span
                    className="inline-block h-3 w-3 border-[1.5px] border-ink"
                    style={{ backgroundColor: LEVEL_STYLE[category].fill }}
                    aria-hidden
                  />
                  <span>{LEVEL_STYLE[category].label}</span>
                </div>
              ))}
            </div>
          )}

          {stormReportsMode && stormReportsMode !== 'none' && (
            <div className="mt-2.5 border-t-[1.5px] border-ink/30 pt-2">
              <div className="font-mono text-[8px] uppercase tracking-[0.2em] text-ink/75 leading-none mb-1.5">
                Verified Reports
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {(stormReportsMode === 'all' || stormReportsMode === 'tornado') && (
                  <div className="flex items-center gap-1 font-mono text-[9px] font-bold leading-none">
                    <span className="inline-block w-0 h-0 border-l-[4px] border-l-transparent border-r-[4px] border-r-transparent border-b-[7px] border-b-[#e11d48]" aria-hidden />
                    <span>Tornado</span>
                  </div>
                )}
                {(stormReportsMode === 'all' || stormReportsMode === 'hail') && (
                  <div className="flex items-center gap-1 font-mono text-[9px] font-bold leading-none">
                    <span className="inline-block h-2 w-2 rounded-full bg-[#16a34a]" aria-hidden />
                    <span>Hail</span>
                  </div>
                )}
                {(stormReportsMode === 'all' || stormReportsMode === 'wind') && (
                  <div className="flex items-center gap-1 font-mono text-[9px] font-bold leading-none">
                    <span className="inline-block h-2 w-2 bg-[#2563eb]" aria-hidden />
                    <span>Wind</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {artifacts?.metadata && (
          <div className="absolute right-2 bottom-2 border-[2px] border-ink bg-paper px-2.5 py-2 shadow-retro-sm font-mono text-[9px] uppercase tracking-widest text-ink/70">
            <div>{artifacts.metadata.cycle}</div>
            <div>
              {effectiveComparisonMode === 'spc'
                ? `SPC ${spcVerification?.spcForecaster || 'DAY 1'}`
                : `Generated ${formatGeneratedAt(artifacts.metadata.generatedAtISO)}`}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function WindBarb({ vector, top = false }: { vector: UpperAirVector; top?: boolean }) {
  if (!top || vector.speedKt < 22) return null;
  const length = 10;
  const featherCount = Math.max(1, Math.min(4, Math.round(vector.speedKt / 22)));
  const angleDeg = (Math.atan2(-vector.vKt, vector.uKt) * 180 / Math.PI) + 180;
  const opacity = 0.36;
  const stroke = '#50565c';
  const halo = '#ffffff';
  const feathers = (prefix: string) => Array.from({ length: featherCount }, (_, i) => {
    const x = length - i * 2.6;
    return <path key={`${prefix}-${i}`} d={`M ${x} 0 L ${x - 3.4} 4.6`} />;
  });

  return (
    <g transform={`rotate(${angleDeg})`} opacity={opacity} strokeLinecap="square">
      <g stroke={halo} strokeWidth={2.2} fill="none" opacity={0.42}>
        <path d={`M ${-length} 0 L ${length} 0`} />
        {feathers('halo')}
      </g>
      <g stroke={stroke} strokeWidth={1.15} fill="none">
        <path d={`M ${-length} 0 L ${length} 0`} />
        {feathers('main')}
      </g>
    </g>
  );
}
