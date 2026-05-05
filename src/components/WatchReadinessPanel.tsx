import type { HourSnapshot, RiskCategory } from '../types/forecast';
import { RISK_META } from '../types/forecast';
import type { ArtifactStatus } from '../hooks/useOutlookArtifacts';
import type { ArtifactRiskCategory, OutlookArtifacts, OutlookTimelineHourSummary } from '../types/outlookArtifacts';
import RetroPanel from './retro/RetroPanel';

interface WatchReadinessPanelProps {
  snapshot: HourSnapshot | null;
  artifacts?: OutlookArtifacts | null;
  artifactStatus?: ArtifactStatus;
}

type Level = 'low' | 'moderate' | 'elevated' | 'high';

const LEVEL_TONE: Record<Level, string> = {
  low:      'bg-signal-lime text-ink',
  moderate: 'bg-signal-amber text-ink',
  elevated: 'bg-signal-orange text-ink',
  high:     'bg-signal-red text-paper',
};

const LEVEL_DOT: Record<Level, string> = {
  low:      'bg-signal-lime',
  moderate: 'bg-signal-amber',
  elevated: 'bg-signal-orange',
  high:     'bg-signal-red',
};

const RISK_ORD: Record<ArtifactRiskCategory, number> = {
  NONE: -1,
  TSTM: 0,
  MRGL: 1,
  SLGT: 2,
  ENH: 3,
  MDT: 4,
  MOD: 4,
  HIGH: 5,
};

function normalizeCategory(cat: ArtifactRiskCategory | RiskCategory | undefined): RiskCategory | undefined {
  if (!cat || cat === 'NONE') return undefined;
  return cat === 'MDT' ? 'MOD' : cat;
}

function levelFromCategory(cat: ArtifactRiskCategory | RiskCategory | undefined, fallback: Level = 'low'): Level {
  if (!cat) return fallback;
  const normalized = normalizeCategory(cat);
  const ord = normalized ? RISK_META[normalized].ord : RISK_ORD[cat as ArtifactRiskCategory] ?? 0;
  if (ord >= 4) return 'high';
  if (ord >= 3) return 'elevated';
  if (ord >= 2) return 'moderate';
  if (ord >= 1) return 'low';
  return 'low';
}

function selectedArtifactHour(
  artifacts: OutlookArtifacts | null | undefined,
  artifactStatus: ArtifactStatus | undefined,
  snapshot: HourSnapshot | null,
): OutlookTimelineHourSummary | undefined {
  if (artifactStatus !== 'ready' || !snapshot) return undefined;
  return artifacts?.timelineSummary?.hours.find((hour) => hour.forecastHour === snapshot.forecastHour);
}

function probabilityLevel(probability: number): Level {
  if (probability >= 0.10) return 'high';
  if (probability >= 0.05) return 'elevated';
  if (probability >= 0.02) return 'moderate';
  return 'low';
}

export default function WatchReadinessPanel({ snapshot, artifacts, artifactStatus }: WatchReadinessPanelProps) {
  const generatedHour = selectedArtifactHour(artifacts, artifactStatus, snapshot);
  const displayCategory = generatedHour?.category ?? snapshot?.outlook.category;
  const categoryOrd = displayCategory ? RISK_ORD[displayCategory as ArtifactRiskCategory] ?? RISK_META[displayCategory as RiskCategory]?.ord ?? 0 : 0;
  const tornadoProbability = generatedHour?.probabilityMax?.tornado ?? snapshot?.hazards.tornado.probability ?? 0;
  const peakProbability = generatedHour?.peakHazardProbability ?? (
    snapshot
      ? Math.max(snapshot.hazards.tornado.probability, snapshot.hazards.hail.probability, snapshot.hazards.wind.probability)
      : 0
  );
  const coverage = generatedHour?.coverage ?? (snapshot ? Math.min(1, peakProbability * 2.5) : 0);
  const usingGeneratedArtifacts = Boolean(generatedHour);

  const tornadoLevel = snapshot
    ? probabilityLevel(tornadoProbability)
    : 'low';
  const svrLevel = snapshot ? levelFromCategory(snapshot.outlook.category) : 'low';
  const generatedSvrLevel = usingGeneratedArtifacts ? levelFromCategory(displayCategory) : svrLevel;
  const mdLevel: Level = snapshot && categoryOrd >= 3 && coverage >= 0.08 ? 'elevated' :
                snapshot && categoryOrd >= 2 && (coverage >= 0.04 || peakProbability >= 0.12) ? 'moderate' : 'low';
  const upgradeConfLevel: Level = snapshot && usingGeneratedArtifacts && categoryOrd >= 4 && Boolean(generatedHour?.significantSevere) ? 'elevated' :
                snapshot && usingGeneratedArtifacts && categoryOrd >= 2 ? 'moderate' :
                snapshot && !usingGeneratedArtifacts && snapshot.outlook.confidence >= 0.75 ? 'high' :
                snapshot && !usingGeneratedArtifacts && snapshot.outlook.confidence >= 0.55 ? 'elevated' :
                snapshot && !usingGeneratedArtifacts && snapshot.outlook.confidence >= 0.40 ? 'moderate' : 'low';
  const monitorLevel: Level = snapshot && categoryOrd >= 4 ? 'high' :
                snapshot && categoryOrd >= 3 ? 'elevated' :
                snapshot && categoryOrd >= 1 ? 'moderate' : 'low';

  const sigHazards = generatedHour?.significantSevere && generatedHour.mainHazard
    ? [generatedHour.mainHazard]
    : !generatedHour && snapshot
    ? (['tornado', 'hail', 'wind'] as const).filter((k) => snapshot.hazards[k].significantSevere)
    : [];
  const sigLevel: Level = generatedHour?.significantSevere
    ? categoryOrd >= 4 ? 'high' : 'elevated'
    : sigHazards.length >= 2 ? 'high' : sigHazards.length === 1 ? 'elevated' : 'low';
  const sigNote = sigHazards.length > 0
    ? `Generated significant severe signal: ${sigHazards.join(', ')}`
    : usingGeneratedArtifacts
    ? 'No generated significant severe signal for this hour.'
    : 'No hazard meets the 10% significant severe threshold.';
  const sourceNote = usingGeneratedArtifacts ? 'Generated HRRR/XGBoost hour summary' : 'Raw forecast snapshot';

  const rows: { label: string; level: Level; note: string }[] = [
    {
      label: 'Severe Thunderstorm Watch potential',
      level: generatedSvrLevel,
      note: snapshot ? `${sourceNote}; ${normalizeCategory(displayCategory)?.toUpperCase() ?? 'TSTM'} risk in ${snapshot.region.label}` : '—',
    },
    {
      label: 'Tornado Watch potential',
      level: tornadoLevel,
      note: snapshot ? `${Math.round(tornadoProbability * 100)}% tornado peak; STP ${snapshot.ingredients.stp.toFixed(1)}, 0-1 km SRH ${Math.round(snapshot.ingredients.srh01)} m2/s2` : '—',
    },
    {
      label: 'Significant Severe potential',
      level: sigLevel,
      note: sigNote,
    },
    {
      label: 'Mesoscale Discussion potential',
      level: mdLevel,
      note: snapshot && mdLevel !== 'low'
        ? `Generated coverage ${Math.round(coverage * 100)}%, peak hazard ${Math.round(peakProbability * 100)}%.`
        : 'No generated MD criteria currently met.',
    },
    {
      label: 'Upgrade / downgrade confidence',
      level: upgradeConfLevel,
      note: snapshot ? `${usingGeneratedArtifacts ? 'Artifact-constrained signal' : `Engine confidence ${Math.round(snapshot.outlook.confidence * 100)}%`}` : '—',
    },
    {
      label: 'Monitoring priority',
      level: monitorLevel,
      note: monitorLevel === 'high' ? 'Close attention required.' :
            monitorLevel === 'elevated' ? 'Heightened watch warranted.' :
            monitorLevel === 'moderate' ? 'Routine monitoring with awareness.' :
            'Standard monitoring cadence.',
    },
  ];

  return (
    <RetroPanel
      title="Watch / Readiness Panel"
      eyebrow="09 / Operational readiness indicators"
    >
      <ul className="flex flex-col">
        {rows.map((r, i) => (
          <li
            key={r.label}
            className={[
              'flex items-center gap-3 px-2 py-2.5',
              i !== rows.length - 1 ? 'border-b-[2px] border-ink' : '',
            ].join(' ')}
          >
            <span className={`shrink-0 w-3 h-3 rounded-full border-[2px] border-ink ${LEVEL_DOT[r.level]} ${r.level === 'high' ? 'animate-pulse-dot' : ''}`} aria-hidden />
            <div className="flex-1 min-w-0">
              <div className="font-display font-extrabold uppercase text-[12px] tracking-wider leading-tight">
                {r.label}
              </div>
              <div className="font-mono text-[11px] text-ink/70 leading-snug">
                {r.note}
              </div>
            </div>
            <span
              className={`shrink-0 inline-block border-[2px] border-ink px-2 py-0.5 font-display font-extrabold text-[11px] tracking-widest shadow-retro-sm ${LEVEL_TONE[r.level]}`}
            >
              {r.level.toUpperCase()}
            </span>
          </li>
        ))}
      </ul>
    </RetroPanel>
  );
}
