import type { ForecastBundle, RiskCategory } from '../types/forecast';
import { HAZARD_META, RISK_META } from '../types/forecast';
import type { ArtifactStatus } from '../hooks/useOutlookArtifacts';
import type { OutlookArtifacts } from '../types/outlookArtifacts';
import { focusLocationFromSnapshot } from '../utils/focusLocation';
import { buildRiskTimeline } from '../utils/riskTimeline';
import FocusLocationBadge from './FocusLocationBadge';
import RetroPanel from './retro/RetroPanel';

interface RiskTimelineProps {
  bundle: ForecastBundle | null;
  selectedForecastHour?: number;
  artifacts?: OutlookArtifacts | null;
  artifactStatus?: ArtifactStatus;
  onHourChange?: (hour: number) => void;
}

function getLedColor(category: RiskCategory, isLit: boolean) {
  if (isLit) {
    if (category === 'TSTM' || category === 'MRGL') {
      return 'bg-signal-lime shadow-[0_0_6px_rgba(154,214,42,0.8)]';
    }
    if (category === 'SLGT' || category === 'ENH') {
      return 'bg-signal-amber shadow-[0_0_6px_rgba(247,181,0,0.8)]';
    }
    return 'bg-signal-red shadow-[0_0_6px_rgba(239,59,44,0.8)]';
  } else {
    if (category === 'TSTM' || category === 'MRGL') {
      return 'bg-signal-lime/10 border-t border-signal-lime/5';
    }
    if (category === 'SLGT' || category === 'ENH') {
      return 'bg-signal-amber/10 border-t border-signal-amber/5';
    }
    return 'bg-signal-red/10 border-t border-signal-red/5';
  }
}

export default function RiskTimeline({
  bundle,
  selectedForecastHour,
  artifacts,
  artifactStatus,
  onHourChange,
}: RiskTimelineProps) {
  const artifactHours = artifactStatus === 'ready' ? artifacts?.timelineSummary?.hours ?? [] : [];
  const segs = bundle ? buildRiskTimeline(bundle, artifactHours) : [];
  const selectedSnapshot = selectedForecastHour !== undefined
    ? bundle?.hours.find((snap) => snap.forecastHour === selectedForecastHour)
    : undefined;
  const activeFocus = focusLocationFromSnapshot(selectedSnapshot);

  return (
    <RetroPanel
      title="Risk Timeline"
      eyebrow="08 / Period-by-period severe outlook"
      badge={<FocusLocationBadge focus={activeFocus} />}
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
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
              onClick={() => onHourChange?.(seg.representativeHour)}
              className={[
                'border-[3px] flex flex-col bg-paper transform transition-all duration-200 ease-out cursor-pointer group select-none',
                isSelected
                  ? 'border-signal-amber shadow-[6px_6px_0_0_#f7b500] hover:-translate-y-1 hover:-translate-x-1 hover:shadow-[10px_10px_0_0_#f7b500]'
                  : 'border-ink shadow-retro hover:-translate-y-1 hover:-translate-x-1 hover:shadow-[10px_10px_0_0_#111111]',
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
                    <span className="font-display font-extrabold text-[9px] tracking-widest border-[2px] border-ink bg-paper text-ink px-1 py-0.5 animate-pulse">
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
              <div className="p-3 flex flex-col gap-2.5 flex-1 justify-between">
                <div className="flex flex-col gap-2.5">
                  <div>
                    <div className="font-mono text-[9px] uppercase tracking-widest text-ink/60 mb-1">
                      Coverage
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-3.5 bg-ink border-[2px] border-ink flex gap-[1.5px] p-[1.5px] select-none">
                        {Array.from({ length: 12 }).map((_, i) => {
                          const isLit = i / 12 < seg.coverage;
                          return (
                            <div
                              key={i}
                              className={`flex-1 h-full transition-all duration-300 ${getLedColor(seg.category, isLit)}`}
                              aria-hidden
                            />
                          );
                        })}
                      </div>
                      <span className="font-mono text-[11px] font-black w-8 text-right text-ink">
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
                    <span className="font-mono text-[10px] tracking-widest text-ink/60 font-bold">
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
                </div>
                {seg.hours.length > 0 && (
                  <div className="font-mono text-[9px] uppercase tracking-widest border-t-[1px] border-ink/20 pt-1.5 relative h-5 overflow-hidden select-none">
                    <div className="absolute inset-x-0 bottom-0 top-1.5 flex items-center transition-all duration-300 transform opacity-100 translate-y-0 group-hover:opacity-0 group-hover:-translate-y-4 text-ink/40">
                      Hours: +{seg.startHour}h to +{seg.endHour}h
                    </div>
                    <div className="absolute inset-x-0 bottom-0 top-1.5 flex items-center justify-between transition-all duration-300 transform opacity-0 translate-y-4 group-hover:opacity-100 group-hover:translate-y-0 text-signal-amber font-black">
                      <span>JUMP TO period peak</span>
                      <span>F{String(seg.representativeHour).padStart(2, '0')} ▸</span>
                    </div>
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
    <div className="border-[2px] border-signal-amber bg-ink p-1.5 select-none flex flex-col justify-between min-w-0 shadow-retro-sm">
      <div className="font-mono text-[8px] uppercase tracking-wider text-signal-lime/60 truncate">
        {label}
      </div>
      <div className="font-mono font-black text-[13px] leading-none text-signal-lime tracking-wide mt-1 truncate">
        {value}
      </div>
      <div className="font-mono text-[7px] uppercase tracking-wider text-signal-lime/40 mt-0.5 truncate">
        {unit}
      </div>
    </div>
  );
}
