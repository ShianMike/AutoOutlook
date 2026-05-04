import type { ForecastBundle, ForecastSource } from '../types/forecast';
import { HAZARD_META, RISK_META } from '../types/forecast';
import RetroBadge from './retro/RetroBadge';

interface CommandHeaderProps {
  bundle: ForecastBundle | null;
  selectedValidTime?: string;
  selectedHourLabel?: string;
}

function fmtTimeUTC(iso: string | undefined): { time: string; date: string } {
  if (!iso) return { time: '—', date: '' };
  const d = new Date(iso);
  const time = `${String(d.getUTCHours()).padStart(2, '0')}${String(d.getUTCMinutes()).padStart(2, '0')}Z`;
  const date = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
  return { time, date };
}

function truncateLabel(label: string, max = 22): string {
  if (label.length <= max) return label;
  // Try splitting on " — " or " - " and keep the first segment
  const dash = label.indexOf(' — ');
  if (dash > 0 && dash <= max) return label.slice(0, dash);
  return label.slice(0, max - 1) + '…';
}

function fmtCoordShort(lat: number, lon: number): string {
  const ns = lat >= 0 ? 'N' : 'S';
  const ew = lon >= 0 ? 'E' : 'W';
  return `${Math.abs(lat).toFixed(1)}°${ns} ${Math.abs(lon).toFixed(1)}°${ew}`;
}

function sourceTone(src: ForecastSource | undefined) {
  if (src === 'live') return 'lime';
  if (src === 'fallback') return 'amber';
  return 'cyan';
}

function sourceLabel(src: ForecastSource | undefined) {
  if (src === 'live') return 'LIVE';
  if (src === 'fallback') return 'FALLBACK';
  if (src === 'simulated') return 'SIMULATED';
  return 'BOOTING';
}

export default function CommandHeader({
  bundle,
  selectedValidTime,
  selectedHourLabel,
}: CommandHeaderProps) {
  const snapshot = selectedValidTime
    ? bundle?.hours.find((hour) => hour.validTimeISO === selectedValidTime)
    : bundle?.hours[0];

  return (
    <header className="bg-ink text-paper border-b-[3px] border-paper/10 relative retro-scanline">
      <div className="px-4 py-2.5 xl:px-5 flex items-center gap-4">
        {/* Brand */}
        <div className="flex items-center gap-3 min-w-fit">
          <div className="bg-paper text-ink border-[3px] border-paper px-2 py-1 font-mono text-[10px] font-bold tracking-[0.3em]">
            AO/01
          </div>
          <div className="flex flex-col">
            <h1 className="font-display text-xl font-extrabold uppercase tracking-tight leading-none">
              Auto<span className="text-signal-amber">Outlook</span>
            </h1>
            <span className="font-mono text-[9px] uppercase tracking-[0.25em] text-paper/60 mt-0.5">
              Automated Convective Risk Intelligence
            </span>
          </div>
        </div>

        {/* Divider */}
        <div className="hidden md:block w-px self-stretch bg-paper/20 shrink-0" />

        {/* Forecast grid */}
        <div className="flex-1 min-w-0 grid grid-cols-[1fr_1fr_1.4fr_0.6fr_1fr_auto] items-center gap-x-4 xl:gap-x-5 font-mono text-[11px]">
          <Stat
            label="OUTLOOK"
            value={snapshot ? RISK_META[snapshot.outlook.category].label : '—'}
            accent={snapshot ? RISK_META[snapshot.outlook.category].tw : undefined}
          />
          <Stat label="HAZARD" value={snapshot ? HAZARD_META[snapshot.outlook.mainHazard].label : '—'} />
          <Stat
            label="FOCUS"
            value={snapshot ? truncateLabel(snapshot.region.label) : '—'}
            sub={snapshot ? fmtCoordShort(snapshot.region.centerLat, snapshot.region.centerLon) : undefined}
          />
          <Stat label="CONF" value={snapshot ? `${Math.round(snapshot.outlook.confidence * 100)}%` : '—'} />
          <Stat
            label="VALID"
            value={fmtTimeUTC(selectedValidTime).time}
            sub={selectedHourLabel ? `${fmtTimeUTC(selectedValidTime).date} · ${selectedHourLabel}` : fmtTimeUTC(selectedValidTime).date || undefined}
          />
          <div className="flex items-center gap-2">
            <RetroBadge tone={sourceTone(bundle?.source)} pulse={bundle?.source === 'live'}>
              {sourceLabel(bundle?.source)}
            </RetroBadge>
          </div>
        </div>
      </div>
      {/* Bottom ticker strip */}
      <div className="border-t border-paper/15 bg-navy text-paper/70 font-mono text-[10px] uppercase tracking-widest overflow-hidden">
        <div className="flex animate-ticker whitespace-nowrap">
          <TickerSpan bundle={bundle} />
          <TickerSpan bundle={bundle} />
        </div>
      </div>
    </header>
  );
}

function Stat({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: string }) {
  return (
    <div className="flex flex-col justify-center leading-none min-w-0">
      <span className="text-paper/40 text-[9px] tracking-[0.3em] mb-1">{label}</span>
      {accent ? (
        <span className={`inline-block self-start px-1.5 py-0.5 text-[11px] font-bold tracking-wide leading-none ${accent}`}>
          {value}
        </span>
      ) : (
        <span className="text-paper font-bold text-[12px] tracking-wide truncate">{value}</span>
      )}
      {sub && <span className="mt-0.5 text-paper/40 text-[9px] tracking-[0.2em] truncate">{sub}</span>}
    </div>
  );
}

function TickerSpan({ bundle }: { bundle: ForecastBundle | null }) {
  const peak = bundle?.hours.length
    ? bundle.hours.reduce((best, hour) => (
      RISK_META[hour.outlook.category].ord > RISK_META[best.outlook.category].ord ? hour : best
    ))
    : undefined;

  const sigTag = peak?.outlook.significantSevere ? '► ⚠ SIGNIFICANT SEVERE POSSIBLE' : null;
  const items = bundle
    ? [
        `► PEAK ${peak ? RISK_META[peak.outlook.category].label : 'OUTLOOK PENDING'}`,
        `► PRIMARY ${peak ? HAZARD_META[peak.outlook.mainHazard].label : 'HAZARD PENDING'}`,
        `► FOCUS ${peak ? truncateLabel(peak.region.label, 30) : 'REGION PENDING'} (AUTO-DETECTED)`,
        ...(sigTag ? [sigTag] : []),
        `► ${peak?.outlook.headline ?? 'GENERATING FORECAST HEADLINE'}`,
        `► ${bundle.hours.length} FORECAST HOURS LOADED`,
      ]
    : ['► BOOTING AUTOOUTLOOK', '► LOADING FORECAST PROVIDERS', '► STAND BY'];
  return (
    <div className="flex shrink-0">
      {items.map((t, i) => (
        <span key={i} className="px-5 py-1">{t}</span>
      ))}
    </div>
  );
}
