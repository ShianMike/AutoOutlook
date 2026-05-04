import { useMemo } from 'react';
import { ComposableMap, Geographies, Geography, Marker } from 'react-simple-maps';
import type { HourSnapshot, RiskCategory, UpperAirVector } from '../types/forecast';
import type { OutlookArtifacts, OutlookArtifactFeatureCollection, ArtifactRiskCategory } from '../types/outlookArtifacts';
import { map500mbLines } from '../utils/upperAirLines';
import { map500mbWindVectors } from '../utils/upperAirWind';
import { buildUpperAirIntensitySegments, upperAirLineVisualStyle } from '../utils/upperAirLineStyle';

const STATES_URL = '/us-states-10m.json';

type ArtifactState = 'loading' | 'ready' | 'missing' | 'error';

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

function normalizeCategory(category: ArtifactRiskCategory): Exclude<ArtifactRiskCategory, 'NONE' | 'MOD'> {
  return category === 'MOD' ? 'MDT' : category === 'NONE' ? 'TSTM' : category;
}

function displayCategory(category: ArtifactRiskCategory | RiskCategory | undefined): string {
  if (!category) return '--';
  return category === 'MOD' ? 'MDT' : category;
}

function filterHourCollection(
  collection: OutlookArtifactFeatureCollection | undefined,
  forecastHour: number | undefined,
): OutlookArtifactFeatureCollection {
  if (!collection || forecastHour === undefined) return { type: 'FeatureCollection', features: [] };
  const features = collection.features
    .filter((feature) => feature.properties.forecastHour === forecastHour)
    .filter((feature) => feature.properties.category !== 'NONE');
  return { type: 'FeatureCollection', features };
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

export default function GeneratedOutlookMap({ snapshot, status, artifacts, message }: GeneratedOutlookMapProps) {
  const selectedForecastHour = snapshot?.forecastHour;
  const selectedCollection = useMemo(
    () => filterHourCollection(artifacts?.riskPolygons, selectedForecastHour),
    [artifacts, selectedForecastHour],
  );
  const aggregateCollection = artifacts?.aggregateRiskPolygons;
  const renderedCollection = selectedCollection.features.length > 0
    ? selectedCollection
    : aggregateCollection ?? selectedCollection;
  const renderedMax = maxCategory(renderedCollection);
  const mapCategory = renderedMax ?? snapshot?.outlook.category;

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

  const windVectors = useMemo(() => map500mbWindVectors(snapshot), [snapshot]);
  const hasGeneratedLayer = renderedCollection.features.length > 0;
  const hasUpperAirOverlay = hasGeneratedLayer && snapshot?.upperAirOverlay?.domain === 'CONUS' && snapshot.upperAirOverlay.level === '500mb';

  return (
    <div className="border-[3px] border-ink bg-paper shadow-retro flex flex-col">
      <header className="border-b-[2px] border-ink bg-ink text-paper px-3 py-1.5 flex items-center justify-between gap-2">
        <span className="font-display font-extrabold uppercase text-[13px] tracking-wider truncate">
          HRRR/XGBoost Risk Levels
        </span>
        <span className="font-mono text-[10px] uppercase tracking-widest text-paper/70 shrink-0">
          CAT {displayCategory(mapCategory)}
        </span>
      </header>

      <div className="aspect-[16/9] xl:aspect-[2/1] relative overflow-hidden bg-[#fbfbf8]">
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

          {hasGeneratedLayer && (
            <Geographies geography={renderedCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const rawCategory = renderedCollection.features[index]?.properties.category ?? 'TSTM';
                  const category = normalizeCategory(rawCategory);
                  const style = LEVEL_STYLE[category];
                  return (
                    <Geography
                      key={`generated-risk-${geo.rsmKey ?? index}-${rawCategory}`}
                      geography={geo}
                      tabIndex={-1}
                      style={{
                        default: {
                          fill: style.fill,
                          fillOpacity: 0.5,
                          stroke: style.stroke,
                          strokeWidth: 2.2,
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        hover: {
                          fill: style.fill,
                          fillOpacity: 0.5,
                          stroke: style.stroke,
                          strokeWidth: 2.2,
                          outline: 'none',
                          pointerEvents: 'none',
                        },
                        pressed: {
                          fill: style.fill,
                          fillOpacity: 0.5,
                          stroke: style.stroke,
                          strokeWidth: 2.2,
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

        {!hasGeneratedLayer && (
          <div className="absolute inset-4 flex items-center justify-center">
            <div className="max-w-[520px] border-[3px] border-ink bg-paper/95 p-4 shadow-retro">
              <div className="font-display text-[14px] font-extrabold uppercase tracking-wider">
                Generated outlook layer unavailable
              </div>
              <p className="mt-2 font-mono text-[11px] leading-relaxed text-ink/70">
                {status === 'loading'
                  ? 'Loading HRRR/XGBoost outlook artifacts…'
                  : message ?? 'Run the HRRR/XGBoost artifact pipeline to publish risk polygons for this map.'}
              </p>
            </div>
          </div>
        )}

        <div className="absolute bottom-2 left-2 border-[2px] border-ink bg-paper/95 px-2.5 py-2 shadow-retro-sm">
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
          <div className="absolute right-2 bottom-2 border-[2px] border-ink bg-paper/95 px-2.5 py-2 shadow-retro-sm font-mono text-[9px] uppercase tracking-widest text-ink/70">
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
  const length = 12;
  const featherCount = Math.max(1, Math.min(4, Math.round(vector.speedKt / 22)));
  const angleDeg = (Math.atan2(-vector.vKt, vector.uKt) * 180 / Math.PI) + 180;
  const opacity = 0.82;
  const stroke = '#50565c';
  const halo = '#ffffff';
  const feathers = (prefix: string) => Array.from({ length: featherCount }, (_, i) => {
    const x = length - i * 3.0;
    return <path key={`${prefix}-${i}`} d={`M ${x} 0 L ${x - 4.0} 5.2`} />;
  });

  return (
    <g transform={`rotate(${angleDeg})`} opacity={opacity} strokeLinecap="square">
      <g stroke={halo} strokeWidth={3.2} fill="none" opacity={0.72}>
        <path d={`M ${-length} 0 L ${length} 0`} />
        {feathers('halo')}
      </g>
      <g stroke={stroke} strokeWidth={1.7} fill="none">
        <path d={`M ${-length} 0 L ${length} 0`} />
        {feathers('main')}
      </g>
    </g>
  );
}
