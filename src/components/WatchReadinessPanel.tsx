import type { HourSnapshot, RiskCategory } from '../types/forecast';
import { RISK_META } from '../types/forecast';
import RetroPanel from './retro/RetroPanel';

interface WatchReadinessPanelProps {
  snapshot: HourSnapshot | null;
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

function levelFromCategory(cat: RiskCategory | undefined, fallback: Level = 'low'): Level {
  if (!cat) return fallback;
  const ord = RISK_META[cat].ord;
  if (ord >= 4) return 'high';
  if (ord >= 3) return 'elevated';
  if (ord >= 2) return 'moderate';
  if (ord >= 1) return 'low';
  return 'low';
}

export default function WatchReadinessPanel({ snapshot }: WatchReadinessPanelProps) {
  const tornadoLevel = snapshot
    ? snapshot.hazards.tornado.probability >= 0.10 ? 'high' :
      snapshot.hazards.tornado.probability >= 0.05 ? 'elevated' :
      snapshot.hazards.tornado.probability >= 0.02 ? 'moderate' : 'low'
    : 'low';
  const svrLevel = snapshot ? levelFromCategory(snapshot.outlook.category) : 'low';
  const mdLevel: Level = snapshot && snapshot.outlook.confidence >= 0.6 && RISK_META[snapshot.outlook.category].ord >= 2 ? 'elevated' :
                snapshot && RISK_META[snapshot.outlook.category].ord >= 2 ? 'moderate' : 'low';
  const upgradeConfLevel: Level = snapshot && snapshot.outlook.confidence >= 0.75 ? 'high' :
                snapshot && snapshot.outlook.confidence >= 0.55 ? 'elevated' :
                snapshot && snapshot.outlook.confidence >= 0.40 ? 'moderate' : 'low';
  const monitorLevel: Level = snapshot && RISK_META[snapshot.outlook.category].ord >= 3 ? 'high' :
                snapshot && RISK_META[snapshot.outlook.category].ord >= 2 ? 'elevated' :
                snapshot && RISK_META[snapshot.outlook.category].ord >= 1 ? 'moderate' : 'low';

  // Significant severe: any hazard meeting SPC 10%+ significant threshold
  const sigHazards = snapshot
    ? (['tornado', 'hail', 'wind', 'flood'] as const).filter((k) => snapshot.hazards[k].significantSevere)
    : [];
  const sigLevel: Level = sigHazards.length >= 2 ? 'high' : sigHazards.length === 1 ? 'elevated' : 'low';
  const sigNote = sigHazards.length > 0
    ? `10%+ probability of significant: ${sigHazards.join(', ')}`
    : 'No hazard meets the 10% significant severe threshold.';

  const rows: { label: string; level: Level; note: string }[] = [
    {
      label: 'Severe Thunderstorm Watch potential',
      level: svrLevel,
      note: snapshot ? `Within ${snapshot.region.label}` : '—',
    },
    {
      label: 'Tornado Watch potential',
      level: tornadoLevel,
      note: snapshot ? `STP ${snapshot.ingredients.stp.toFixed(1)}, 0–1 km SRH ${Math.round(snapshot.ingredients.srh01)} m²/s²` : '—',
    },
    {
      label: 'Significant Severe potential',
      level: sigLevel,
      note: sigNote,
    },
    {
      label: 'Mesoscale Discussion potential',
      level: mdLevel,
      note: snapshot && mdLevel !== 'low' ? 'Convective trends warrant SPC mesoscale messaging.' : 'No MD criteria currently met.',
    },
    {
      label: 'Upgrade / downgrade confidence',
      level: upgradeConfLevel,
      note: snapshot ? `Engine confidence ${Math.round(snapshot.outlook.confidence * 100)}%` : '—',
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
