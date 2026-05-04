import type { ForecastBundle } from '../types/forecast';
import { HAZARD_META, RISK_META } from '../types/forecast';
import { buildRiskTimeline } from '../utils/riskTimeline';
import RetroPanel from './retro/RetroPanel';

interface RiskTimelineProps {
  bundle: ForecastBundle | null;
  selectedForecastHour?: number;
}

export default function RiskTimeline({ bundle, selectedForecastHour }: RiskTimelineProps) {
  const segs = bundle ? buildRiskTimeline(bundle) : [];
  return (
    <RetroPanel
      title="Risk Timeline"
      eyebrow="08 / Period-by-period severe outlook"
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {segs.map((seg) => {
          const meta = RISK_META[seg.category];
          const hzMeta = seg.dominantHazard ? HAZARD_META[seg.dominantHazard] : null;
          const isDark = meta.tw.includes('text-paper');
          const isSelected = selectedForecastHour !== undefined &&
            selectedForecastHour >= seg.startHour &&
            selectedForecastHour <= seg.endHour;
          return (
            <div
              key={seg.period}
              className={[
                'border-[3px] shadow-retro flex flex-col bg-paper',
                isSelected ? 'border-signal-amber shadow-[6px_6px_0_0_#f5b800]' : 'border-ink',
              ].join(' ')}
            >
              <div className={`flex items-center justify-between border-b-[3px] border-ink px-3 py-1.5 ${meta.tw}`}>
                <span className={`font-display font-extrabold uppercase tracking-wider text-sm ${isDark ? 'text-paper' : 'text-ink'}`}>
                  {seg.label}
                </span>
                <div className="flex items-center gap-1">
                  {seg.significantSevere && (
                    <span className="font-display font-extrabold text-[9px] tracking-widest border-[2px] border-signal-red bg-signal-red text-paper px-1 py-0.5">
                      SIG
                    </span>
                  )}
                  {isSelected && (
                    <span className="font-display font-extrabold text-[9px] tracking-widest border-[2px] border-ink bg-paper text-ink px-1 py-0.5">
                      ACTIVE
                    </span>
                  )}
                  <span
                    className={`font-display font-extrabold text-[11px] tracking-widest border-[2px] px-1.5 py-0.5 ${isDark ? 'border-paper text-paper' : 'border-ink text-ink'}`}
                  >
                    {meta.chipText}
                  </span>
                </div>
              </div>
              <div className="p-3 flex flex-col gap-2.5">
                <div>
                  <div className="font-mono text-[9px] uppercase tracking-widest text-ink/60">
                    Coverage
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-2 border-[2px] border-ink relative overflow-hidden">
                      {Array.from({ length: 12 }).map((_, i) => (
                        <div
                          key={i}
                          className={`absolute top-0 h-full ${i / 12 < seg.coverage ? 'bg-ink' : ''}`}
                          style={{ left: `${(i / 12) * 100}%`, width: `${100 / 12 - 0.5}%` }}
                          aria-hidden
                        />
                      ))}
                    </div>
                    <span className="font-mono text-[11px] font-bold w-8 text-right">
                      {Math.round(seg.coverage * 100)}%
                    </span>
                  </div>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5">
                    {hzMeta && <span className="text-lg leading-none">{hzMeta.glyph}</span>}
                    <span className="font-display font-extrabold uppercase text-[12px] tracking-wider leading-tight">
                      {hzMeta?.label ?? '—'}
                    </span>
                  </div>
                  <span className="font-mono text-[10px] tracking-widest text-ink/60">
                    CONF {Math.round(seg.confidence * 100)}%
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-1.5">
                  <TimelineStat label="Peak CAPE" value={`${Math.round(seg.peakCape)}`} unit="J/kg" />
                  <TimelineStat label="Shear" value={`${Math.round(seg.peakShear)}`} unit="kt" />
                  <TimelineStat label="Hazard" value={`${Math.round(seg.peakHazardProbability * 100)}%`} unit="peak" />
                </div>
                <p className="font-sans text-[11px] leading-snug text-ink/80">
                  {seg.note}
                </p>
                {seg.hours.length > 0 && (
                  <div className="font-mono text-[9px] uppercase tracking-widest text-ink/40 border-t-[1px] border-ink/20 pt-1">
                    Hours: +{seg.startHour}h to +{seg.endHour}h
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {segs.length === 0 && (
          <div className="col-span-full text-center font-mono text-ink/50 py-4">
            Awaiting forecast bundle…
          </div>
        )}
      </div>
    </RetroPanel>
  );
}

function TimelineStat({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <div className="border-[2px] border-ink bg-paper px-1.5 py-1 shadow-retro-sm min-w-0">
      <div className="font-mono text-[8px] uppercase tracking-widest text-ink/50 truncate">
        {label}
      </div>
      <div className="font-display font-extrabold text-[13px] leading-none text-ink truncate">
        {value}
      </div>
      <div className="font-mono text-[8px] uppercase tracking-widest text-ink/40 truncate">
        {unit}
      </div>
    </div>
  );
}
