import type { HazardKey, HourSnapshot } from '../types/forecast';
import { HAZARD_META, RISK_META } from '../types/forecast';
import type { ArtifactStatus } from '../hooks/useOutlookArtifacts';
import type { OutlookArtifacts } from '../types/outlookArtifacts';
import {
  getArtifactHazardLevel,
  getArtifactHazardPeak,
  getArtifactHazardPeakLocation,
  getArtifactHourTile,
  type ArtifactHazardKey,
} from '../utils/artifactProbabilities';
import { focusLocationFromSnapshot, formatFocusCoord } from '../utils/focusLocation';
import FocusLocationBadge from './FocusLocationBadge';
import RetroPanel from './retro/RetroPanel';

interface HazardProbabilityBoardProps {
  snapshot: HourSnapshot | null;
  artifacts?: OutlookArtifacts | null;
  artifactStatus?: ArtifactStatus;
}

const HAZARD_ORDER: HazardKey[] = ['tornado', 'hail', 'wind', 'flood'];

export default function HazardProbabilityBoard({ snapshot, artifacts, artifactStatus }: HazardProbabilityBoardProps) {
  const focus = focusLocationFromSnapshot(snapshot);
  return (
    <RetroPanel
      title="Hazard Probability Board"
      eyebrow="04 / Per-hazard automated estimate"
      badge={<FocusLocationBadge focus={focus} />}
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
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
  const selectedArtifactTile = getArtifactHourTile(artifacts, snapshot?.forecastHour);
  const displayArtifactTile = selectedArtifactTile;
  const displayArtifactHour = displayArtifactTile?.forecastHour ?? snapshot?.forecastHour;
  const canUseArtifact = Boolean(artifactHazard && displayArtifactTile && (artifactStatus === 'ready' || artifactStatus === 'loading'));
  const artifactPeak = artifactHazard && canUseArtifact
    ? getArtifactHazardPeak(artifacts, displayArtifactHour, artifactHazard)
    : undefined;
  const artifactPeakLocation = artifactHazard && canUseArtifact
    ? getArtifactHazardPeakLocation(artifacts, displayArtifactHour, artifactHazard)
    : undefined;
  const artifactUnavailable = Boolean(
    artifactHazard
      && artifactStatus
      && artifactStatus !== 'missing'
      && artifactStatus !== 'ready'
      && !displayArtifactTile,
  );
  const probability = artifactPeak ?? (artifactUnavailable ? 0 : hz?.probability ?? 0);
  const probPct = Math.round(probability * 100);
  const confPct = artifactUnavailable ? 0 : hz ? Math.round(hz.confidence * 100) : 0;
  const riskLevel = artifactHazard && artifactPeak !== undefined
    ? getArtifactHazardLevel(artifactHazard, artifactPeak)
    : artifactUnavailable ? 'TSTM' : hz?.level ?? 'TSTM';
  const riskMeta = RISK_META[riskLevel];
  const isArtifact = artifactHazard && artifactPeak !== undefined;
  const location = artifactPeakLocation && artifactPeakLocation.probability > 0
    ? describePeakLocation(snapshot, artifactPeakLocation.lat, artifactPeakLocation.lon)
    : describeFallbackLocation(snapshot);

  return (
    <div className="border-[3px] border-ink bg-paper shadow-retro flex flex-col">
      <div className="flex items-center justify-between border-b-[3px] border-ink px-3 py-2 bg-ink text-paper">
        <div className="flex items-center gap-2">
          <span className="text-xl leading-none">{meta.glyph}</span>
          <span className="font-display font-extrabold uppercase text-sm tracking-wider">
            {meta.label}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {hz?.significantSevere && !isArtifact && (
            <span className="font-display font-extrabold text-[9px] tracking-widest border-[2px] border-signal-red bg-signal-red text-paper px-1 py-0.5">
              SIG
            </span>
          )}
          <span
            className={`font-display font-extrabold text-[11px] tracking-widest border-[2px] border-paper px-1.5 py-0.5 ${riskMeta.tw}`}
          >
            {riskMeta.chipText}
          </span>
        </div>
      </div>
      <div className="p-3 flex flex-col gap-3">
        {/* Probability */}
        <div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-2 font-mono text-[10px] uppercase tracking-widest text-ink/60">
            <span className="min-w-0 truncate pr-1">Probability</span>
            <span className="min-w-[4.25rem] max-w-full overflow-hidden text-right">
              <span className="block whitespace-nowrap font-display text-[clamp(1.25rem,1.8vw,1.5rem)] font-extrabold text-ink leading-none tabular-nums">{probPct}%</span>
              <span className="mt-1 block max-w-[8.5rem] truncate font-mono text-[8px] font-bold uppercase tracking-[0.12em] text-ink/65 sm:max-w-[9.5rem]">
                {location}
              </span>
            </span>
          </div>
          <ProgressBar value={probability} fillClass="bg-signal-red" />
        </div>
        {/* Confidence */}
        <div>
          <div className="flex items-baseline justify-between font-mono text-[10px] uppercase tracking-widest text-ink/60">
            <span>Confidence</span>
            <span className="font-mono text-[12px] font-bold text-ink">{confPct}%</span>
          </div>
          <ProgressBar value={hz?.confidence ?? 0} fillClass="bg-ink" segments={10} />
        </div>
      </div>
    </div>
  );
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
  fillClass,
  segments = 16,
}: {
  value: number;
  fillClass: string;
  segments?: number;
}) {
  return (
    <div className="mt-1 h-2 border-[2px] border-ink bg-paper relative overflow-hidden">
      {Array.from({ length: segments }).map((_, i) => (
        <div
          key={i}
          className={`absolute top-0 h-full ${i / segments < value ? fillClass : ''}`}
          style={{ left: `${(i / segments) * 100}%`, width: `${100 / segments - 0.4}%` }}
          aria-hidden
        />
      ))}
    </div>
  );
}
