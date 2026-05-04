import type { ForecastBundle } from '../types/forecast';
import { FORECAST_HOUR_LABELS } from '../types/forecast';
import type { FetchStatus } from '../hooks/useAutoForecast';
import type { FetchResult } from '../utils/fetchLatestForecast';
import RetroPanel from './retro/RetroPanel';
import RetroBadge from './retro/RetroBadge';

interface SystemStatusPanelProps {
  bundle: ForecastBundle | null;
  status: FetchStatus;
  attempted: FetchResult['attemptedProviders'];
  selectedHour: number | undefined;
  selectedValidTime: string | undefined;
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
  refreshIntervalMs,
  onRefresh,
}: SystemStatusPanelProps) {
  const minutes = Math.round(refreshIntervalMs / 60000);
  const fetchStatusLabel =
    status === 'loading' ? 'FETCHING'
      : status === 'error' ? 'ERROR'
      : status === 'success' ? 'OK'
      : 'IDLE';
  const fallbackTone = bundle?.source === 'live' ? 'lime'
    : bundle?.source === 'fallback' ? 'amber' : 'cyan';

  return (
    <RetroPanel
      title="System Status"
      eyebrow="10 / Pipeline telemetry"
      badge={<RetroBadge tone={status === 'loading' ? 'amber' : status === 'error' ? 'red' : 'lime'} pulse={status === 'loading'}>
        {fetchStatusLabel}
      </RetroBadge>}
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <table className="w-full font-mono text-[12px] border-[2px] border-ink bg-paper">
          <tbody>
            <KvRow k="Source" v={bundle?.providerNotes ?? '—'} />
            <KvRow k="Cycle" v={bundle?.cycle ?? '—'} />
            <KvRow k="Forecast Hour" v={selectedHour !== undefined ? FORECAST_HOUR_LABELS[selectedHour] ?? `+${selectedHour}h` : '—'} />
            <KvRow k="Valid Time" v={fmtUTC(selectedValidTime)} />
            <KvRow k="Refresh Interval" v={`${minutes} min`} />
            <KvRow k="Latency" v={bundle ? `${bundle.latencyMs} ms` : '—'} />
          </tbody>
        </table>
        <div className="border-[2px] border-ink bg-paper">
          <div className="border-b-[2px] border-ink px-2 py-1.5 flex items-center justify-between bg-ink text-paper">
            <span className="font-mono text-[11px] uppercase tracking-widest">
              Provider chain
            </span>
            <RetroBadge tone={fallbackTone}>{(bundle?.source ?? 'BOOTING').toUpperCase()}</RetroBadge>
          </div>
          <ul className="p-2 flex flex-col gap-1.5 font-mono text-[11px]">
            {(['backend', 'openMeteo', 'mock'] as const).map((id, idx) => {
              const att = attempted.find((a) => a.id === id);
              const isWinner = bundle?.providerId === id;
              const tone =
                isWinner ? 'bg-signal-lime' :
                att && att.ok ? 'bg-paper' :
                att && !att.ok ? 'bg-signal-red text-paper' :
                'bg-paper';
              return (
                <li key={id} className={`border-[2px] border-ink p-1.5 flex items-center justify-between gap-2 ${tone}`}>
                  <div className="flex items-center gap-2">
                    <span className="font-bold">{idx + 1}.</span>
                    <span className="font-display uppercase text-[11px] tracking-wider font-extrabold">
                      {PROVIDER_LABELS[id]}
                    </span>
                  </div>
                  <span className="font-mono text-[10px] uppercase tracking-widest">
                    {isWinner ? 'WINNER' : att?.ok ? 'SKIPPED' : att ? 'FAIL' : 'PENDING'}
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="border-t-[2px] border-ink p-2 flex items-center justify-between gap-2">
            <span className="font-mono text-[10px] uppercase tracking-widest text-ink/60">
              Manual refresh
            </span>
            <button
              type="button"
              onClick={onRefresh}
              className="retro-button text-[11px] !py-1.5"
            >
              ↻ REFRESH NOW
            </button>
          </div>
        </div>
      </div>
    </RetroPanel>
  );
}

function KvRow({ k, v }: { k: string; v: string }) {
  return (
    <tr className="border-b-[1px] border-ink/20 last:border-b-0">
      <td className="px-2 py-1.5 text-ink/60 uppercase tracking-widest text-[10px] w-[40%]">{k}</td>
      <td className="px-2 py-1.5 font-bold break-all">{v}</td>
    </tr>
  );
}

function fmtUTC(iso: string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}${String(d.getUTCMinutes()).padStart(2, '0')}Z`;
}
