import type { HazardKey, HourSnapshot } from '../types/forecast';
import { HAZARD_META, RISK_META } from '../types/forecast';
import type { ArtifactStatus } from '../hooks/useOutlookArtifacts';
import type { OutlookArtifacts } from '../types/outlookArtifacts';
import {
  resolveHazardEstimate,
  type ArtifactHazardKey,
} from '../utils/artifactProbabilities';
import { focusLocationFromSnapshot, formatFocusCoord, focusLocationFromRegion } from '../utils/focusLocation';
import { mergedRegionFromArtifacts } from '../utils/mergedFocus';
import FocusLocationBadge from './FocusLocationBadge';
import RetroPanel from './retro/RetroPanel';

interface HazardProbabilityBoardProps {
  snapshot: HourSnapshot | null;
  artifacts?: OutlookArtifacts | null;
  artifactStatus?: ArtifactStatus;
  viewType?: 'hourly' | 'merged';
}

const HAZARD_ORDER: HazardKey[] = ['tornado', 'hail', 'wind', 'flood'];

export default function HazardProbabilityBoard({ snapshot, artifacts, artifactStatus, viewType = 'hourly' }: HazardProbabilityBoardProps) {
  const isMerged = viewType === 'merged';
  const mergedRegion = isMerged ? mergedRegionFromArtifacts(artifacts ?? null) : null;
  const focus = mergedRegion ? focusLocationFromRegion(mergedRegion) : focusLocationFromSnapshot(snapshot);
  return (
    <RetroPanel
      title="Hazard Probability Board"
      eyebrow="05 / Per-hazard automated estimate"
      badge={<FocusLocationBadge focus={focus} label={isMerged ? 'Day 1 Focus' : 'Risk Center'} showCoord={!isMerged} />}
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {HAZARD_ORDER.map((key) => (
          <HazardCard
            key={key}
            hazardKey={key}
            snapshot={snapshot}
            artifacts={artifacts ?? null}
            artifactStatus={artifactStatus}
          />
        ))}
      </div>
    </RetroPanel>
  );
}

const TornadoIcon = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg className={`${className} stroke-current`} fill="none" viewBox="0 0 24 24" strokeWidth={2.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M4 4h16M6 8h12M8 12h8M9 16h6M11 20h2" />
  </svg>
);

const HailIcon = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg className={`${className} fill-current`} viewBox="0 0 24 24">
    <path d="M12 2L2 12l10 10 10-10L12 2z" />
  </svg>
);

const WindIcon = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg className={`${className} stroke-current`} fill="none" viewBox="0 0 24 24" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
    <path d="M17.7 7.7a2.5 2.5 0 1 1 1.8 4.3H2" />
    <path d="M9.6 4.6A2 2 0 1 1 11 8H2" />
    <path d="M12.6 19.4A2 2 0 1 0 14 16H2" />
  </svg>
);

const FloodIcon = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg className={`${className} stroke-current`} fill="none" viewBox="0 0 24 24" strokeWidth={2.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M3 9c3.33-2 6.67-2 10 0s6.67 2 10 0M3 15c3.33-2 6.67-2 10 0s6.67 2 10 0" />
  </svg>
);

const LightningIcon = ({ className = 'w-2.5 h-2.5' }: { className?: string }) => (
  <svg className={`${className} fill-current`} viewBox="0 0 24 24">
    <path d="M13 2L3 14h9v8l10-12h-9l1-8z" />
  </svg>
);

const MapPinIcon = ({ className = 'w-2.5 h-2.5' }: { className?: string }) => (
  <svg className={`${className} fill-current`} viewBox="0 0 24 24">
    <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
  </svg>
);

const BroadcastIcon = ({ className = 'w-3.5 h-3.5' }: { className?: string }) => (
  <svg className={`${className} fill-current`} viewBox="0 0 24 24">
    <path d="M12 2C6.48 2 2 6.48 2 12c0 2.76 1.12 5.26 2.93 7.07l1.42-1.42C4.85 16.15 4 14.18 4 12c0-4.41 3.59-8 8-8s8 3.59 8 8c0 2.18-.85 4.15-2.34 5.66l1.42 1.42C20.88 17.26 22 14.76 22 12c0-5.52-4.48-10-10-10zm0 4c-3.31 0-6 2.69-6 6 0 1.66.68 3.15 1.76 4.24l1.42-1.42C8.42 14.07 8 13.08 8 12c0-2.21 1.79-4 4-4s4 1.79 4 4c0 1.08-.42 2.07-1.18 2.82l1.42 1.42C17.32 15.15 18 13.66 18 12c0-3.31-2.69-6-6-6zm0 4c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z" />
  </svg>
);

const HAZARD_ICONS: Record<HazardKey, React.ComponentType<{ className?: string }>> = {
  tornado: TornadoIcon,
  hail: HailIcon,
  wind: WindIcon,
  flood: FloodIcon,
};

const HAZARD_DESCRIPTIONS: Record<HazardKey, string> = {
  tornado: 'Tornado: A violently rotating column of air in contact with both the surface and a convective cloud. Risks assess the likelihood of a tornado within 25 miles of a point.',
  hail: 'Severe Hail: Frozen precipitation falling from intense storm updrafts. Severe threshold is ≥ 1.00 inch diameter; significant severe (SIG) indicates giant ≥ 2.00 inch hail.',
  wind: 'Damaging Wind: High-velocity convective downdrafts producing damaging straight-line gusts exceeding 50 knots (58 mph). Risk assesses local downburst/squall line potential.',
  flood: 'Excessive Rainfall & Flooding: Intense convective precipitation rates exceeding local soil infiltration or drainage capacity, resulting in rapid runoff and flash flooding.',
};

function HazardCard({
  hazardKey,
  snapshot,
  artifacts,
  artifactStatus,
}: {
  hazardKey: HazardKey;
  snapshot: HourSnapshot | null;
  artifacts: OutlookArtifacts | null;
  artifactStatus?: ArtifactStatus;
}) {
  const meta = HAZARD_META[hazardKey];
  const hz = snapshot?.hazards[hazardKey];
  const artifactHazard = hazardKey === 'tornado' || hazardKey === 'hail' || hazardKey === 'wind'
    ? hazardKey as ArtifactHazardKey
    : null;
  const { probability, level: riskLevel, isArtifact, artifactUnavailable, peakLocation: artifactPeakLocation } =
    resolveHazardEstimate(hazardKey, snapshot, artifacts, artifactStatus);
  const probPct = formatHazardProbability(probability, isArtifact ? minimumArtifactHazardThreshold(artifactHazard) : undefined);
  const confPct = artifactUnavailable ? 0 : hz ? Math.round(hz.confidence * 100) : 0;
  const riskMeta = RISK_META[riskLevel];
  const location = artifactPeakLocation && artifactPeakLocation.probability > 0
    ? describePeakLocation(snapshot, artifactPeakLocation.lat, artifactPeakLocation.lon)
    : describeFallbackLocation(snapshot);

  const HazardIcon = HAZARD_ICONS[hazardKey];

  return (
    <div className="relative border-[3px] border-ink bg-paper shadow-retro hover:shadow-retro-lg hover:-translate-y-1 transition-all duration-200 flex flex-col cursor-default group overflow-visible">
      <div className="flex items-center justify-between border-b-[3px] border-ink px-3 py-2.5 bg-ink text-paper group-hover:bg-signal-amber group-hover:text-ink transition-colors duration-200 relative overflow-visible">
        <div className="flex items-center gap-2 relative group/tooltip cursor-help select-none">
          <span className="text-paper group-hover:text-ink transition-colors duration-200 shrink-0">
            <HazardIcon className="w-5 h-5 transition-transform group-hover/tooltip:scale-110 duration-200" />
          </span>
          <span className="font-display font-extrabold uppercase text-xs tracking-wider border-b border-dashed border-paper/30 group-hover:border-ink/30 group-hover/tooltip:text-signal-amber transition-colors duration-150">
            {meta.label}
          </span>
          
          {/* Neo-Brutalist Floating Tooltip */}
          <div className="pointer-events-none absolute bottom-full left-0 z-50 mb-2.5 w-60 scale-90 opacity-0 transition-all duration-200 group-hover/tooltip:scale-100 group-hover/tooltip:opacity-100 origin-bottom-left">
            <div className="border-[2px] border-signal-amber bg-ink text-signal-lime px-2.5 py-1.5 font-mono text-[9.5px] leading-normal shadow-[4px_4px_0_0_#9ad62a] text-left normal-case tracking-normal">
              {HAZARD_DESCRIPTIONS[hazardKey]}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 select-none">
          {hz?.significantSevere && !isArtifact && (
            <span className="font-display font-extrabold text-[8px] tracking-widest border-[2px] border-signal-red bg-signal-red text-paper px-1 py-0.5 animate-pulse shadow-retro-xs flex items-center gap-0.5">
              <LightningIcon /> SIG
            </span>
          )}
          <span
            className={`font-display font-extrabold text-[10px] tracking-widest border-[2px] border-paper px-1.5 py-0.5 shadow-retro-xs ${riskMeta.tw}`}
          >
            {riskMeta.chipText}
          </span>
        </div>
      </div>
      
      {/* Body container with relative and overflow-hidden for the sliding overlay */}
      <div className="relative overflow-hidden flex-1 flex flex-col">
        <div className="p-3.5 flex flex-col gap-3.5 bg-paper/50 flex-1">
          {/* Probability display */}
          <div className="flex flex-col">
            <div className="flex items-start justify-between">
              <span className="font-mono text-[9px] font-bold uppercase tracking-widest text-ink/65">
                PROBABILITY
              </span>
              <div className="text-right">
                <span className="block whitespace-nowrap font-display text-[26px] font-extrabold text-ink leading-none tracking-tight tabular-nums">
                  {probPct}
                </span>
                <span className="mt-1 flex items-center gap-1 justify-end font-mono text-[8px] font-bold uppercase tracking-wider text-ink/50 max-w-[12rem]" title={location}>
                  <MapPinIcon /> <span className="truncate">{location}</span>
                </span>
              </div>
            </div>
            <ProgressBar value={probability} type="probability" />
          </div>

          {/* Confidence display */}
          <div className="flex flex-col">
            <div className="flex items-center justify-between">
              <span className="font-mono text-[9px] font-bold uppercase tracking-widest text-ink/65">
                CONFIDENCE
              </span>
              <span className="font-mono text-[11px] font-extrabold text-ink tracking-wide">
                {confPct}%
              </span>
            </div>
            <ProgressBar value={hz?.confidence ?? 0} type="confidence" segments={10} />
          </div>
        </div>

        {/* Sliding meteorological explanation overlay on hover */}
        {hz && (
          <div className="absolute inset-0 bg-ink/95 text-paper p-3.5 font-mono text-[10px] leading-relaxed flex flex-col justify-between translate-y-full group-hover:translate-y-0 transition-transform duration-300 ease-out z-10 select-none">
            <div className="flex flex-col gap-1.5 overflow-y-auto max-h-[110px] scrollbar-thin pr-1">
              <div className="font-display font-extrabold text-[9px] uppercase tracking-wider text-signal-amber border-b border-paper/20 pb-0.5 mb-1.5 flex items-center gap-1.5">
                <BroadcastIcon /> METEOROLOGICAL REASONING
              </div>
              <p className="text-paper/90 text-[10.5px]">
                {artifactUnavailable
                  ? 'Selected cycle hazard data is currently offline or unavailable.'
                  : hz.explanation || 'No hazard description details are available for this forecast hour.'}
              </p>
            </div>
            {hz.supporting && hz.supporting.length > 0 && !artifactUnavailable && (
              <div className="mt-2 pt-2 border-t border-paper/10">
                <span className="block font-bold text-[8px] tracking-widest text-signal-amber mb-0.5">SUPPORTING VARIABLES</span>
                <span className="block text-[8px] text-paper/70 truncate uppercase tracking-wider">
                  {hz.supporting.join(' · ')}
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function minimumArtifactHazardThreshold(hazard: ArtifactHazardKey | null): number | undefined {
  if (hazard === 'tornado') return 0.02;
  if (hazard === 'hail' || hazard === 'wind') return 0.05;
  return undefined;
}

function formatHazardProbability(probability: number, drawableThreshold?: number): string {
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

function describePeakLocation(snapshot: HourSnapshot | null, lat: number, lon: number): string {
  const city = nearestCity(snapshot, lat, lon);
  const coord = formatFocusCoord(lat, lon);
  return city && city !== coord ? `${city} · ${coord}` : coord;
}

function describeFallbackLocation(snapshot: HourSnapshot | null): string {
  const focus = focusLocationFromSnapshot(snapshot);
  return focus.usesCoordinateLabel ? focus.label : `${focus.label} · ${focus.coord}`;
}

function nearestCity(snapshot: HourSnapshot | null, lat: number, lon: number): string | null {
  if (!snapshot?.cities?.length) return null;
  const best = snapshot.cities
    .map((city) => ({
      name: city.name,
      distanceKm: distance(lat, lon, city.lat, city.lon),
    }))
    .sort((a, b) => a.distanceKm - b.distanceKm)[0];
  return best && best.distanceKm <= 250 ? best.name : null;
}

function distance(latA: number, lonA: number, latB: number, lonB: number): number {
  const kmPerLat = 111.2;
  const latMid = ((latA + latB) / 2) * (Math.PI / 180);
  const dx = (lonA - lonB) * Math.cos(latMid) * kmPerLat;
  const dy = (latA - latB) * kmPerLat;
  return Math.hypot(dx, dy);
}

function ProgressBar({
  value,
  type = 'probability',
  segments = 16,
}: {
  value: number;
  type?: 'probability' | 'confidence';
  segments?: number;
}) {
  return (
    <div className="mt-1.5 h-3.5 border-[2px] border-ink bg-ink/5 p-[1.5px] relative overflow-hidden flex gap-[2px] shadow-retro-xs">
      {Array.from({ length: segments }).map((_, i) => {
        const isActive = i / segments < value;
        let colorClass = 'bg-paper-dark/15'; // inactive segment
        if (isActive) {
          if (type === 'probability') {
            if (i < segments * 0.4) {
              colorClass = 'bg-emerald-500 border-t border-emerald-300';
            } else if (i < segments * 0.7) {
              colorClass = 'bg-yellow-500 border-t border-yellow-300';
            } else if (i < segments * 0.9) {
              colorClass = 'bg-orange-500 border-t border-orange-300';
            } else {
              colorClass = 'bg-red-500 border-t border-red-300 animate-pulse';
            }
          } else {
            // Confidence uses solid retro ink bars
            colorClass = 'bg-ink border-t border-ink/40';
          }
        }
        return (
          <div
            key={i}
            className={`h-full flex-1 transition-all duration-300 ${colorClass}`}
            aria-hidden
          />
        );
      })}
    </div>
  );
}
