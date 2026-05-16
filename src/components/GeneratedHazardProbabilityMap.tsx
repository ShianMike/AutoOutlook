import { useMemo } from 'react';
import { ComposableMap, Geographies, Geography, Marker } from 'react-simple-maps';
import type { HourSnapshot, UpperAirVector } from '../types/forecast';
import type { OutlookArtifacts } from '../types/outlookArtifacts';
import {
  artifactProbabilityShapesToFeatureCollection,
  artifactProbabilityToFeatureCollection,
  artifactThunderToFeatureCollection,
  getArtifactHazardPeak,
  getArtifactHazardPeakLocation,
  getArtifactHourTile,
  getArtifactThunderCoverage,
  type ArtifactHazardKey,
  type GeneratedArtifactHazardKey,
} from '../utils/artifactProbabilities';
import { HAZARD_CONFIGS, buildArtifactSigBlob } from '../utils/hazardProbabilityBands';
import { map500mbLines } from '../utils/upperAirLines';
import { map500mbWindVectors } from '../utils/upperAirWind';
import { buildUpperAirIntensitySegments, upperAirLineVisualStyle } from '../utils/upperAirLineStyle';
import type { ArtifactStatus } from '../hooks/useOutlookArtifacts';

const STATES_URL = '/us-states-10m.json';

export type GeneratedHazardKey = GeneratedArtifactHazardKey;

interface GeneratedHazardProbabilityMapProps {
  snapshot: HourSnapshot | null;
  hazard: GeneratedHazardKey;
  title: string;
  artifacts: OutlookArtifacts | null;
  status: ArtifactStatus;
}

export function hasGeneratedHazardTile(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
  _status?: ArtifactStatus,
): boolean {
  if (forecastHour === undefined) return false;
  return Boolean(artifacts?.probabilityTiles?.hours.some((hour) => hour.forecastHour === forecastHour));
}

export default function GeneratedHazardProbabilityMap({
  snapshot,
  hazard,
  title,
  artifacts,
  status,
}: GeneratedHazardProbabilityMapProps) {
  const forecastHour = snapshot?.forecastHour;
  const tile = useMemo(() => getArtifactHourTile(artifacts, forecastHour), [artifacts, forecastHour]);
  const displayForecastHour = tile?.forecastHour ?? forecastHour;
  const cfg = HAZARD_CONFIGS[hazard];
  const vectorFeatureCollection = useMemo(
    () => artifactProbabilityShapesToFeatureCollection(tile, hazard),
    [tile, hazard],
  );
  const featureCollection = useMemo(
    () => vectorFeatureCollection ?? (
      hazard === 'thunder'
        ? artifactThunderToFeatureCollection(tile)
        : artifactProbabilityToFeatureCollection(tile, hazard)
    ),
    [tile, hazard, vectorFeatureCollection],
  );
  const usingVectorProbability = Boolean(vectorFeatureCollection);
  // SIG (significant severe) overlay: a SINGLE smooth blob anchored at the
  // peak hazard cell and OFFSET along a per-hazard axis, so the SIG core has
  // its own location instead of perfectly overlaying every ENH+ cell.
  // Mirrors the rule-based HazardOutlookMap's offset SIG core so the two
  // map paths render the same visual signature.
  const sigFeatureCollection = useMemo(() => {
    if (cfg.sigThreshold === undefined) {
      return { type: 'FeatureCollection' as const, features: [] };
    }
    const peakLocation = getArtifactHazardPeakLocation(
      artifacts,
      displayForecastHour,
      hazard as ArtifactHazardKey,
    );
    if (!peakLocation || peakLocation.probability < cfg.sigThreshold) {
      return { type: 'FeatureCollection' as const, features: [] };
    }
    const blob = buildArtifactSigBlob(
      hazard,
      peakLocation.lat,
      peakLocation.lon,
      displayForecastHour ?? 0,
      peakLocation.probability,
    );
    if (!blob || blob.coords.length < 4) {
      return { type: 'FeatureCollection' as const, features: [] };
    }
    const ring = [...blob.coords, blob.coords[0]] as [number, number][];
    return {
      type: 'FeatureCollection' as const,
      features: [{
        type: 'Feature' as const,
        properties: { kind: 'sig' as const },
        geometry: { type: 'Polygon' as const, coordinates: [ring] },
      }],
    };
  }, [artifacts, displayForecastHour, hazard, cfg.sigThreshold]);
  const peakProb = hazard === 'thunder'
    ? getArtifactThunderCoverage(tile) ?? 0
    : getArtifactHazardPeak(artifacts, displayForecastHour, hazard as ArtifactHazardKey) ?? 0;
  const peakPct = peakProb >= 0.005 ? `${Math.round(peakProb * 100)}%` : '--';
  const metricLabel = hazard === 'thunder' ? `COVER ${peakPct}` : `PEAK ${peakPct}`;
  const headerTitle = title.replace(/\s+Outlook$/i, '');
  const legendItems = cfg.thresholds.map((threshold, i) => ({ label: cfg.labels[i], color: cfg.colors[i], threshold }));
  const upperAirLines = useMemo(() => map500mbLines(snapshot), [snapshot]);
  const upperAirLineCollection = useMemo(
    () => ({
      type: 'FeatureCollection' as const,
      features: upperAirLines.map((line, idx) => {
        const style = upperAirLineVisualStyle(snapshot, idx, upperAirLines.length);
        return {
          type: 'Feature' as const,
          properties: { idx, value: line.value, ...style },
          geometry: { type: 'LineString' as const, coordinates: line.coords },
        };
      }),
    }),
    [snapshot, upperAirLines],
  );
  const upperAirStreakCollection = useMemo(
    () => ({
      type: 'FeatureCollection' as const,
      features: buildUpperAirIntensitySegments(snapshot, upperAirLines).map((segment, idx) => ({
        type: 'Feature' as const,
        properties: {
          idx,
          stroke: segment.stroke,
          strokeWidth: segment.strokeWidth,
          strokeOpacity: segment.strokeOpacity,
        },
        geometry: { type: 'LineString' as const, coordinates: segment.coords },
      })),
    }),
    [snapshot, upperAirLines],
  );
  const windVectors = useMemo(() => map500mbWindVectors(snapshot, 55), [snapshot]);
  const hasUpperAirOverlay = snapshot?.upperAirOverlay?.domain === 'CONUS' && snapshot.upperAirOverlay.level === '500mb';

  return (
    <div className="outlook-export-map-card border-[3px] border-ink bg-paper shadow-retro flex flex-col">
      <header className="min-h-[40px] border-b-[2px] border-ink bg-ink text-paper px-3 py-2 flex items-center justify-between gap-3 overflow-visible">
        <span className="shrink-0 whitespace-nowrap pr-3 font-display font-extrabold uppercase text-[12px] leading-none tracking-normal">
          {headerTitle}
        </span>
        <div className="flex shrink-0 items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-widest text-paper/70">
            {metricLabel}
          </span>
        </div>
      </header>
      <div className="outlook-export-map-frame aspect-[5/3] relative overflow-hidden bg-paper md:aspect-[19/10] xl:aspect-[43/20]">
        <ComposableMap
          projection="geoAlbers"
          width={500}
          height={300}
          projectionConfig={{
            rotate: [96, 0, 0],
            center: [0, 38],
            parallels: [29.5, 45.5],
            scale: 760,
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
                    default: { fill: '#efe6cf', stroke: '#888888', strokeWidth: 0.5, outline: 'none' },
                    hover:   { fill: '#efe6cf', stroke: '#888888', strokeWidth: 0.5, outline: 'none' },
                    pressed: { fill: '#efe6cf', stroke: '#888888', strokeWidth: 0.5, outline: 'none' },
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
                    key={`artifact-h500-base-${geo.rsmKey ?? index}`}
                    geography={geo}
                    style={{
                      default: { fill: 'none', stroke: '#707070', strokeWidth: 0.85, strokeOpacity: 0.28, outline: 'none' },
                      hover:   { fill: 'none', stroke: '#707070', strokeWidth: 0.85, strokeOpacity: 0.28, outline: 'none' },
                      pressed: { fill: 'none', stroke: '#707070', strokeWidth: 0.85, strokeOpacity: 0.28, outline: 'none' },
                    }}
                  />
                ))
              }
            </Geographies>
          )}

          {featureCollection.features.length > 0 && (
            <Geographies geography={featureCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`artifact-prob-${hazard}-${geo.rsmKey ?? index}`}
                    geography={geo}
                    tabIndex={-1}
                    style={{
                      default: {
                        fill: geo.properties.color as string,
                        fillOpacity: usingVectorProbability ? 0.50 : 0.58,
                        stroke: '#111111',
                        strokeWidth: usingVectorProbability ? 0.7 : 0.08,
                        outline: 'none',
                        pointerEvents: 'none',
                      },
                      hover: {
                        fill: geo.properties.color as string,
                        fillOpacity: usingVectorProbability ? 0.50 : 0.58,
                        stroke: '#111111',
                        strokeWidth: usingVectorProbability ? 0.7 : 0.08,
                        outline: 'none',
                        pointerEvents: 'none',
                      },
                      pressed: {
                        fill: geo.properties.color as string,
                        fillOpacity: usingVectorProbability ? 0.50 : 0.58,
                        stroke: '#111111',
                        strokeWidth: usingVectorProbability ? 0.7 : 0.08,
                        outline: 'none',
                        pointerEvents: 'none',
                      },
                    }}
                  />
                ))
              }
            </Geographies>
          )}

          {sigFeatureCollection.features.length > 0 && (
            <Geographies geography={sigFeatureCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const sigStyle = {
                    fill: '#1a1a1a',
                    fillOpacity: 0.58,
                    stroke: '#cc1f1f',
                    strokeWidth: 1.1,
                    strokeDasharray: '3 2',
                    outline: 'none',
                    pointerEvents: 'none' as const,
                  };
                  return (
                    <Geography
                      key={`artifact-sig-${hazard}-${geo.rsmKey ?? index}`}
                      geography={geo}
                      tabIndex={-1}
                      style={{
                        default: sigStyle,
                        hover: sigStyle,
                        pressed: sigStyle,
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
                  key={`generated-hazard-state-outline-${geo.rsmKey}`}
                  geography={geo}
                  style={{
                    default: { fill: 'none', stroke: '#777777', strokeWidth: 0.55, strokeOpacity: 0.75, outline: 'none' },
                    hover:   { fill: 'none', stroke: '#777777', strokeWidth: 0.55, strokeOpacity: 0.75, outline: 'none' },
                    pressed: { fill: 'none', stroke: '#777777', strokeWidth: 0.55, strokeOpacity: 0.75, outline: 'none' },
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
                    key={`artifact-h500-top-${geo.rsmKey ?? index}`}
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
                    key={`artifact-h500-streak-${geo.rsmKey ?? index}`}
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
            <Marker key={`generated-hazard-wind-vector-top-${idx}`} coordinates={[vector.lon, vector.lat]}>
              <WindBarb vector={vector} top />
            </Marker>
          ))}
        </ComposableMap>

        <div className="absolute bottom-1.5 left-1.5 border-[2px] border-ink bg-paper px-2 py-1 shadow-retro-sm">
          <div className="font-mono text-[8px] uppercase tracking-[0.2em] text-ink/70 leading-none mb-1">
            {title}
          </div>
          <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
            {legendItems.map((item) => (
              <div key={item.label} className="flex items-center gap-1 font-mono text-[9px] font-bold leading-none">
                <span
                  className="inline-block w-3 h-2 border-[1.5px] border-ink shrink-0"
                  style={{ background: item.color }}
                  aria-hidden
                />
                <span className="text-ink">{item.label}</span>
              </div>
            ))}
            {cfg.sigThreshold !== undefined && (
              <div className="flex items-center gap-1 font-mono text-[9px] font-bold leading-none">
                <span
                  className="inline-block w-3 h-2 border-[1.5px] shrink-0"
                  style={{ background: '#1a1a1a', borderColor: '#cc1f1f' }}
                  aria-hidden
                />
                <span className="text-ink">SIG</span>
              </div>
            )}
          </div>
        </div>

        {featureCollection.features.length === 0 && (
          <div className="absolute top-1.5 right-1.5 border-[2px] border-ink bg-paper px-2 py-1 shadow-retro-sm font-mono text-[10px] uppercase tracking-widest">
            BELOW THRESHOLD
          </div>
        )}
      </div>
    </div>
  );
}

function WindBarb({ vector, top = false }: { vector: UpperAirVector; top?: boolean }) {
  if (!top || vector.speedKt < 22) return null;
  const length = 7;
  const featherCount = Math.max(1, Math.min(4, Math.round(vector.speedKt / 22)));
  const angleDeg = (Math.atan2(-vector.vKt, vector.uKt) * 180 / Math.PI) + 180;
  const opacity = 0.30;
  const stroke = '#50565c';
  const halo = '#ffffff';
  const feathers = (prefix: string) => Array.from({ length: featherCount }, (_, i) => {
    const x = length - i * 1.8;
    return <path key={`${prefix}-${i}`} d={`M ${x} 0 L ${x - 2.5} 3.3`} />;
  });

  return (
    <g transform={`rotate(${angleDeg})`} opacity={opacity} strokeLinecap="square">
      <g stroke={halo} strokeWidth={1.8} fill="none" opacity={0.36}>
        <path d={`M ${-length} 0 L ${length} 0`} />
        {feathers('halo')}
      </g>
      <g stroke={stroke} strokeWidth={0.9} fill="none">
        <path d={`M ${-length} 0 L ${length} 0`} />
        {feathers('main')}
      </g>
    </g>
  );
}
