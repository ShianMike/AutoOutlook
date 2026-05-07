import { useRef, useState } from 'react';
import { toPng } from 'html-to-image';
import type { HourSnapshot } from '../types/forecast';
import { FORECAST_HOUR_LABELS } from '../types/forecast';
import RetroPanel from './retro/RetroPanel';
import RetroBadge from './retro/RetroBadge';
import HazardOutlookMap from './HazardOutlookMap';
import GeneratedOutlookMap from './GeneratedOutlookMap';
import GeneratedHazardProbabilityMap, { hasGeneratedHazardTile } from './GeneratedHazardProbabilityMap';
import ForecastDisclaimer from './ForecastDisclaimer';
import type { OutlookArtifactState } from '../hooks/useOutlookArtifacts';

interface OutlookMapPanelProps {
  snapshot: HourSnapshot | null;
  outlookArtifacts: OutlookArtifactState;
}

type OutlookMode = 'levels' | 'hazards';

function fmtCoord(lat: number, lon: number): string {
  const ns = lat >= 0 ? 'N' : 'S';
  const ew = lon >= 0 ? 'E' : 'W';
  return `${Math.abs(lat).toFixed(1)}°${ns} ${Math.abs(lon).toFixed(1)}°${ew}`;
}

function waitForPaint(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

function fmtUTC(iso: string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}${String(d.getUTCMinutes()).padStart(2, '0')}Z`;
}

function isNewerCycle(candidateISO: string | undefined, selectedISO: string | undefined): boolean {
  const candidateMs = Date.parse(candidateISO ?? '');
  const selectedMs = Date.parse(selectedISO ?? '');
  return Number.isFinite(candidateMs) && Number.isFinite(selectedMs) && candidateMs > selectedMs;
}

export default function OutlookMapPanel({ snapshot, outlookArtifacts }: OutlookMapPanelProps) {
  const [mode, setMode] = useState<OutlookMode>('levels');
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const exportRef = useRef<HTMLDivElement | null>(null);
  const artifactMetadata = outlookArtifacts.artifacts?.metadata;
  const latestCandidate = artifactMetadata?.latestExtendedCandidate ?? undefined;
  const staleArtifacts = isNewerCycle(latestCandidate?.cycleTimeISO, artifactMetadata?.cycleTimeISO);
  const generatedHazardsReady = hasGeneratedHazardTile(outlookArtifacts.artifacts, snapshot?.forecastHour, outlookArtifacts.status);
  const mlDriven = Boolean(snapshot?.mlHazards);
  const useRuleHazardFallback = !mlDriven && outlookArtifacts.status === 'missing';
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
  const timeRows = [
    ['HRRR cycle', artifactMetadata?.cycle ?? fmtUTC(artifactMetadata?.cycleTimeISO)],
    ['Forecast valid', fmtUTC(snapshot?.validTimeISO)],
    ['Artifact generated', fmtUTC(artifactMetadata?.generatedAtISO)],
  ] as const;

  const saveCurrentMap = async () => {
    if (!snapshot || !exportRef.current || isExporting) return;
    setIsExporting(true);
    setExportError(null);
    try {
      await waitForPaint();
      if (!exportRef.current) return;
      const exportWidth = Math.max(exportRef.current.scrollWidth, 1120);
      const dataUrl = await toPng(exportRef.current, {
        backgroundColor: '#f5f0e6',
        cacheBust: true,
        skipFonts: true,
        pixelRatio: 2,
        width: exportWidth,
        style: {
          width: `${exportWidth}px`,
          maxWidth: 'none',
          overflow: 'visible',
        },
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
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setExportError(`Export failed: ${message}`);
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
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-[3px] border-ink bg-paper p-2 shadow-retro-sm">
        <div className="border-[2px] border-ink bg-paper px-2 py-1 font-mono text-[9px] font-bold uppercase tracking-[0.28em] text-ink shadow-retro-sm">
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
        {exportError && (
          <div className="basis-full border-[2px] border-signal-red bg-paper px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-widest text-signal-red">
            {exportError}
          </div>
        )}
      </div>

      <div className="mb-2 grid grid-cols-1 gap-2 lg:grid-cols-[1fr_auto]">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {timeRows.map(([label, value]) => (
            <div key={label} className="border-[2px] border-ink bg-paper px-2 py-1.5 shadow-retro-sm">
              <div className="font-mono text-[8px] font-bold uppercase tracking-[0.24em] text-ink/55">{label}</div>
              <div className="mt-0.5 font-mono text-[11px] font-bold uppercase tracking-wider text-ink">{value}</div>
            </div>
          ))}
        </div>
        <div
          className={[
            'border-[2px] border-ink px-2 py-1.5 font-mono text-[10px] font-bold uppercase tracking-widest shadow-retro-sm',
            staleArtifacts ? 'bg-signal-amber text-ink' : 'bg-paper text-ink/65',
          ].join(' ')}
        >
          {staleArtifacts
            ? `Artifact lag: latest ${latestCandidate?.label ?? 'extended HRRR'}`
            : `Cycle policy: ${artifactMetadata?.cyclePolicy?.name ?? '—'}`}
        </div>
      </div>

      <div ref={exportRef} className="bg-paper" data-testid="outlook-export-area">
        <div
          className={[
            'flex-wrap items-center justify-between gap-3 border-[3px] border-b-0 border-ink bg-paper px-3 py-2',
            isExporting ? 'flex' : 'hidden',
          ].join(' ')}
        >
          <div className="w-[320px] shrink-0 overflow-hidden border-[3px] border-ink bg-paper px-3 py-2 shadow-retro-sm">
            <div
              className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap font-display text-[18px] font-extrabold uppercase leading-none tracking-normal text-ink"
              title="AutoOutlook"
            >
              AUTO<span className="text-signal-amber">OUTLOOK</span>
            </div>
            <div className="mt-1 max-w-full overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[9px] font-bold uppercase tracking-[0.2em] text-ink/65">
              autooutlook.tech
            </div>
          </div>
          <div className="min-w-[280px] flex-1 text-center">
            <div className="font-mono text-[9px] font-bold uppercase tracking-[0.32em] text-ink/55">
              Automated Convective Risk Intelligence
            </div>
            <div className="mt-1 font-display text-[18px] font-extrabold uppercase tracking-wide text-ink">
              F{String(snapshot?.forecastHour ?? 0).padStart(3, '0')}h {mode === 'levels' ? 'Risk Levels' : 'Hazard Probabilities'}
            </div>
          </div>
          <div className="border-[2px] border-ink bg-ink px-2.5 py-1.5 font-mono text-[10px] font-bold uppercase tracking-widest text-paper shadow-retro-sm">
            {hourLabel}
          </div>
        </div>
        {/* Header strip — mimics the rawinsonde valid/init header */}
        <div
          className={[
            'flex-wrap items-center justify-between gap-x-4 gap-y-1 border-[3px] border-b-0 border-ink bg-ink text-paper px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest',
            isExporting ? 'flex' : 'hidden',
          ].join(' ')}
        >
          <span className="shrink-0">HRRR cycle: {fmtUTC(artifactMetadata?.cycleTimeISO)}</span>
          <span className="shrink-0">Forecast valid: {validTime}</span>
          <span className="shrink-0">Generated: {fmtUTC(artifactMetadata?.generatedAtISO)}</span>
          <span className="min-w-[220px] flex-1 text-center leading-snug text-paper/80">
            {snapshot ? snapshot.region.label : 'AWAITING REGION DETECTION…'}
          </span>
          <span className="text-paper/80">{cape}</span>
          <span className="text-paper/80">{shear}</span>
          <span className="shrink-0">{snapshot ? fmtCoord(snapshot.region.centerLat, snapshot.region.centerLon) : '—'}</span>
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
            {generatedHazardsReady ? (
              <>
                <GeneratedHazardProbabilityMap
                  snapshot={snapshot}
                  hazard="thunder"
                  title="Thunderstorm Outlook"
                  artifacts={outlookArtifacts.artifacts}
                  status={outlookArtifacts.status}
                />
                <GeneratedHazardProbabilityMap
                  snapshot={snapshot}
                  hazard="hail"
                  title="Hail Outlook"
                  artifacts={outlookArtifacts.artifacts}
                  status={outlookArtifacts.status}
                />
                <GeneratedHazardProbabilityMap
                  snapshot={snapshot}
                  hazard="wind"
                  title="Damaging Wind Outlook"
                  artifacts={outlookArtifacts.artifacts}
                  status={outlookArtifacts.status}
                />
                <GeneratedHazardProbabilityMap
                  snapshot={snapshot}
                  hazard="tornado"
                  title="Tornado Outlook"
                  artifacts={outlookArtifacts.artifacts}
                  status={outlookArtifacts.status}
                />
              </>
            ) : useRuleHazardFallback ? (
              <>
                <HazardOutlookMap snapshot={snapshot} hazard="thunder" title="Thunderstorm Outlook" sourceLabel="Rule fallback" />
                <HazardOutlookMap snapshot={snapshot} hazard="hail" title="Hail Outlook" sourceLabel="Rule fallback" />
                <HazardOutlookMap snapshot={snapshot} hazard="wind" title="Damaging Wind Outlook" sourceLabel="Rule fallback" />
                <HazardOutlookMap snapshot={snapshot} hazard="tornado" title="Tornado Outlook" sourceLabel="Rule fallback" />
              </>
            ) : (
              <GeneratedHazardsUnavailable message={outlookArtifacts.message} status={outlookArtifacts.status} />
            )}
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

function GeneratedHazardsUnavailable({ message, status }: { message: string | null; status: string }) {
  const isFetchingHour = status === 'loading' || status === 'pending';
  return (
    <div className="md:col-span-2 border-[3px] border-ink bg-paper min-h-[260px] flex items-center justify-center p-4 shadow-retro">
      <div className="max-w-[520px] border-[3px] border-ink bg-paper p-4 shadow-retro-sm">
        <div className="font-display text-[14px] font-extrabold uppercase tracking-wider">
          {isFetchingHour ? 'Forecast hour unavailable' : 'Generated hazard tiles unavailable'}
        </div>
        <p className="mt-2 font-mono text-[11px] leading-relaxed text-ink/70">
          {status === 'loading'
            ? 'Selected forecast hour is still fetching generated hazard tiles.'
            : status === 'pending'
              ? message ?? 'Selected forecast hour is still generating.'
            : message ?? 'Selected forecast hour does not have a generated HRRR/XGBoost probability tile yet.'}
        </p>
      </div>
    </div>
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
        'retro-button min-h-8 px-3 py-1.5 text-[12px] leading-none',
        disabled ? 'cursor-not-allowed opacity-50' : '',
        active
          ? 'bg-signal-amber text-ink translate-x-[2px] translate-y-[2px] shadow-[1px_1px_0_0_#111111] hover:bg-signal-amber hover:text-ink'
          : 'bg-paper text-ink hover:bg-signal-amber hover:text-ink',
      ].join(' ')}
    >
      {children}
    </button>
  );
}
