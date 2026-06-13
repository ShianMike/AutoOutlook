import { Fragment, useId, useMemo, useState } from 'react';
import { ComposableMap, Geographies, Geography, Marker } from 'react-simple-maps';
import type { ActiveRegion, HourSnapshot, UpperAirVector } from '../types/forecast';
import type { OutlookArtifacts, OutlookProbabilityShapeFeatureCollection, SpcStormReport } from '../types/outlookArtifacts';
import type { SpcComparisonMode } from './GeneratedOutlookMap';
import {
  artifactCigShapesToFeatureCollection,
  artifactProbabilityShapesToFeatureCollection,
  artifactProbabilityToFeatureCollection,
  getArtifactHazardPeak,
  getArtifactHazardPeakLocation,
  getArtifactHourTile,
  getArtifactThunderPeak,
  measureArtifactBandRadius,
  type ArtifactProbabilityFeature,
  type ArtifactProbabilityFeatureCollection,
  type ArtifactHazardKey,
  type GeneratedArtifactHazardKey,
} from '../utils/artifactProbabilities';
import { HAZARD_CONFIGS, getHazardConfig, buildArtifactSigBlob } from '../utils/hazardProbabilityBands';
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
  activeRegion?: ActiveRegion;
  stormReportsMode?: 'none' | 'all' | 'tornado' | 'hail' | 'wind';
  stormReports?: SpcStormReport[];
  comparisonMode?: SpcComparisonMode;
  spcHazardProbabilityShapes?: OutlookProbabilityShapeFeatureCollection | null;
  cigOverlayEnabled?: boolean;
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
  activeRegion = 'conus',
  stormReportsMode = 'none',
  stormReports = [],
  comparisonMode = 'auto',
  spcHazardProbabilityShapes = null,
  cigOverlayEnabled = false,
}: GeneratedHazardProbabilityMapProps) {
  void activeRegion;
  const geoUrl = STATES_URL;
  const usClipId = `us-land-clip-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const projection = 'geoAlbers';
  const projectionConfig = {
    rotate: [96, 0, 0] as [number, number, number],
    center: [0, 38] as [number, number],
    parallels: [29.5, 45.5] as [number, number],
    scale: 1000,
  };

  const forecastHour = snapshot?.forecastHour;
  const tile = useMemo(() => getArtifactHourTile(artifacts, forecastHour), [artifacts, forecastHour]);
  const displayForecastHour = tile?.forecastHour ?? forecastHour;
  const cfg = getHazardConfig(hazard);
  const vectorFeatureCollection = useMemo(
    () => artifactProbabilityShapesToFeatureCollection(tile, hazard),
    [tile, hazard],
  );
  const tileFeatureCollection = useMemo(
    () => (
      hazard === 'thunder'
        ? { type: 'FeatureCollection' as const, features: [] }
        : artifactProbabilityToFeatureCollection(tile, hazard)
    ),
    [tile, hazard],
  );
  const featureCollection = useMemo(
    () => vectorFeatureCollection ?? tileFeatureCollection,
    [tileFeatureCollection, vectorFeatureCollection],
  );
  const cigCollection = useMemo(
    () => (hazard === 'thunder' ? undefined : artifactCigShapesToFeatureCollection(tile, hazard as ArtifactHazardKey)),
    [tile, hazard],
  );
  const [showCigOverlay, setShowCigOverlay] = useState(true);
  const hasCigOverlay = cigOverlayEnabled && Boolean(cigCollection?.features.length);
  const visibleCigCollection = hasCigOverlay && showCigOverlay ? cigCollection : undefined;
  const visibleCigHatchCollection = useMemo(
    () => visibleCigCollection
      ? {
          ...visibleCigCollection,
          features: visibleCigCollection.features.map((feature) => ({
            ...feature,
            geometry: feature.geometry,
          })),
        }
      : undefined,
    [visibleCigCollection],
  );
  const spcFeatureCollection = useMemo(
    () => spcHazardShapesToFeatureCollection(spcHazardProbabilityShapes, hazard),
    [hazard, spcHazardProbabilityShapes],
  );
  const hasSpcHazardLayer = hazard !== 'thunder' && spcFeatureCollection.features.length > 0;
  const effectiveComparisonMode = hasSpcHazardLayer ? comparisonMode : 'auto';
  const showAutoLayer = effectiveComparisonMode !== 'spc';
  const showSpcFillLayer = effectiveComparisonMode === 'spc';
  const showSpcOutlineLayer = effectiveComparisonMode === 'overlay';
  const usingVectorProbability = Boolean(vectorFeatureCollection);
  const vectorPeakProbability = useMemo(
    () => vectorFeatureCollection?.features.reduce((peak, feature) => {
      const probability = Number(feature.properties.probability);
      return Number.isFinite(probability) ? Math.max(peak, probability) : peak;
    }, 0),
    [vectorFeatureCollection],
  );
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
    // Measure the ACTUAL 5% (brown band) radius from the artifact's
    // probability grid — the largest in-band circle around the peak cell.
    // This is the authoritative containment bound the SIG must fit inside,
    // and it's typically tighter than the formula-based estimate the SIG
    // builder would compute on its own (because the real probability
    // region can be smaller / asymmetric vs the idealized ellipse).
    const measuredBandRadius = (hazard !== 'thunder')
      ? measureArtifactBandRadius(tile, hazard as ArtifactHazardKey, peakLocation.lat, peakLocation.lon, 0.05)
      : undefined;
    // Feed the active hour's ingredients + region into the SIG generator so
    // the blob morphs with the actual environmental profile (CAPE / shear /
    // capStrength / stormMode / frontSignal / STP / SCP) instead of relying
    // solely on a synthetic motion clock. Same morphHarmonics +
    // ingredientAspect + ingredientTilt logic the rule-based bands use.
    const blob = buildArtifactSigBlob(
      hazard,
      peakLocation.lat,
      peakLocation.lon,
      displayForecastHour ?? 0,
      peakLocation.probability,
      snapshot?.ingredients,
      snapshot?.region,
      measuredBandRadius,
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
  }, [artifacts, tile, displayForecastHour, hazard, cfg.sigThreshold, snapshot?.ingredients, snapshot?.region]);
  const peakProb = hazard === 'thunder'
    ? Math.max(getArtifactThunderPeak(tile) ?? 0, vectorPeakProbability ?? 0)
    : getArtifactHazardPeak(artifacts, displayForecastHour, hazard as ArtifactHazardKey) ?? vectorPeakProbability ?? 0;
  const spcPeakProb = useMemo(
    () => spcFeatureCollection.features.reduce((peak, feature) => {
      const probability = Number(feature.properties.probability);
      return Number.isFinite(probability) ? Math.max(peak, probability) : peak;
    }, 0),
    [spcFeatureCollection],
  );
  const peakPct = formatProbabilityMetric(
    showSpcFillLayer ? spcPeakProb : peakProb,
    cfg.thresholds[0],
  );
  const metricLabel = hazard === 'thunder'
    ? `COVER ${peakPct}`
    : `${showSpcFillLayer ? 'SPC' : 'PEAK'} ${peakPct}`;
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
          {hasCigOverlay && showAutoLayer && (
            <button
              type="button"
              onClick={() => setShowCigOverlay((value) => !value)}
              aria-pressed={showCigOverlay}
              title={showCigOverlay ? 'Hide CIG overlay' : 'Show CIG overlay'}
              className={[
                'outlook-export-hide retro-button min-h-6 gap-1.5 px-2 py-1 text-[10px] leading-none tracking-wider',
                showCigOverlay
                  ? 'bg-signal-amber text-ink translate-x-[2px] translate-y-[2px] shadow-[1px_1px_0_0_#111111] hover:bg-signal-amber hover:text-ink'
                  : 'bg-paper text-ink hover:bg-signal-amber hover:text-ink',
              ].join(' ')}
            >
              CIG
            </button>
          )}
          <span className="font-mono text-[10px] uppercase tracking-widest text-paper/70">
            {metricLabel}
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
          <defs>
            <pattern id="generated-hazard-cig" patternUnits="userSpaceOnUse" width="22" height="22">
              <path d="M-5 22 L22 -5 M6 27 L27 6" stroke="#111111" strokeWidth="1.05" strokeOpacity={0.74} />
            </pattern>
            <mask id={usClipId} maskUnits="userSpaceOnUse" x="0" y="0" width="900" height="520">
              <rect x="0" y="0" width="900" height="520" fill="#000000" />
              <Geographies geography={geoUrl}>
                {({ geographies }) =>
                  geographies.map((geo) => (
                    <Geography
                      key={`us-clip-${geo.rsmKey}`}
                      geography={geo}
                      style={{
                        default: { fill: '#ffffff', stroke: '#ffffff', strokeWidth: 1.2, outline: 'none' },
                        hover: { fill: '#ffffff', stroke: '#ffffff', strokeWidth: 1.2, outline: 'none' },
                        pressed: { fill: '#ffffff', stroke: '#ffffff', strokeWidth: 1.2, outline: 'none' },
                      }}
                    />
                  ))
                }
              </Geographies>
            </mask>
          </defs>
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

          {showAutoLayer && featureCollection.features.length > 0 && (
            <g mask={`url(#${usClipId})`}>
            <Geographies geography={featureCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const fill = geo.properties.color as string;
                  const featureKey = `${hazard}-${geo.rsmKey ?? index}`;
                  return (
                    <Fragment key={`artifact-prob-layer-${featureKey}`}>
                      <Geography
                        key={`artifact-prob-${featureKey}`}
                        geography={geo}
                        tabIndex={-1}
                        style={{
                          // Probability fill opacity raised to 0.80 / 0.88
                          // so bands read as solid colors. Bands merge through
                          // color contrast alone since all boundary strokes removed.
                          default: {
                            fill,
                            fillOpacity: usingVectorProbability ? 0.80 : 0.88,
                            stroke: 'none',
                            strokeWidth: 0,
                            strokeOpacity: 0,
                            outline: 'none',
                            pointerEvents: 'none',
                          },
                          hover: {
                            fill,
                            fillOpacity: usingVectorProbability ? 0.80 : 0.88,
                            stroke: 'none',
                            strokeWidth: 0,
                            strokeOpacity: 0,
                            outline: 'none',
                            pointerEvents: 'none',
                          },
                          pressed: {
                            fill,
                            fillOpacity: usingVectorProbability ? 0.80 : 0.88,
                            stroke: 'none',
                            strokeWidth: 0,
                            strokeOpacity: 0,
                            outline: 'none',
                            pointerEvents: 'none',
                          },
                        }}
                      />
                    </Fragment>
                  );
                })
              }
            </Geographies>
            </g>
          )}

          {(showSpcFillLayer || showSpcOutlineLayer) && spcFeatureCollection.features.length > 0 && (
            <Geographies geography={spcFeatureCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const color = geo.properties.color as string;
                  const spcStyle = {
                    fill: showSpcFillLayer ? color : 'none',
                    fillOpacity: showSpcFillLayer ? 0.72 : 0,
                    stroke: showSpcOutlineLayer ? color : 'none',
                    strokeWidth: showSpcOutlineLayer ? 2.1 : 0,
                    strokeOpacity: showSpcOutlineLayer ? 0.95 : 0,
                    strokeDasharray: showSpcOutlineLayer ? '7 4' : undefined,
                    outline: 'none',
                    pointerEvents: 'none' as const,
                  };
                  return (
                    <Geography
                      key={`spc-hazard-prob-${hazard}-${geo.rsmKey ?? index}`}
                      geography={geo}
                      tabIndex={-1}
                      style={{
                        default: spcStyle,
                        hover: spcStyle,
                        pressed: spcStyle,
                      }}
                    />
                  );
                })
              }
            </Geographies>
          )}

          {showAutoLayer && visibleCigHatchCollection && visibleCigHatchCollection.features.length > 0 && (
            <Geographies geography={visibleCigHatchCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const fill = 'url(#generated-hazard-cig)';
                  const cigStyle = {
                    fill,
                    fillOpacity: 0.86,
                    stroke: 'none',
                    strokeWidth: 0,
                    strokeOpacity: 0,
                    outline: 'none',
                    pointerEvents: 'none' as const,
                  };
                  return (
                    <Geography
                      key={`generated-hazard-cig-${hazard}-${geo.rsmKey ?? index}`}
                      geography={geo}
                      tabIndex={-1}
                      style={{
                        default: cigStyle,
                        hover: cigStyle,
                        pressed: cigStyle,
                      }}
                    />
                  );
                })
              }
            </Geographies>
          )}

          {showAutoLayer && visibleCigHatchCollection && visibleCigHatchCollection.features.length > 0 && (
            <Geographies geography={visibleCigHatchCollection}>
              {({ geographies }) =>
                geographies.map((geo, index) => {
                  const contourStyle = {
                    fill: 'none',
                    fillOpacity: 0,
                    stroke: '#111111',
                    strokeWidth: 0.8,
                    strokeOpacity: 0.62,
                    strokeLinecap: 'round' as const,
                    strokeLinejoin: 'round' as const,
                    outline: 'none',
                    pointerEvents: 'none' as const,
                  };
                  return (
                    <Geography
                      key={`generated-hazard-cig-contour-${hazard}-${geo.rsmKey ?? index}`}
                      geography={geo}
                      tabIndex={-1}
                      style={{
                        default: contourStyle,
                        hover: contourStyle,
                        pressed: contourStyle,
                      }}
                    />
                  );
                })
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

          <Geographies geography={geoUrl}>
            {({ geographies }) =>
              geographies.map((geo) => (
                <Geography
                  key={`generated-hazard-outline-${geo.rsmKey}`}
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

          {stormReportsMode && stormReportsMode !== 'none' && stormReports && stormReports
            .filter((report) => {
              if (stormReportsMode !== 'all' && report.type !== stormReportsMode) {
                return false;
              }
              if (hazard === 'tornado' && report.type !== 'tornado') return false;
              if (hazard === 'hail' && report.type !== 'hail') return false;
              if (hazard === 'wind' && report.type !== 'wind') return false;
              return true;
            })
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
                <Marker key={`hazard-storm-report-${hazard}-${report.type}-${idx}`} coordinates={[report.lon, report.lat]}>
                  {markerShape === 'triangle' && (
                    <polygon
                      points="0,-5 4,2 -4,2"
                      fill={markerColor}
                      stroke="#ffffff"
                      strokeWidth={0.75}
                    >
                      <title>{tooltipText}</title>
                    </polygon>
                  )}
                  {markerShape === 'circle' && (
                    <circle
                      r={3.2}
                      fill={markerColor}
                      stroke="#ffffff"
                      strokeWidth={0.75}
                    >
                      <title>{tooltipText}</title>
                    </circle>
                  )}
                  {markerShape === 'square' && (
                    <rect
                      x={-2.8}
                      y={-2.8}
                      width={5.6}
                      height={5.6}
                      fill={markerColor}
                      stroke="#ffffff"
                      strokeWidth={0.75}
                    >
                      <title>{tooltipText}</title>
                    </rect>
                  )}
                </Marker>
              );
            })}
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
          </div>
          {hasCigOverlay && showCigOverlay && showAutoLayer && (
            <div className="mt-1 border-t-[1px] border-ink/20 pt-1">
              <div className="flex items-center gap-1.5 font-mono text-[9px] font-bold leading-none">
                <span className="inline-block h-3.5 w-6 border-[1.5px] border-ink bg-[repeating-linear-gradient(135deg,transparent_0,transparent_5px,#111_5px,#111_6.5px)]" aria-hidden />
                <span>CIG</span>
              </div>
            </div>
          )}
          {stormReportsMode && stormReportsMode !== 'none' && (
            <div className="mt-1 border-t-[1px] border-ink/20 pt-1 flex flex-wrap gap-x-2 gap-y-0.5 font-mono text-[8px] font-bold leading-none text-ink">
              {hazard === 'tornado' && (stormReportsMode === 'all' || stormReportsMode === 'tornado') && (
                <div className="flex items-center gap-0.5">
                  <span className="inline-block w-0 h-0 border-l-[3px] border-l-transparent border-r-[3px] border-r-transparent border-b-[5px] border-b-[#e11d48]" aria-hidden />
                  <span>Torn Reports</span>
                </div>
              )}
              {hazard === 'hail' && (stormReportsMode === 'all' || stormReportsMode === 'hail') && (
                <div className="flex items-center gap-0.5">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#16a34a]" aria-hidden />
                  <span>Hail Reports</span>
                </div>
              )}
              {hazard === 'wind' && (stormReportsMode === 'all' || stormReportsMode === 'wind') && (
                <div className="flex items-center gap-0.5">
                  <span className="inline-block h-1.5 w-1.5 bg-[#2563eb]" aria-hidden />
                  <span>Wind Reports</span>
                </div>
              )}
              {hazard === 'thunder' && (
                <div className="flex flex-wrap gap-x-1.5 gap-y-0.5">
                  {(stormReportsMode === 'all' || stormReportsMode === 'tornado') && (
                    <div className="flex items-center gap-0.5">
                      <span className="inline-block w-0 h-0 border-l-[3px] border-l-transparent border-r-[3px] border-r-transparent border-b-[5px] border-b-[#e11d48]" aria-hidden />
                      <span>Torn</span>
                    </div>
                  )}
                  {(stormReportsMode === 'all' || stormReportsMode === 'hail') && (
                    <div className="flex items-center gap-0.5">
                      <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#16a34a]" aria-hidden />
                      <span>Hail</span>
                    </div>
                  )}
                  {(stormReportsMode === 'all' || stormReportsMode === 'wind') && (
                    <div className="flex items-center gap-0.5">
                      <span className="inline-block h-1.5 w-1.5 bg-[#2563eb]" aria-hidden />
                      <span>Wind</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
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

function spcHazardShapesToFeatureCollection(
  collection: OutlookProbabilityShapeFeatureCollection | null,
  hazard: GeneratedHazardKey,
): ArtifactProbabilityFeatureCollection {
  if (!collection || hazard === 'thunder') return { type: 'FeatureCollection', features: [] };
  const features = collection.features
    .filter((feature) => feature.properties.hazard === hazard)
    .map((feature): ArtifactProbabilityFeature => ({
      type: 'Feature',
      geometry: feature.geometry,
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

function formatProbabilityMetric(probability: number, drawableThreshold?: number): string {
  if (!Number.isFinite(probability) || probability < 0.0005) return '--';
  if (drawableThreshold !== undefined && probability < drawableThreshold) {
    return `<${Math.round(drawableThreshold * 100)}%`;
  }
  const percent = probability * 100;
  if (percent < 10 && Math.abs(percent - Math.round(percent)) > 0.05) {
    return `${percent.toFixed(1)}%`;
  }
  return `${Math.round(percent)}%`;
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
