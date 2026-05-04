import { useRef, useState } from 'react';
import { toPng } from 'html-to-image';
import type { HourSnapshot } from '../types/forecast';
import { FORECAST_HOUR_LABELS } from '../types/forecast';
import RetroPanel from './retro/RetroPanel';
import RetroBadge from './retro/RetroBadge';
import HazardOutlookMap from './HazardOutlookMap';
import GeneratedOutlookMap from './GeneratedOutlookMap';
import ForecastDisclaimer from './ForecastDisclaimer';
import { useOutlookArtifacts } from '../hooks/useOutlookArtifacts';

interface OutlookMapPanelProps {
  snapshot: HourSnapshot | null;
}

type OutlookMode = 'levels' | 'hazards';

function fmtCoord(lat: number, lon: number): string {
  const ns = lat >= 0 ? 'N' : 'S';
  const ew = lon >= 0 ? 'E' : 'W';
  return `${Math.abs(lat).toFixed(1)}°${ns} ${Math.abs(lon).toFixed(1)}°${ew}`;
}

export default function OutlookMapPanel({ snapshot }: OutlookMapPanelProps) {
  const [mode, setMode] = useState<OutlookMode>('levels');
  const [isExporting, setIsExporting] = useState(false);
  const exportRef = useRef<HTMLDivElement | null>(null);
  const outlookArtifacts = useOutlookArtifacts();
  const mlDriven = Boolean(snapshot?.mlHazards);
  const engineLabel = mlDriven
    ? outlookArtifacts.status === 'ready'
      ? 'Auto-generated · HRRR/XGBoost artifact pipeline'
      : 'Auto-generated · XGBoost hazard model · artifact pending'
    : 'Auto-generated · rule-based outlook engine v1';
  const hourLabel = snapshot
    ? FORECAST_HOUR_LABELS[snapshot.forecastHour] ?? `+${snapshot.forecastHour}h`
    : '—';

  const validTime = snapshot
    ? (() => {
        const d = new Date(snapshot.validTimeISO);
        const hh = String(d.getUTCHours()).padStart(2, '0');
        const mm = String(d.getUTCMinutes()).padStart(2, '0');
        return `${hh}${mm}Z ${String(d.getUTCDate()).padStart(2, '0')}/${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
      })()
    : '—';

  const shear = snapshot ? `${Math.round(snapshot.ingredients.shear06Kt)} kt SHR` : '—';
  const cape = snapshot ? `${Math.round(snapshot.ingredients.mucape)} CAPE` : '—';

  const saveCurrentMap = async () => {
    if (!snapshot || !exportRef.current || isExporting) return;
    setIsExporting(true);
    try {
      const dataUrl = await toPng(exportRef.current, {
        backgroundColor: '#f5f0e6',
        cacheBust: true,
        pixelRatio: 2,
      });
      const validStamp = snapshot.validTimeISO
        .replace(/[:.]/g, '')
        .replace('T', '_')
        .replace('Z', 'z');
      const filename = `autooutlook_${mode}_F${String(snapshot.forecastHour).padStart(3, '0')}_${validStamp}.png`;
      const link = document.createElement('a');
      link.href = dataUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <RetroPanel
      title={`F${String(snapshot?.forecastHour ?? 0).padStart(3, '0')}h Automated Convective Outlook`}
      eyebrow="03 / automated categorical + hazard outlook · auto-detected focus region"
      badge={<RetroBadge tone="paper">FCST · {hourLabel}</RetroBadge>}
      size="sm"
      className="[&>div]:p-2"
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-[3px] border-ink bg-paper p-2">
        <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-ink/60">
          Forecast type
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <ModeButton active={mode === 'levels'} onClick={() => setMode('levels')}>
            Risk Levels
          </ModeButton>
          <ModeButton active={mode === 'hazards'} onClick={() => setMode('hazards')}>
            Hazard Probs
          </ModeButton>
          <ModeButton active={false} onClick={saveCurrentMap} disabled={!snapshot || isExporting}>
            {isExporting ? 'Saving…' : `Save ${mode === 'levels' ? 'Levels' : 'Hazards'} PNG`}
          </ModeButton>
        </div>
      </div>

      <div ref={exportRef} className="bg-paper" data-testid="outlook-export-area">
        {/* Header strip — mimics the rawinsonde valid/init header */}
        <div className="flex items-center justify-between gap-3 border-[3px] border-b-0 border-ink bg-ink text-paper px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest">
          <span>Valid: {validTime}</span>
          <span className="hidden md:inline truncate max-w-[55%] text-center text-paper/80">
            {snapshot ? snapshot.region.label : 'AWAITING REGION DETECTION…'}
          </span>
          <span className="hidden lg:inline text-paper/80">{cape}</span>
          <span className="hidden lg:inline text-paper/80">{shear}</span>
          <span>{snapshot ? fmtCoord(snapshot.region.centerLat, snapshot.region.centerLon) : '—'}</span>
        </div>

        {mode === 'levels' ? (
          <div className="border-[3px] border-ink bg-paper p-2">
            <GeneratedOutlookMap
              snapshot={snapshot}
              status={outlookArtifacts.status}
              artifacts={outlookArtifacts.artifacts}
              message={outlookArtifacts.message}
            />
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 border-[3px] border-ink bg-paper p-2">
            <HazardOutlookMap snapshot={snapshot} hazard="thunder" title="Thunderstorm Outlook" />
            <HazardOutlookMap snapshot={snapshot} hazard="hail" title="Hail Outlook" />
            <HazardOutlookMap snapshot={snapshot} hazard="wind" title="Damaging Wind Outlook" />
            <HazardOutlookMap snapshot={snapshot} hazard="tornado" title="Tornado Outlook" />
          </div>
        )}

        {/* Footer strip */}
        <div className="border-[3px] border-t-0 border-ink bg-paper px-3 py-1.5 flex items-center justify-between gap-3 flex-wrap font-mono text-[10px] uppercase tracking-widest text-ink/70">
          <span>States in focus: {snapshot?.region.states.join(' · ') ?? '—'}</span>
          <span>{engineLabel}</span>
        </div>

        <div className="border-[3px] border-t-0 border-ink bg-ink px-3 py-2 text-paper">
          <ForecastDisclaimer variant="export" />
        </div>
      </div>
    </RetroPanel>
  );
}

function ModeButton({
  active,
  children,
  onClick,
  disabled = false,
}: {
  active: boolean;
  children: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={[
        'border-[3px] border-ink px-3 py-1 font-display text-[12px] font-extrabold uppercase tracking-wider shadow-retro-sm transition-transform active:translate-x-[2px] active:translate-y-[2px] active:shadow-none',
        disabled ? 'cursor-not-allowed opacity-50' : '',
        active ? 'bg-signal-amber text-ink' : 'bg-paper text-ink hover:bg-ink hover:text-paper',
      ].join(' ')}
    >
      {children}
    </button>
  );
}
