import { useMemo } from 'react';
import { ComposableMap, Geographies, Geography, Marker } from 'react-simple-maps';
import type { ActiveRegion, HourSnapshot } from '../types/forecast';
import {
  HAZARD_CONFIGS,
  getHazardConfig,
  buildHazardBands,
  mergeOuterHazardBands,
  thunderProbability,
  type OutlookHazardKey,
} from '../utils/hazardProbabilityBands';
import { deriveTriplePoint } from '../utils/triplePoint';
import { map500mbLines } from '../utils/upperAirLines';
import { buildUpperAirIntensitySegments, upperAirLineVisualStyle } from '../utils/upperAirLineStyle';
import { buildHazards } from '../utils/hazardEngine';
import { displayOutlookAreas } from '../utils/outlookAreaMotion';

const STATES_URL = '/us-states-10m.json';

interface HazardOutlookMapProps {
  snapshot: HourSnapshot | null;
  hazard: OutlookHazardKey;
  title: string;
  sourceLabel?: string;
  activeRegion?: ActiveRegion;
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

interface TriplePointFeature {
  type: 'Feature';
  properties: { kind: 'cold' | 'warm' | 'dryline' };
  geometry: { type: 'LineString'; coordinates: [number, number][] };
}

export default function HazardOutlookMap({
  snapshot,
  hazard,
  title,
  sourceLabel,
  activeRegion = 'conus',
}: HazardOutlookMapProps) {
  void activeRegion;
  const geoUrl = STATES_URL;
  const projection = 'geoAlbers';
  const projectionConfig = {
    rotate: [96, 0, 0] as [number, number, number],
    center: [0, 38] as [number, number],
    parallels: [29.5, 45.5] as [number, number],
    scale: 1000,
  };

  const cfg = getHazardConfig(hazard);
  const contourHour = snapshot?.forecastHour ?? 0;

  const peakProb = snapshot
    ? hazard === 'thunder'
      ? thunderProbability(snapshot.ingredients)
      : snapshot.hazards[hazard].probability
    : 0;

  const bands = useMemo(
    () => {
      if (!snapshot) return [];

      const areas = displayOutlookAreas(snapshot, contourHour);

      const areaBands = areas.flatMap((area) => {
        const ingredients = { ...snapshot.ingredients, ...(area.ingredients ?? {}) };
        const areaPeakProb = hazard === 'thunder'
          ? thunderProbability(ingredients)
          : area.hazards?.[hazard]?.probability ?? buildHazards(ingredients)[hazard].probability;

        return buildHazardBands(
          area.region,
          hazard,
          areaPeakProb,
          ingredients,
          contourHour,
        );
      });

      return mergeOuterHazardBands(areaBands, hazard, snapshot?.region?.bbox);
    },
    [snapshot, hazard, peakProb, contourHour],
  );

  const featureCollection = useMemo(
    () => {
      const groups = new Map<string, {
        color: string;
        significant: boolean;
        features: Array<{
          type: 'Feature';
          properties: {
            idx: number;
            featureKey: string;
            color: string;
            label: string;
            significant: boolean;
          };
          geometry: { type: 'Polygon'; coordinates: [number, number][][] };
        }>;
      }>();

      bands.forEach((b, i) => {
        const closeRing = (pts: [number, number][]) => [...pts, pts[0]] as [number, number][];
        // Each band is a single filled disk; we stack them largest -> smallest,
        // and the natural SVG paint order makes the smaller (higher-threshold)
        // disks visually overlay the larger ones, producing concentric bands.
        const significant = b.significant === true;
        const key = `${b.threshold}:${significant ? 'sig' : 'base'}:${b.color}`;
        const group = groups.get(key) ?? {
          color: b.color,
          significant,
          features: [],
        };
        group.features.push({
          type: 'Feature' as const,
          properties: {
            idx: i,
            featureKey: `${hazard}-${b.label}-${b.threshold}-${significant ? 'sig' : 'base'}-${i}`,
            color: b.color,
            label: b.label,
            significant,
          },
          geometry: {
            type: 'Polygon' as const,
            coordinates: [closeRing(b.coords)],
          },
        });
        groups.set(key, group);
      });

      return Array.from(groups.entries()).map(([key, group]) => ({
        key,
        color: group.color,
        significant: group.significant,
        collection: {
          type: 'FeatureCollection' as const,
          features: group.features,
        },
      }));
    },
    [bands],
  );

  const upperAirLineCollection = useMemo(
    () => {
      const lines = map500mbLines(snapshot);
      return {
        type: 'FeatureCollection' as const,
        features: lines
        .map((line, idx): UpperAirFeature => {
          const style = upperAirLineVisualStyle(snapshot, idx, lines.length);
          return {
          type: 'Feature',
          properties: { idx, value: line.value, ...style },
          geometry: { type: 'LineString', coordinates: line.coords },
          };
        }),
      };
    },
    [snapshot],
  );

  const upperAirStreakCollection = useMemo(
    () => ({
      type: 'FeatureCollection' as const,
      features: buildUpperAirIntensitySegments(snapshot, map500mbLines(snapshot))
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
    [snapshot],
  );

  const triplePoint = useMemo(
    () => (snapshot ? deriveTriplePoint(snapshot) : null),
    [snapshot],
  );

  const triplePointCollection = useMemo(
    () => ({
      type: 'FeatureCollection' as const,
      features: triplePoint
        ? triplePoint.boundaries.map((boundary): TriplePointFeature => ({
          type: 'Feature',
          properties: { kind: boundary.kind },
          geometry: { type: 'LineString', coordinates: boundary.coords },
        }))
        : [],
    }),
    [triplePoint],
  );

  const peakPct = peakProb >= 0.005 ? `${Math.round(peakProb * 100)}%` : '—';
  const headerTitle = title.replace(/\s+Outlook$/i, '');

  return (
    <div className="outlook-export-map-card border-[3px] border-ink bg-paper shadow-retro flex flex-col">
      <header className="min-h-[40px] border-b-[2px] border-ink bg-ink text-paper px-3 py-2 flex items-center justify-between gap-3 overflow-visible">
        <span className="shrink-0 whitespace-nowrap pr-3 font-display font-extrabold uppercase text-[12px] leading-none tracking-normal">
          {headerTitle}
        </span>
        <div className="font-mono text-[10px] uppercase tracking-widest text-paper/70 shrink-0 flex items-center gap-2">
          {sourceLabel && <span>{sourceLabel}</span>}
          <span>PEAK {peakPct}</span>
        </div>
      </header>
      <div className="outlook-export-map-frame aspect-[16/9] xl:aspect-[2/1] relative overflow-hidden bg-[#fbfbf8]">
        <ComposableMap
          // NB: we use plain `geoAlbers` (not `geoAlbersUsa`) because the
          // composite projection prepends a full-canvas clip rectangle to
          // every polygon's path, which combined with default nonzero
          // fill-rule made our small probability ellipses paint as if the
          // whole canvas were filled (with the ellipse acting as a hole).
          projection={projection}
          width={900}
          height={520}
          projectionConfig={projectionConfig}
          style={{ width: '100%', height: '100%' }}
        >
          {/* Base CONUS states (AK/HI excluded - geoAlbers is CONUS-only) */}
          <Geographies geography={geoUrl}>
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

          {/* 500 mb geopotential-height contours: real HRRR HGT at 500 mb only. */}
          {upperAirLineCollection.features.length > 0 && (
            <Geographies geography={upperAirLineCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`h500-${geo.rsmKey ?? index}`}
                    geography={geo}
                    style={{
                      default: {
                        fill: 'none',
                        stroke: '#707070',
                        strokeWidth: 0.85,
                        strokeOpacity: 0.42,
                        outline: 'none',
                      },
                      hover: {
                        fill: 'none',
                        stroke: '#707070',
                        strokeWidth: 0.85,
                        strokeOpacity: 0.42,
                        outline: 'none',
                      },
                      pressed: {
                        fill: 'none',
                        stroke: '#707070',
                        strokeWidth: 0.85,
                        strokeOpacity: 0.42,
                        outline: 'none',
                      },
                    }}
                  />
                ))
              }
            </Geographies>
          )}

          {/* Derived frontal triple point: cold front + warm front + dryline
              intersection. This is a diagnostic focus marker, not an observed
              surface-analysis product. */}
          {triplePointCollection.features.length > 0 && (
            <Geographies geography={triplePointCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const kind = geo.properties.kind as TriplePointFeature['properties']['kind'];
                  const stroke = kind === 'cold' ? '#2278d4' : kind === 'warm' ? '#d63232' : '#8b5a2b';
                  const dash = kind === 'dryline' ? '5 3' : undefined;
                  return (
                    <Geography
                      key={`tp-front-${kind}-${index}`}
                      geography={geo}
                      style={{
                        default: { fill: 'none', stroke, strokeWidth: 2.2, strokeOpacity: 0.78, strokeDasharray: dash, outline: 'none' },
                        hover: { fill: 'none', stroke, strokeWidth: 2.2, strokeOpacity: 0.78, strokeDasharray: dash, outline: 'none' },
                        pressed: { fill: 'none', stroke, strokeWidth: 2.2, strokeOpacity: 0.78, strokeDasharray: dash, outline: 'none' },
                      }}
                    />
                  );
                })
              }
            </Geographies>
          )}

          {/* Probability bands */}
          {featureCollection.map((group) => {
            const opacity = group.significant ? 0.72 : 0.56;
            const stroke = group.significant ? '#cc1f1f' : '#111111';
            const strokeWidth = group.significant ? 1.2 : 0.8;
            const dash = group.significant ? '3 2' : undefined;
            return (
              <g key={`band-group-${group.key}`} opacity={opacity}>
                <Geographies geography={group.collection}>
                  {({ geographies }) =>
                geographies.map((geo, index) => (
                      <Geography
                        key={`band-${group.key}-${geo.rsmKey ?? index}`}
                        geography={geo}
                        tabIndex={-1}
                        style={{
                          default: { fill: group.color, fillOpacity: 1, stroke, strokeWidth, strokeDasharray: dash, outline: 'none', pointerEvents: 'none' },
                          hover:   { fill: group.color, fillOpacity: 1, stroke, strokeWidth, strokeDasharray: dash, outline: 'none', pointerEvents: 'none' },
                          pressed: { fill: group.color, fillOpacity: 1, stroke, strokeWidth, strokeDasharray: dash, outline: 'none', pointerEvents: 'none' },
                        }}
                      />
                    ))
                  }
                </Geographies>
              </g>
            );
          })}

          <Geographies geography={geoUrl}>
            {({ geographies }) =>
              geographies.map((geo) => (
                <Geography
                  key={`state-outline-${geo.rsmKey}`}
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

          {upperAirLineCollection.features.length > 0 && (
            <Geographies geography={upperAirLineCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`h500-top-${geo.rsmKey ?? index}`}
                    geography={geo}
                    style={{
                      default: {
                        fill: 'none',
                        stroke: '#ffffff',
                        strokeWidth: geo.properties.haloWidth as number,
                        strokeOpacity: geo.properties.haloOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      hover: {
                        fill: 'none',
                        stroke: '#ffffff',
                        strokeWidth: geo.properties.haloWidth as number,
                        strokeOpacity: geo.properties.haloOpacity as number,
                        strokeLinecap: 'round',
                        strokeLinejoin: 'round',
                        outline: 'none',
                      },
                      pressed: {
                        fill: 'none',
                        stroke: '#ffffff',
                        strokeWidth: geo.properties.haloWidth as number,
                        strokeOpacity: geo.properties.haloOpacity as number,
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

          {upperAirLineCollection.features.length > 0 && (
            <Geographies geography={upperAirLineCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`h500-intensity-${geo.rsmKey ?? index}`}
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

          {upperAirStreakCollection.features.length > 0 && (
            <Geographies geography={upperAirStreakCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => (
                  <Geography
                    key={`h500-streak-${geo.rsmKey ?? index}`}
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

          {triplePoint && (
            <>
              {triplePoint.boundaries.flatMap((boundary) => boundary.symbolCoords.map((coords, index) => (
                <Marker key={`tp-symbol-${boundary.kind}-${index}`} coordinates={coords}>
                  {boundary.kind === 'warm' && (
                    <circle r={2.7} fill="#d63232" stroke="#111111" strokeWidth={0.6} />
                  )}
                  {boundary.kind === 'cold' && (
                    <path d="M 0 -3.7 L 3.5 2.8 L -3.5 2.8 Z" fill="#2278d4" stroke="#111111" strokeWidth={0.6} />
                  )}
                  {boundary.kind === 'dryline' && (
                    <path d="M -3.4 0 C -1.8 -3.2 1.8 -3.2 3.4 0 C 1.8 3.2 -1.8 3.2 -3.4 0 Z" fill="#8b5a2b" stroke="#111111" strokeWidth={0.6} />
                  )}
                </Marker>
              )))}
            </>
          )}
        </ComposableMap>

        {/* Legend overlay (bottom-left) */}
        <div className="absolute bottom-1.5 left-1.5 border-[2px] border-ink bg-paper px-2 py-1 shadow-retro-sm">
          <div className="font-mono text-[8px] uppercase tracking-[0.2em] text-ink/70 leading-none mb-1">
            {title}
          </div>
          <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
            {cfg.thresholds.map((t, i) => (
              <div key={t} className="flex items-center gap-1 font-mono text-[9px] font-bold leading-none">
                <span
                  className="inline-block w-3 h-2 border-[1.5px] border-ink shrink-0"
                  style={{ background: cfg.colors[i] }}
                  aria-hidden
                />
                <span className="text-ink">{cfg.labels[i]}</span>
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

        {/* "No risk" hint when peakProb is below the lowest threshold */}
        {snapshot && bands.length === 0 && (
          <div className="absolute top-1.5 right-1.5 border-[2px] border-ink bg-paper px-2 py-1 shadow-retro-sm font-mono text-[10px] uppercase tracking-widest">
            BELOW THRESHOLD
          </div>
        )}
      </div>
    </div>
  );
}
