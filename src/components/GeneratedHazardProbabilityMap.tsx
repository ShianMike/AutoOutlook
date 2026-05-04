import { useMemo } from 'react';
import { ComposableMap, Geographies, Geography, Marker } from 'react-simple-maps';
import type { HourSnapshot } from '../types/forecast';
import type { OutlookArtifacts, OutlookProbabilityTile } from '../types/outlookArtifacts';
import { HAZARD_CONFIGS } from '../utils/hazardProbabilityBands';

const STATES_URL = '/us-states-10m.json';

export type GeneratedHazardKey = 'tornado' | 'hail' | 'wind';

interface GeneratedHazardProbabilityMapProps {
  snapshot: HourSnapshot | null;
  hazard: GeneratedHazardKey;
  title: string;
  artifacts: OutlookArtifacts | null;
}

interface HazardPoint {
  lon: number;
  lat: number;
  probability: number;
  bucket: number;
}

export function hasGeneratedHazardTile(
  artifacts: OutlookArtifacts | null,
  forecastHour: number | undefined,
): boolean {
  if (forecastHour === undefined) return false;
  return Boolean(artifacts?.probabilityTiles?.hours.some((hour) => hour.forecastHour === forecastHour));
}

export default function GeneratedHazardProbabilityMap({
  snapshot,
  hazard,
  title,
  artifacts,
}: GeneratedHazardProbabilityMapProps) {
  const forecastHour = snapshot?.forecastHour;
  const probabilityHour = useMemo(
    () => artifacts?.probabilityTiles?.hours.find((hour) => hour.forecastHour === forecastHour),
    [artifacts?.probabilityTiles, forecastHour],
  );
  const tile = probabilityHour?.tile;
  const cfg = HAZARD_CONFIGS[hazard];
  const points = useMemo(
    () => tileToPoints(tile, hazard),
    [tile, hazard],
  );
  const peakProb = points.reduce((max, point) => Math.max(max, point.probability), 0);
  const peakPct = peakProb >= 0.005 ? `${Math.round(peakProb * 100)}%` : '--';

  return (
    <div className="border-[3px] border-ink bg-paper shadow-retro flex flex-col">
      <header className="border-b-[2px] border-ink bg-ink text-paper px-3 py-1.5 flex items-center justify-between gap-2">
        <span className="font-display font-extrabold uppercase text-[12px] tracking-wider truncate">
          {title}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-widest text-paper/70 shrink-0">
          PEAK {peakPct}
        </span>
      </header>
      <div className="aspect-[5/3] relative overflow-hidden bg-paper md:aspect-[19/10] xl:aspect-[43/20]">
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

          {points.map((point, idx) => {
            const size = 2.4 + point.bucket * 0.65;
            return (
              <Marker key={`${hazard}-tile-${idx}`} coordinates={[point.lon, point.lat]}>
                <rect
                  x={-size / 2}
                  y={-size / 2}
                  width={size}
                  height={size}
                  fill={cfg.colors[point.bucket]}
                  fillOpacity={0.62}
                  stroke="#111111"
                  strokeWidth={0.12}
                />
              </Marker>
            );
          })}

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
        </ComposableMap>

        <div className="absolute bottom-1.5 left-1.5 border-[2px] border-ink bg-paper/95 px-2 py-1 shadow-retro-sm">
          <div className="font-mono text-[8px] uppercase tracking-[0.2em] text-ink/70 leading-none mb-1">
            HRRR/XGBoost {title}
          </div>
          <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
            {cfg.thresholds.map((threshold, i) => (
              <div key={threshold} className="flex items-center gap-1 font-mono text-[9px] font-bold leading-none">
                <span
                  className="inline-block w-3 h-2 border-[1.5px] border-ink shrink-0"
                  style={{ background: cfg.colors[i] }}
                  aria-hidden
                />
                <span className="text-ink">{cfg.labels[i]}</span>
              </div>
            ))}
          </div>
        </div>

        {points.length === 0 && (
          <div className="absolute top-1.5 right-1.5 border-[2px] border-ink bg-paper px-2 py-1 shadow-retro-sm font-mono text-[10px] uppercase tracking-widest">
            BELOW THRESHOLD
          </div>
        )}
      </div>
    </div>
  );
}

function tileToPoints(tile: OutlookProbabilityTile | undefined, hazard: GeneratedHazardKey): HazardPoint[] {
  if (!tile) return [];
  const probs = tile.probabilities[hazard];
  const cfg = HAZARD_CONFIGS[hazard];
  const points: HazardPoint[] = [];
  for (let row = 0; row < probs.length; row += 1) {
    for (let col = 0; col < probs[row].length; col += 1) {
      const probability = Number(probs[row][col]);
      const lat = Number(tile.lats[row]?.[col]);
      const lon = Number(tile.lons[row]?.[col]);
      const riskOrdinal = Number(tile.categoryOrdinal[row]?.[col] ?? 0);
      if (!Number.isFinite(probability) || !Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      if (riskOrdinal < 2) continue;
      const rawBucket = probabilityBucket(probability, cfg.thresholds);
      if (rawBucket < 0) continue;
      const cappedBucket = Math.min(rawBucket, riskOrdinal - 2);
      if (cappedBucket < 0) continue;
      points.push({ lon, lat, probability, bucket: cappedBucket });
    }
  }
  return points;
}

function probabilityBucket(probability: number, thresholds: number[]): number {
  let bucket = -1;
  thresholds.forEach((threshold, idx) => {
    if (probability >= threshold) bucket = idx;
  });
  return bucket;
}
