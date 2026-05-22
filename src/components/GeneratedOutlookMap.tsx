import { useMemo } from 'react';
import { ComposableMap, Geographies, Geography, Marker } from 'react-simple-maps';
import type { HourSnapshot, RiskCategory, UpperAirVector } from '../types/forecast';
import type { OutlookArtifacts, OutlookArtifactFeatureCollection, ArtifactRiskCategory } from '../types/outlookArtifacts';
import {
  artifactRiskShapesToFeatureCollection,
  artifactRiskToFeatureCollection,
  getArtifactHourTile,
  getArtifactMaxCategory,
} from '../utils/artifactProbabilities';
import { map500mbLines } from '../utils/upperAirLines';
import { map500mbWindVectors } from '../utils/upperAirWind';
import { buildUpperAirIntensitySegments, upperAirLineVisualStyle } from '../utils/upperAirLineStyle';

const STATES_URL = '/us-states-10m.json';

type ArtifactState = 'loading' | 'ready' | 'missing' | 'error' | 'pending' | 'failed';

interface GeneratedOutlookMapProps {
  snapshot: HourSnapshot | null;
  status: ArtifactState;
  artifacts: OutlookArtifacts | null;
  message: string | null;
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
const RISK_BAND_BOUNDARY_STROKE_WIDTH = 1.05;
const RISK_BAND_BOUNDARY_STROKE_OPACITY = 0.78;
const RISK_BAND_SEPARATOR_STROKE_WIDTH = 5.25;
const RISK_BAND_SEPARATOR_STROKE_OPACITY = 0.58;
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

function maxCategory(collection: OutlookArtifactFeatureCollection): ArtifactRiskCategory | undefined {
  return collection.features.reduce<ArtifactRiskCategory | undefined>((best, feature) => {
    const category = feature.properties.category;
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
  if (!collection) return undefined;
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

// Polygons below this threshold render as a dot at typical map scale. Drawing
// a broad separator stroke around them makes halo artifacts, so tiny features
// keep only their thin category boundary.
const MIN_SEPARATOR_FEATURE_AREA_DEG2 = 0.04;

function lowerCategoryFor(category: ArtifactRiskCategory): Exclude<ArtifactRiskCategory, 'NONE' | 'MOD'> | null {
  const normalized = normalizeCategory(category);
  const index = CATEGORY_RAMP.indexOf(normalized);
  if (index <= 0) return null;
  return CATEGORY_RAMP[index - 1];
}

// Build a sibling FeatureCollection of separator strokes sitting one risk-tier
// below each higher band. These draw the lower category's color along each
// inter-band edge without putting strokes directly on the fill layers.
function buildSeparatorStrokeCollection(
  collection: OutlookArtifactFeatureCollection,
): OutlookArtifactFeatureCollection {
  const features: OutlookArtifactFeatureCollection['features'] = [];
  for (const feature of collection.features) {
    const lower = lowerCategoryFor(feature.properties.category);
    if (!lower) continue;
    if (geometryArea(feature.geometry) < MIN_SEPARATOR_FEATURE_AREA_DEG2) continue;
    features.push({
      ...feature,
      properties: { ...feature.properties, category: lower },
    });
  }
  return { type: 'FeatureCollection', features };
}

function riskPolygonsForHour(
  collection: OutlookArtifactFeatureCollection | undefined,
  forecastHour: number | undefined,
): OutlookArtifactFeatureCollection | undefined {
  if (!collection || forecastHour === undefined) return undefined;
  const features = collection.features.filter((feature) => feature.properties.forecastHour === forecastHour);
  if (features.length === 0) return undefined;
  return { ...collection, features };
}

export default function GeneratedOutlookMap({ snapshot, status, artifacts, message }: GeneratedOutlookMapProps) {
  const selectedForecastHour = snapshot?.forecastHour;
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
  const riskBandOutlineCollection = useMemo(
    () => ({
      type: 'FeatureCollection' as const,
      features: renderedCollection.features,
    }),
    [renderedCollection],
  );
  const showRiskBandOutlines = hasGeneratedLayer && !usingTileRisk && riskBandOutlineCollection.features.length > 0;
  const separatorStrokeCollection = useMemo(
    () => buildSeparatorStrokeCollection(renderedCollection),
    [renderedCollection],
  );
  const showSeparatorStrokes = hasGeneratedLayer && !usingTileRisk && separatorStrokeCollection.features.length > 0;
  const renderedMax = maxCategory(renderedCollection);
  const tileMax = getArtifactMaxCategory(artifacts, selectedForecastHour);
  const hasGeneratedArtifact = Boolean(selectedTile || artifactRiskCollection?.features.length || aggregateRiskCollection?.features.length);
  const mapCategory = hasGeneratedArtifact ? renderedMax ?? tileMax : undefined;

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
          HRRR/XGBoost Risk Levels
        </span>
        <div className="flex shrink-0 items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-widest text-paper/70">
            CAT {displayCategory(mapCategory)}
          </span>
        </div>
      </header>

      <div className="outlook-export-map-frame aspect-[16/9] xl:aspect-[2/1] relative overflow-hidden bg-[#fbfbf8]">
        <ComposableMap
          projection="geoAlbers"
          width={900}
          height={520}
          projectionConfig={{
            rotate: [96, 0, 0],
            center: [0, 38],
            parallels: [29.5, 45.5],
            scale: 1000,
          }}
          style={{ width: '100%', height: '100%' }}
        >
          <Geographies geography={STATES_URL}>
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

          {showSeparatorStrokes && (
            <Geographies geography={separatorStrokeCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const rawCategory = separatorStrokeCollection.features[index]?.properties.category ?? 'TSTM';
                  const category = normalizeCategory(rawCategory);
                  const style = LEVEL_STYLE[category];
                  return (
                    <Geography
                      key={`generated-risk-separator-${geo.rsmKey ?? index}-${rawCategory}`}
                      geography={geo}
                      tabIndex={-1}
                      style={{
                        default: {
                          fill: 'none',
                          stroke: style.fill,
                          strokeWidth: RISK_BAND_SEPARATOR_STROKE_WIDTH,
                          strokeOpacity: RISK_BAND_SEPARATOR_STROKE_OPACITY,
                          strokeLinecap: 'round',
                          strokeLinejoin: 'round',
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        hover: {
                          fill: 'none',
                          stroke: style.fill,
                          strokeWidth: RISK_BAND_SEPARATOR_STROKE_WIDTH,
                          strokeOpacity: RISK_BAND_SEPARATOR_STROKE_OPACITY,
                          strokeLinecap: 'round',
                          strokeLinejoin: 'round',
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        pressed: {
                          fill: 'none',
                          stroke: style.fill,
                          strokeWidth: RISK_BAND_SEPARATOR_STROKE_WIDTH,
                          strokeOpacity: RISK_BAND_SEPARATOR_STROKE_OPACITY,
                          strokeLinecap: 'round',
                          strokeLinejoin: 'round',
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

          {hasGeneratedLayer && (
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
                        default: {
                          fill: style.fill,
                          fillOpacity: usingTileRisk ? 0.58 : 0.48,
                          stroke: 'none',
                          strokeWidth: 0,
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        hover: {
                          fill: style.fill,
                          fillOpacity: usingTileRisk ? 0.58 : 0.48,
                          stroke: 'none',
                          strokeWidth: 0,
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        pressed: {
                          fill: style.fill,
                          fillOpacity: usingTileRisk ? 0.58 : 0.48,
                          stroke: 'none',
                          strokeWidth: 0,
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

          {showRiskBandOutlines && (
            <Geographies geography={riskBandOutlineCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const rawCategory = riskBandOutlineCollection.features[index]?.properties.category ?? 'TSTM';
                  const category = normalizeCategory(rawCategory);
                  const style = LEVEL_STYLE[category];
                  return (
                    <Geography
                      key={`generated-risk-outline-${geo.rsmKey ?? index}-${rawCategory}`}
                      geography={geo}
                      tabIndex={-1}
                      style={{
                        default: {
                          fill: 'none',
                          stroke: style.stroke,
                          strokeWidth: RISK_BAND_BOUNDARY_STROKE_WIDTH,
                          strokeOpacity: RISK_BAND_BOUNDARY_STROKE_OPACITY,
                          strokeLinecap: 'round',
                          strokeLinejoin: 'round',
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        hover: {
                          fill: 'none',
                          stroke: style.stroke,
                          strokeWidth: RISK_BAND_BOUNDARY_STROKE_WIDTH,
                          strokeOpacity: RISK_BAND_BOUNDARY_STROKE_OPACITY,
                          strokeLinecap: 'round',
                          strokeLinejoin: 'round',
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        pressed: {
                          fill: 'none',
                          stroke: style.stroke,
                          strokeWidth: RISK_BAND_BOUNDARY_STROKE_WIDTH,
                          strokeOpacity: RISK_BAND_BOUNDARY_STROKE_OPACITY,
                          strokeLinecap: 'round',
                          strokeLinejoin: 'round',
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

          <Geographies geography={STATES_URL}>
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
        </ComposableMap>

        {!hasGeneratedArtifact && (
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

        <div className="absolute bottom-2 left-2 border-[2px] border-ink bg-paper px-2.5 py-2 shadow-retro-sm">
          <div className="font-mono text-[9px] uppercase tracking-[0.22em] text-ink/70 leading-none mb-1.5">
            Generated risk categories
          </div>
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
        </div>

        {artifacts?.metadata && (
          <div className="absolute right-2 bottom-2 border-[2px] border-ink bg-paper px-2.5 py-2 shadow-retro-sm font-mono text-[9px] uppercase tracking-widest text-ink/70">
            <div>{artifacts.metadata.cycle}</div>
            <div>Generated {formatGeneratedAt(artifacts.metadata.generatedAtISO)}</div>
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
