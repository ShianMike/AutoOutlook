import type { ForecastBundle } from '../types/forecast';
import { FORECAST_HOUR_LABELS } from '../types/forecast';
import RetroButton from './retro/RetroButton';
import RetroBadge from './retro/RetroBadge';

interface ForecastTimeSliderProps {
  bundle: ForecastBundle | null;
  index: number;
  isPlaying: boolean;
  onIndexChange: (i: number) => void;
  onNext: () => void;
  onPrev: () => void;
  onTogglePlay: () => void;
}

function fmtValid(iso?: string): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const day = `${String(d.getUTCMonth() + 1).padStart(2, '0')}/${String(d.getUTCDate()).padStart(2, '0')}`;
  const hr = String(d.getUTCHours()).padStart(2, '0');
  const mn = String(d.getUTCMinutes()).padStart(2, '0');
  return `${day} · ${hr}${mn}Z`;
}

export default function ForecastTimeSlider({
  bundle,
  index,
  isPlaying,
  onIndexChange,
  onNext,
  onPrev,
  onTogglePlay,
}: ForecastTimeSliderProps) {
  const stops = bundle?.hours ?? [];
  const totalStops = Math.max(stops.length, 1);
  const safeIndex = stops.length > 0 ? Math.max(0, Math.min(index, stops.length - 1)) : 0;
  const current = stops[safeIndex];
  const atStart = safeIndex <= 0;
  const atEnd = stops.length === 0 || safeIndex >= stops.length - 1 || (current?.forecastHour ?? 0) >= 48;
  const isHourly = stops.length > 24;

  return (
    <section className="bg-paper border-[3px] border-ink shadow-retro p-2 sm:p-3">
      <div className="flex items-center justify-between gap-3 mb-1.5 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <RetroBadge tone="ink">
            <span className="font-mono">FORECAST · TIME SCRUBBER</span>
          </RetroBadge>
          <span className="font-display text-lg sm:text-xl font-extrabold text-ink leading-none">
            {fmtValid(current?.validTimeISO)}
          </span>
          <span className="hidden lg:inline font-mono text-[10px] uppercase tracking-widest text-ink/60">
            ←/→ step · space play
          </span>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <RetroBadge tone="paper">
            HOUR {current ? FORECAST_HOUR_LABELS[current.forecastHour] : '—'}
          </RetroBadge>
          <RetroBadge tone={isPlaying ? 'lime' : 'paper'} pulse={isPlaying}>
            {isPlaying ? 'AUTOSTEP' : 'PAUSED'}
          </RetroBadge>
          <RetroButton onClick={onPrev} aria-label="Previous forecast hour" iconOnly disabled={atStart}>
            <Triangle direction="left" />
          </RetroButton>
          <RetroButton
            onClick={onTogglePlay}
            primary={isPlaying}
            disabled={stops.length === 0 || (!isPlaying && atEnd)}
            aria-label={isPlaying ? 'Pause animation' : 'Play animation'}
          >
            {isPlaying ? '■  PAUSE' : '▶  PLAY'}
          </RetroButton>
          <RetroButton onClick={onNext} aria-label="Next forecast hour" iconOnly disabled={atEnd}>
            <Triangle direction="right" />
          </RetroButton>
        </div>
      </div>

      {/* The track itself */}
      <div className="relative">
        {/* The thick black rail */}
        <div className="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-[6px] bg-ink" aria-hidden />
        {/* Filled portion */}
        <div
          className="absolute left-0 top-1/2 -translate-y-1/2 h-[6px] bg-signal-amber border-t-[2px] border-b-[2px] border-ink"
          style={{ width: `${(safeIndex / Math.max(totalStops - 1, 1)) * 100}%` }}
          aria-hidden
        />
        {/* Stops */}
        <div className={`relative flex items-center justify-between ${isHourly ? 'gap-0 py-1.5' : 'gap-1 py-2'}`}>
          {stops.map((stop, i) => {
            const isActive = i === safeIndex;
            const lbl = FORECAST_HOUR_LABELS[stop.forecastHour] ?? `+${stop.forecastHour}h`;
            const showLabel = !isHourly;
            return (
              <button
                key={stop.forecastHour}
                type="button"
                onClick={() => onIndexChange(i)}
                aria-current={isActive ? 'step' : undefined}
                aria-label={`Forecast hour ${lbl}`}
                className="relative flex flex-col items-center group cursor-pointer"
              >
                <span
                  className={[
                    'block rotate-45 border-ink transition-all',
                    isHourly ? 'w-[8px] h-[8px] border-[2px]' : 'w-[20px] h-[20px] border-[3px]',
                    isActive ? 'bg-signal-amber shadow-retro-sm scale-110' : 'bg-paper hover:bg-signal-amber',
                  ].join(' ')}
                />
                {showLabel && (
                  <span
                    className={[
                      'font-display font-extrabold uppercase tracking-wider transition-colors',
                      isHourly ? 'text-[7px]' : 'text-[11px]',
                      isActive ? 'text-ink' : 'text-ink/60 group-hover:text-ink',
                    ].join(' ')}
                  >
                    {lbl}
                  </span>
                )}
              </button>
            );
          })}
          {stops.length === 0 && (
            <div className="flex-1 text-center text-ink/40 font-mono text-[11px] py-3">
              Awaiting forecast bundle…
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function Triangle({ direction }: { direction: 'left' | 'right' }) {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
      {direction === 'left' ? (
        <polygon points="11,1 11,13 2,7" fill="currentColor" />
      ) : (
        <polygon points="3,1 3,13 12,7" fill="currentColor" />
      )}
    </svg>
  );
}
