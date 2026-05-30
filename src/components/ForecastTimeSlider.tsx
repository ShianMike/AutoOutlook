import { useCallback, useEffect, useRef } from 'react';
import type { PointerEvent } from 'react';
import type { ForecastBundle } from '../types/forecast';
import { FORECAST_HOUR_LABELS } from '../types/forecast';
import type { OutlookIncrementalIndex } from '../types/outlookArtifacts';
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
  artifactIndex?: OutlookIncrementalIndex;
}

function fmtValid(iso?: string): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const day = `${String(d.getUTCMonth() + 1).padStart(2, '0')}/${String(d.getUTCDate()).padStart(2, '0')}`;
  const hr = String(d.getUTCHours()).padStart(2, '0');
  const mn = String(d.getUTCMinutes()).padStart(2, '0');
  return `${day} · ${hr}${mn}Z`;
}

const HOUR_MS = 60 * 60 * 1000;
const VALID_TIME_TOLERANCE_MS = 20 * 60 * 1000;
const HOLD_REPEAT_DELAY_MS = 260;
const HOLD_REPEAT_INTERVAL_MS = 110;

function artifactHourForStop(
  artifactIndex: OutlookIncrementalIndex | undefined,
  stop: { forecastHour: number; validTimeISO?: string },
): number {
  if (artifactIndex?.cycleTimeISO && stop.validTimeISO) {
    const cycleMs = Date.parse(artifactIndex.cycleTimeISO);
    const validMs = Date.parse(stop.validTimeISO);
    if (Number.isFinite(cycleMs) && Number.isFinite(validMs)) {
      const rawHours = (validMs - cycleMs) / HOUR_MS;
      const roundedHours = Math.round(rawHours);
      if (Math.abs(validMs - (cycleMs + roundedHours * HOUR_MS)) <= VALID_TIME_TOLERANCE_MS) {
        return roundedHours;
      }
    }
  }
  return stop.forecastHour;
}

export default function ForecastTimeSlider({
  bundle,
  index,
  isPlaying,
  onIndexChange,
  onNext,
  onPrev,
  onTogglePlay,
  artifactIndex,
}: ForecastTimeSliderProps) {
  const stops = bundle?.hours ?? [];
  const totalStops = Math.max(stops.length, 1);
  const safeIndex = stops.length > 0 ? Math.max(0, Math.min(index, stops.length - 1)) : 0;
  const current = stops[safeIndex];
  const atStart = safeIndex <= 0;
  const atEnd = stops.length === 0 || safeIndex >= stops.length - 1 || (current?.forecastHour ?? 0) >= 48;
  const isHourly = stops.length > 24;
  const holdDelayRef = useRef<number | null>(null);
  const holdIntervalRef = useRef<number | null>(null);
  const didHoldRepeatRef = useRef(false);
  const trackRef = useRef<HTMLDivElement>(null);
  const isDraggingRef = useRef(false);

  const clearHoldRepeat = useCallback(() => {
    if (holdDelayRef.current !== null) {
      window.clearTimeout(holdDelayRef.current);
      holdDelayRef.current = null;
    }
    if (holdIntervalRef.current !== null) {
      window.clearInterval(holdIntervalRef.current);
      holdIntervalRef.current = null;
    }
  }, []);

  const startHoldRepeat = useCallback((step: () => void, disabled: boolean) =>
    (event: PointerEvent<HTMLButtonElement>) => {
      if (disabled) return;
      clearHoldRepeat();
      didHoldRepeatRef.current = false;
      event.currentTarget.setPointerCapture(event.pointerId);
      holdDelayRef.current = window.setTimeout(() => {
        didHoldRepeatRef.current = true;
        step();
        holdIntervalRef.current = window.setInterval(step, HOLD_REPEAT_INTERVAL_MS);
      }, HOLD_REPEAT_DELAY_MS);
    }, [clearHoldRepeat]);

  const handlePrevClick = useCallback(() => {
    if (didHoldRepeatRef.current) {
      didHoldRepeatRef.current = false;
      return;
    }
    onPrev();
  }, [onPrev]);

  const handleNextClick = useCallback(() => {
    if (didHoldRepeatRef.current) {
      didHoldRepeatRef.current = false;
      return;
    }
    onNext();
  }, [onNext]);

  const handlePointerMove = useCallback((clientX: number) => {
    if (!trackRef.current || stops.length === 0) return;
    const rect = trackRef.current.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const newIndex = Math.round(pct * (stops.length - 1));
    onIndexChange(newIndex);
  }, [stops, onIndexChange]);

  const handleTrackPointerDown = useCallback((event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 && event.pointerType === 'mouse') return;
    isDraggingRef.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    handlePointerMove(event.clientX);
  }, [handlePointerMove]);

  const handleTrackPointerMove = useCallback((event: PointerEvent<HTMLDivElement>) => {
    if (isDraggingRef.current) {
      handlePointerMove(event.clientX);
    }
  }, [handlePointerMove]);

  const handleTrackPointerUp = useCallback((event: PointerEvent<HTMLDivElement>) => {
    if (isDraggingRef.current) {
      isDraggingRef.current = false;
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, []);

  useEffect(() => clearHoldRepeat, [clearHoldRepeat]);

  const showLabelForStop = useCallback(
    (stop: { forecastHour: number }, _i: number) => {
      if (stops.length <= 12) return true;
      let interval = 6;
      if (stops.length > 96) {
        interval = 24;
      } else if (stops.length > 48) {
        interval = 12;
      }
      return stop.forecastHour % interval === 0;
    },
    [stops.length],
  );

  const getTickStyle = useCallback(
    (stop: { forecastHour: number }, i: number) => {
      const isMajor = showLabelForStop(stop, i);
      if (isMajor) {
        return {
          tickClass: 'w-[2px] h-[20px] bg-ink',
          ledClass: 'w-1.5 h-1.5 opacity-100',
        };
      }

      let showMinor = true;
      let tickWidth = 'w-[2px]';
      let ledClass = 'w-1.5 h-1.5 opacity-30 group-hover:opacity-75';

      if (stops.length > 96) {
        showMinor = i % 4 === 0;
        tickWidth = 'w-[2px]';
        ledClass = 'w-1 h-1 opacity-20';
      } else if (stops.length > 48) {
        showMinor = i % 2 === 0;
        tickWidth = 'w-[2px]';
        ledClass = 'w-1 h-1 opacity-25';
      }

      return {
        tickClass: showMinor ? `${tickWidth} h-[10px] bg-ink/20 group-hover:bg-ink/55` : 'hidden',
        ledClass: showMinor ? ledClass : 'hidden',
      };
    },
    [stops.length, showLabelForStop],
  );

  return (
    <section className="bg-paper border-[3px] border-ink shadow-retro overflow-hidden flex flex-col">
      {/* 1. Terminal Header Bar */}
      <div className="bg-ink text-paper px-3 py-2 flex items-center justify-between flex-wrap gap-2 border-b-[3px] border-ink">
        <div className="flex items-center gap-2.5">
          <span className="font-mono text-[10px] font-bold tracking-[0.25em] text-signal-lime">
            ◢ AO // CHRONOLOGY
          </span>
          <span className="h-4 w-px bg-paper/20" />
          <span className="font-mono text-[10px] uppercase tracking-wider text-paper/70">
            Valid: <span className="text-paper font-bold">{fmtValid(current?.validTimeISO)}</span>
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden md:inline font-mono text-[9px] uppercase tracking-widest text-paper/40">
            ←/→ keys to step · Space to play/pause · Drag track to scrub
          </span>
          <div className="flex items-center gap-1.5 border border-paper/20 bg-paper/5 px-2 py-0.5 rounded-sm">
            <span
              className={[
                'inline-block h-1.5 w-1.5 rounded-full',
                isPlaying ? 'bg-signal-lime animate-pulse-dot' : 'bg-paper/30',
              ].join(' ')}
            />
            <span className="font-mono text-[9px] uppercase tracking-wider text-paper/80 font-bold">
              {isPlaying ? 'Looping' : 'Paused'}
            </span>
          </div>
        </div>
      </div>

      {/* 2. Control & Gauge Deck */}
      <div className="p-3 flex flex-col gap-4">
        {/* Gauge & Media Controls Row */}
        <div className="flex items-center justify-between gap-4 flex-wrap">
          {/* Hour Gauge (Large LCD display style) */}
          <div className="flex items-center gap-3">
            <div className="border-[3px] border-ink bg-ink text-signal-amber px-3 py-1 font-mono text-xl font-black tracking-widest shadow-retro-sm select-none">
              F-{current ? String(current.forecastHour).padStart(2, '0') : '00'}
            </div>
            <div className="flex flex-col leading-none">
              <span className="font-mono text-[9px] uppercase tracking-wider text-ink/50">Forecast Hour</span>
              <span className="mt-1 font-display text-xs font-bold uppercase tracking-tight text-ink">
                {current ? `Step ${safeIndex + 1} of ${totalStops}` : 'No active steps'}
              </span>
            </div>
          </div>

          {/* Media Player Controls Group */}
          <div className="flex items-center gap-1.5 bg-ink/[0.03] border-[2px] border-dashed border-ink/20 p-1">
            <RetroButton
              onClick={handlePrevClick}
              onPointerDown={startHoldRepeat(onPrev, atStart)}
              onPointerUp={clearHoldRepeat}
              onPointerCancel={clearHoldRepeat}
              onLostPointerCapture={clearHoldRepeat}
              aria-label="Previous forecast hour"
              disabled={atStart}
              className="!h-8 !px-2.5"
            >
              <div className="flex items-center gap-1 font-mono text-[10px] font-bold">
                <span>◀◀</span> <span className="hidden xs:inline">PREV</span>
              </div>
            </RetroButton>

            <RetroButton
              onClick={onTogglePlay}
              primary={isPlaying}
              disabled={stops.length === 0 || (!isPlaying && atEnd)}
              aria-label={isPlaying ? 'Pause animation' : 'Play animation'}
              className="!h-8 !px-3.5"
            >
              <div className="flex items-center gap-1.5 font-mono text-[10px] font-bold">
                <span>{isPlaying ? '■' : '▶'}</span>
                <span>{isPlaying ? 'PAUSE' : 'PLAY'}</span>
              </div>
            </RetroButton>

            <RetroButton
              onClick={handleNextClick}
              onPointerDown={startHoldRepeat(onNext, atEnd)}
              onPointerUp={clearHoldRepeat}
              onPointerCancel={clearHoldRepeat}
              onLostPointerCapture={clearHoldRepeat}
              aria-label="Next forecast hour"
              disabled={atEnd}
              className="!h-8 !px-2.5"
            >
              <div className="flex items-center gap-1 font-mono text-[10px] font-bold">
                <span className="hidden xs:inline">NEXT</span> <span>▶▶</span>
              </div>
            </RetroButton>
          </div>
        </div>

        {/* 3. Slider Track Container */}
        <div className="relative pt-2 pb-6">
          {/* Main Channel Track */}
          <div
            ref={trackRef}
            onPointerDown={handleTrackPointerDown}
            onPointerMove={handleTrackPointerMove}
            onPointerUp={handleTrackPointerUp}
            onPointerCancel={handleTrackPointerUp}
            className="relative h-11 border-[3px] border-ink bg-ink/[0.03] flex items-stretch select-none overflow-hidden shadow-retro-inset cursor-ew-resize"
          >
            {/* Sliding Thumb (Smooth snaps) */}
            {stops.length > 0 && (
              <div
                className="absolute top-0 bottom-0 z-10 transition-all duration-150 ease-out pointer-events-none"
                style={{
                  left: `${(safeIndex / Math.max(totalStops - 1, 1)) * 100}%`,
                  transform: 'translateX(-50%)',
                }}
              >
                <div className="w-10 h-full bg-signal-amber border-l-[3px] border-r-[3px] border-ink shadow-retro-sm flex flex-col items-center justify-center relative">
                  {/* Grip ridges */}
                  <div className="absolute inset-y-1 left-1.5 flex flex-col justify-between py-1 pointer-events-none">
                    <span className="w-[1.5px] h-[3px] bg-ink/40" />
                    <span className="w-[1.5px] h-[3px] bg-ink/40" />
                    <span className="w-[1.5px] h-[3px] bg-ink/40" />
                  </div>
                  <div className="absolute inset-y-1 right-1.5 flex flex-col justify-between py-1 pointer-events-none">
                    <span className="w-[1.5px] h-[3px] bg-ink/40" />
                    <span className="w-[1.5px] h-[3px] bg-ink/40" />
                    <span className="w-[1.5px] h-[3px] bg-ink/40" />
                  </div>
                  {/* Vernier scale hairline */}
                  <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 w-[2px] bg-signal-red" />
                  {/* Display number */}
                  <span className="font-mono text-[9px] font-black text-ink bg-paper px-1 border border-ink relative z-10 leading-none py-0.5 select-none">
                    F{String(current.forecastHour).padStart(2, '0')}
                  </span>
                </div>
              </div>
            )}

            {/* Individual Columns for Stops */}
            {stops.map((stop, i) => {
              const isActive = i === safeIndex;
              const { tickClass, ledClass } = getTickStyle(stop, i);
              const artifactHour = artifactHourForStop(artifactIndex, stop);
              const artifactState = artifactIndex
                ? artifactIndex.readyForecastHours.includes(artifactHour)
                  ? 'ready'
                  : artifactIndex.failedForecastHours.includes(artifactHour)
                    ? 'failed'
                    : artifactIndex.pendingForecastHours.includes(artifactHour)
                      ? 'pending'
                      : 'missing'
                : 'missing';

              return (
                <button
                  key={stop.forecastHour}
                  type="button"
                  onClick={() => onIndexChange(i)}
                  aria-current={isActive ? 'step' : undefined}
                  className="flex-1 h-full flex flex-col items-center justify-between py-1.5 group relative hover:bg-ink/[0.02] cursor-pointer"
                >
                  {/* Tick line */}
                  <div className={['transition-colors duration-150', tickClass].join(' ')} />

                  {/* Status Indicator LED */}
                  <div
                    className={[
                      'w-1.5 h-1.5 rounded-full border border-ink transition-all duration-150',
                      ledClass,
                      artifactState === 'ready' ? 'bg-signal-lime' :
                        artifactState === 'failed' ? 'bg-signal-red' :
                          artifactState === 'pending' ? 'bg-signal-amber/60 animate-pulse' :
                            'bg-transparent',
                    ].join(' ')}
                  />
                </button>
              );
            })}

            {stops.length === 0 && (
              <div className="flex-1 text-center text-ink/40 font-mono text-[11px] py-3">
                Awaiting forecast bundle…
              </div>
            )}
          </div>

          {/* Chronological Ruler Labels */}
          <div className="absolute left-0 right-0 bottom-0 h-5 pointer-events-none">
            {stops.map((stop, i) => {
              if (!showLabelForStop(stop, i)) return null;
              const lbl = FORECAST_HOUR_LABELS[stop.forecastHour] ?? `+${stop.forecastHour}h`;
              const pct = (i / Math.max(totalStops - 1, 1)) * 100;
              return (
                <span
                  key={stop.forecastHour}
                  className="absolute text-[10px] font-mono font-bold text-ink/50 uppercase tracking-wider"
                  style={{
                    left: `${pct}%`,
                    transform: 'translateX(-50%)',
                  }}
                >
                  {lbl}
                </span>
              );
            })}
          </div>
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
