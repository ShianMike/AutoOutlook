import type { ReactNode } from 'react';
import type { ForecastBundle } from '../types/forecast';
import { FORECAST_HOUR_LABELS } from '../types/forecast';
import type { FetchStatus } from '../hooks/useAutoForecast';
import type { OutlookArtifactState } from '../hooks/useOutlookArtifacts';
import type { FetchResult } from '../utils/fetchLatestForecast';
import RetroPanel from './retro/RetroPanel';
import RetroBadge from './retro/RetroBadge';
import RetroButton from './retro/RetroButton';

interface SystemStatusPanelProps {
  bundle: ForecastBundle | null;
  status: FetchStatus;
  attempted: FetchResult['attemptedProviders'];
  selectedHour: number | undefined;
  selectedValidTime: string | undefined;
  outlookArtifacts: OutlookArtifactState;
  refreshIntervalMs: number;
  onRefresh: () => void;
}

const PROVIDER_LABELS: Record<string, string> = {
  backend: 'Python · NOMADS + MetPy',
  openMeteo: 'Open-Meteo GFS',
  mock: 'Deterministic mock',
};

export default function SystemStatusPanel({
  bundle,
  status,
  attempted,
  selectedHour,
  selectedValidTime,
  outlookArtifacts,
  refreshIntervalMs,
  onRefresh,
}: SystemStatusPanelProps) {
  const minutes = Math.round(refreshIntervalMs / 60000);
  const artifactIndex = outlookArtifacts.artifacts?.incrementalIndex;
  const artifactMetadata = outlookArtifacts.artifacts?.metadata;
  const readyHours = artifactIndex?.readyForecastHours ?? artifactMetadata?.readyForecastHours ?? [];
  const requestedHours = artifactIndex?.requestedForecastHours ?? artifactMetadata?.requestedForecastHours ?? artifactMetadata?.forecastHours ?? [];
  const failedHours = artifactIndex?.failedForecastHours ?? artifactMetadata?.failedForecastHours ?? [];
  const pendingHours = artifactIndex?.pendingForecastHours ?? artifactMetadata?.pendingForecastHours ?? [];
  const selectedArtifactHour = outlookArtifacts.artifacts?.selectedArtifactForecastHour ?? artifactMetadata?.selectedArtifactForecastHour;
  const artifactReadyPct = requestedHours.length > 0 ? Math.round((readyHours.length / requestedHours.length) * 100) : 0;
  const cycleSynced = Boolean(bundle?.issuedAtISO && artifactMetadata?.cycleTimeISO && sameHour(bundle.issuedAtISO, artifactMetadata.cycleTimeISO));
  const latestCandidate = artifactMetadata?.latestExtendedCandidate ?? undefined;
  const staleArtifacts = isNewerCycle(latestCandidate?.cycleTimeISO, artifactMetadata?.cycleTimeISO);
  const fetchStatusLabel =
    status === 'loading' ? 'FETCHING'
      : status === 'error' ? 'ERROR'
      : status === 'success' ? 'OK'
      : 'IDLE';
  const artifactStatusLabel = outlookArtifacts.status === 'ready' ? 'READY'
    : outlookArtifacts.status === 'loading' ? 'LOADING'
      : outlookArtifacts.status === 'pending' ? 'PENDING'
        : outlookArtifacts.status === 'failed' ? 'FAILED'
          : outlookArtifacts.status === 'missing' ? 'MISSING'
            : outlookArtifacts.status === 'error' ? 'ERROR'
              : 'IDLE';
  const fallbackTone = bundle?.source === 'live' ? 'lime'
    : bundle?.source === 'fallback' ? 'amber' : 'cyan';
  const artifactTone = outlookArtifacts.status === 'ready' ? 'lime'
    : outlookArtifacts.status === 'loading' || outlookArtifacts.status === 'pending' ? 'amber'
      : outlookArtifacts.status === 'missing' ? 'cyan'
        : 'red';

  return (
    <RetroPanel
      title="System Status"
      eyebrow="08 / Pipeline telemetry"
      badge={<RetroBadge tone={status === 'loading' ? 'amber' : status === 'error' ? 'red' : 'lime'} pulse={status === 'loading'}>
        {fetchStatusLabel}
      </RetroBadge>}
    >
      <div className="grid grid-cols-1 xl:grid-cols-[1.1fr_1fr] gap-3">
        <div className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-2 gap-2">
          <StatusTile label="Forecast Feed" value={fetchStatusLabel} sub={bundle?.cycle ?? 'No bundle'} tone={status === 'error' ? 'red' : status === 'loading' ? 'amber' : 'lime'} />
          <StatusTile label="Artifacts" value={artifactStatusLabel} sub={`${readyHours.length}/${requestedHours.length || '—'} ready`} tone={artifactTone} />
          <StatusTile
            label="HRRR Cycle"
            value={staleArtifacts ? 'STALE' : cycleSynced ? 'LOCKED' : 'CHECK'}
            sub={artifactMetadata?.cycle ?? 'Artifact cycle pending'}
            tone={staleArtifacts ? 'amber' : cycleSynced ? 'lime' : 'amber'}
          />
          <StatusTile label="Refresh" value={`${minutes} MIN`} sub={bundle ? `${bundle.latencyMs} ms last fetch` : 'Awaiting fetch'} tone="paper" />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-1 gap-3">
          <InfoCard title="Selected Valid Hour" badge={selectedArtifactHour !== undefined ? `F${String(selectedArtifactHour).padStart(2, '0')}` : 'F--'}>
            <KvGrid rows={[
              ['Forecast hour', selectedHour !== undefined ? FORECAST_HOUR_LABELS[selectedHour] ?? `+${selectedHour}h` : '—'],
              ['Forecast valid time', fmtUTC(selectedValidTime)],
              ['HRRR cycle time', fmtUTC(artifactMetadata?.cycleTimeISO)],
              ['Generated hour', selectedArtifactHour !== undefined ? `F${String(selectedArtifactHour).padStart(2, '0')}` : '—'],
              ['Artifact valid time', fmtUTC(artifactMetadata?.artifactValidTimeISO)],
              ['Artifact generated', fmtUTC(artifactMetadata?.generatedAtISO)],
              ['Latest candidate', latestCandidate?.label ?? '—'],
            ]} />
          </InfoCard>

          <InfoCard title="Generated Artifact Pipeline" badge={`${artifactReadyPct}%`}>
            <div className="flex items-center gap-2 mb-2">
              <div className="h-3 flex-1 border-[2px] border-ink bg-paper overflow-hidden">
                <div className="h-full bg-signal-lime border-r-[2px] border-ink" style={{ width: `${artifactReadyPct}%` }} />
              </div>
              <span className="font-mono text-[10px] font-bold w-14 text-right">
                {readyHours.length}/{requestedHours.length || '—'}
              </span>
            </div>
            <KvGrid rows={[
              ['Ready', compactHourRange(readyHours)],
              ['Pending', compactHourRange(pendingHours)],
              ['Failed', compactHourRange(failedHours)],
              ['Cycle policy', artifactMetadata?.cyclePolicy?.name ?? '—'],
              ['Required cycle hour', artifactMetadata?.requiredForecastHourForCycle !== undefined ? `F${String(artifactMetadata.requiredForecastHourForCycle).padStart(2, '0')}` : '—'],
              ['Fallback reason', artifactMetadata?.fallbackReason ?? (staleArtifacts ? 'Artifacts older than latest extended HRRR candidate.' : 'none')],
            ]} />
          </InfoCard>
        </div>

        <InfoCard
          title="Forecast Provider Chain"
          badge={(bundle?.source ?? 'BOOTING').toUpperCase()}
          badgeTone={fallbackTone}
          className="xl:col-span-2"
        >
          <ul className="grid grid-cols-1 lg:grid-cols-3 gap-2 font-mono text-[11px]">
            {(['backend', 'openMeteo', 'mock'] as const).map((id, idx) => {
              const att = attempted.find((a) => a.id === id);
              const isWinner = bundle?.providerId === id;
              const itemTone =
                isWinner ? 'bg-signal-lime' :
                att && att.ok ? 'bg-paper' :
                att && !att.ok ? 'bg-signal-red text-paper' :
                'bg-paper';
              return (
                <li key={id} className={`border-[2px] border-ink p-2 flex flex-col gap-1 min-w-0 ${itemTone}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-display uppercase text-[11px] tracking-wider font-extrabold truncate">
                      {idx + 1}. {PROVIDER_LABELS[id]}
                    </span>
                    <span className="font-mono text-[9px] uppercase tracking-widest shrink-0">
                      {isWinner ? 'ACTIVE' : att?.ok ? 'OK' : att ? 'FAIL' : 'WAIT'}
                    </span>
                  </div>
                  <span className="font-mono text-[9px] leading-snug opacity-70 break-words">
                    {att?.error ?? (isWinner ? bundle?.providerNotes ?? 'Live provider selected.' : att?.ok ? 'Available fallback.' : 'Not attempted yet.')}
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="border-t-[2px] border-ink mt-2 pt-2 flex items-center justify-between gap-2 flex-wrap">
            <span className="font-mono text-[10px] uppercase tracking-widest text-ink/60">
              Forecast and artifact data refresh independently.
            </span>
            <RetroButton onClick={onRefresh} className="text-[11px] !py-1.5">
              ↻ REFRESH NOW
            </RetroButton>
          </div>
        </InfoCard>
      </div>
    </RetroPanel>
  );
}

function StatusTile({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub: string;
  tone: 'paper' | 'lime' | 'amber' | 'red' | 'cyan';
}) {
  const toneClass = tone === 'lime' ? 'bg-signal-lime'
    : tone === 'amber' ? 'bg-signal-amber'
      : tone === 'red' ? 'bg-signal-red text-paper'
        : tone === 'cyan' ? 'bg-signal-cyan'
          : 'bg-paper';
  return (
    <div className={`border-[3px] border-ink shadow-retro-sm p-2 min-w-0 ${toneClass}`}>
      <div className="font-mono text-[9px] uppercase tracking-[0.22em] opacity-65 truncate">{label}</div>
      <div className="font-display text-[18px] font-extrabold uppercase leading-none mt-1 truncate">{value}</div>
      <div className="font-mono text-[9px] uppercase tracking-widest opacity-70 mt-1 truncate">{sub}</div>
    </div>
  );
}

function InfoCard({
  title,
  badge,
  badgeTone = 'paper',
  className = '',
  children,
}: {
  title: string;
  badge: string;
  badgeTone?: 'paper' | 'ink' | 'lime' | 'amber' | 'red' | 'cyan' | 'orange';
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`border-[2px] border-ink bg-paper min-w-0 ${className}`}>
      <div className="border-b-[2px] border-ink px-2 py-1.5 flex items-center justify-between gap-2 bg-ink text-paper">
        <span className="font-mono text-[11px] uppercase tracking-widest truncate">{title}</span>
        <RetroBadge tone={badgeTone}>{badge}</RetroBadge>
      </div>
      <div className="p-2">{children}</div>
    </div>
  );
}

function KvGrid({ rows }: { rows: Array<[string, string]> }) {
  return (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 font-mono text-[11px]">
      {rows.map(([k, v]) => (
        <div key={k} className="border-[1.5px] border-ink/40 px-2 py-1 min-w-0">
          <dt className="text-[8px] uppercase tracking-widest text-ink/50 truncate">{k}</dt>
          <dd className="font-bold break-words leading-snug">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function fmtUTC(iso: string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}${String(d.getUTCMinutes()).padStart(2, '0')}Z`;
}

function sameHour(a: string, b: string): boolean {
  const aMs = Date.parse(a);
  const bMs = Date.parse(b);
  return Number.isFinite(aMs) && Number.isFinite(bMs) && Math.abs(aMs - bMs) < 60 * 1000;
}

function isNewerCycle(candidateISO: string | undefined, selectedISO: string | undefined): boolean {
  const candidateMs = Date.parse(candidateISO ?? '');
  const selectedMs = Date.parse(selectedISO ?? '');
  return Number.isFinite(candidateMs) && Number.isFinite(selectedMs) && candidateMs > selectedMs;
}

function compactHourRange(hours: number[]): string {
  if (!hours.length) return 'none';
  const sorted = [...new Set(hours)].sort((a, b) => a - b);
  const ranges: string[] = [];
  let start = sorted[0];
  let prev = sorted[0];
  for (let i = 1; i <= sorted.length; i += 1) {
    const hour = sorted[i];
    if (hour === prev + 1) {
      prev = hour;
      continue;
    }
    ranges.push(start === prev ? `F${String(start).padStart(2, '0')}` : `F${String(start).padStart(2, '0')}-F${String(prev).padStart(2, '0')}`);
    start = hour;
    prev = hour;
  }
  return ranges.join(', ');
}
