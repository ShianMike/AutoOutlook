import type { HazardKey, HourSnapshot } from '../types/forecast';
import { HAZARD_META, RISK_META } from '../types/forecast';
import RetroPanel from './retro/RetroPanel';
import RetroBadge from './retro/RetroBadge';

interface HazardProbabilityBoardProps {
  snapshot: HourSnapshot | null;
}

const HAZARD_ORDER: HazardKey[] = ['tornado', 'hail', 'wind', 'flood'];

export default function HazardProbabilityBoard({ snapshot }: HazardProbabilityBoardProps) {
  return (
    <RetroPanel title="Hazard Probability Board" eyebrow="04 / Per-hazard automated estimate">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {HAZARD_ORDER.map((key) => (
          <HazardCard key={key} hazardKey={key} snapshot={snapshot} />
        ))}
      </div>
    </RetroPanel>
  );
}

function HazardCard({ hazardKey, snapshot }: { hazardKey: HazardKey; snapshot: HourSnapshot | null }) {
  const meta = HAZARD_META[hazardKey];
  const hz = snapshot?.hazards[hazardKey];
  const probPct = hz ? Math.round(hz.probability * 100) : 0;
  const confPct = hz ? Math.round(hz.confidence * 100) : 0;
  const riskMeta = hz ? RISK_META[hz.level] : RISK_META.TSTM;

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
          {hz?.significantSevere && (
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
          <div className="flex items-baseline justify-between font-mono text-[10px] uppercase tracking-widest text-ink/60">
            <span>Probability</span>
            <span className="font-display text-2xl font-extrabold text-ink">{probPct}%</span>
          </div>
          <ProgressBar value={hz?.probability ?? 0} fillClass="bg-signal-red" />
        </div>
        {/* Confidence */}
        <div>
          <div className="flex items-baseline justify-between font-mono text-[10px] uppercase tracking-widest text-ink/60">
            <span>Confidence</span>
            <span className="font-mono text-[12px] font-bold text-ink">{confPct}%</span>
          </div>
          <ProgressBar value={hz?.confidence ?? 0} fillClass="bg-ink" segments={10} />
        </div>
        {/* Supporting */}
        <div>
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink/60 mb-1">
            Supporting signals
          </div>
          <ul className="flex flex-wrap gap-1">
            {(hz?.supporting ?? []).slice(0, 4).map((s, i) => (
              <li key={i}>
                <RetroBadge tone="paper">{s}</RetroBadge>
              </li>
            ))}
          </ul>
        </div>
        {/* Explanation */}
        <p className="font-sans text-[12px] leading-snug text-ink/80">
          {hz?.explanation ?? '—'}
        </p>
      </div>
    </div>
  );
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
