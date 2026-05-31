import { useCallback, useEffect, useRef, useState } from 'react';
import type { ForecastBundle, HourSnapshot } from '../types/forecast';

const PLAY_INTERVAL_MS = 1500;
const MAX_FORECAST_HOUR = 90;

export interface ForecastHourState {
  index: number;
  snapshot: HourSnapshot | null;
  isPlaying: boolean;
  setIndex: (i: number) => void;
  next: () => void;
  prev: () => void;
  togglePlay: () => void;
  setPlaying: (playing: boolean) => void;
}

export function clampForecastIndex(index: number, length: number): number {
  if (!Number.isFinite(index) || length <= 0) return 0;
  return Math.max(0, Math.min(length - 1, Math.round(index)));
}

export function nextForecastIndex(index: number, length: number): number {
  return clampForecastIndex(index + 1, length);
}

export function prevForecastIndex(index: number, length: number): number {
  return clampForecastIndex(index - 1, length);
}

export function nearestForecastHourIndex(hours: HourSnapshot[], targetHour: number | null | undefined): number {
  if (hours.length === 0 || targetHour === null || targetHour === undefined || !Number.isFinite(targetHour)) {
    return 0;
  }

  const clampedTarget = Math.max(0, Math.min(MAX_FORECAST_HOUR, targetHour));
  return hours.reduce((bestIndex, hour, index) => {
    const bestDist = Math.abs(hours[bestIndex].forecastHour - clampedTarget);
    const dist = Math.abs(hour.forecastHour - clampedTarget);
    return dist < bestDist ? index : bestIndex;
  }, 0);
}

export function useForecastHour(bundle: ForecastBundle | null): ForecastHourState {
  const [index, setIndexState] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const intervalRef = useRef<number | null>(null);
  const selectedForecastHourRef = useRef<number | null>(null);

  const rememberSelectedHour = useCallback((nextIndex: number, nextBundle: ForecastBundle | null = bundle) => {
    const hour = nextBundle?.hours[nextIndex]?.forecastHour;
    if (typeof hour === 'number' && Number.isFinite(hour)) {
      selectedForecastHourRef.current = Math.max(0, Math.min(MAX_FORECAST_HOUR, hour));
    }
  }, [bundle]);

  const setIndex = useCallback((nextIndex: number) => {
    setIndexState(() => {
      const safeIndex = clampForecastIndex(nextIndex, bundle?.hours.length ?? 0);
      rememberSelectedHour(safeIndex, bundle);
      return safeIndex;
    });
  }, [bundle, rememberSelectedHour]);

  // Preserve the selected forecast hour when a refreshed bundle arrives.
  useEffect(() => {
    if (!bundle || bundle.hours.length === 0) {
      setIndexState(0);
      return;
    }

    setIndexState((currentIndex) => {
      const rememberedHour =
        selectedForecastHourRef.current ??
        bundle.hours[clampForecastIndex(currentIndex, bundle.hours.length)]?.forecastHour;
      const safeIndex = nearestForecastHourIndex(bundle.hours, rememberedHour);
      rememberSelectedHour(safeIndex, bundle);
      return safeIndex;
    });
  }, [bundle, rememberSelectedHour]);

  const next = useCallback(() => {
    if (!bundle) return;
    setIndexState((i) => {
      const safeIndex = nextForecastIndex(i, bundle.hours.length);
      rememberSelectedHour(safeIndex, bundle);
      return safeIndex;
    });
  }, [bundle, rememberSelectedHour]);

  const prev = useCallback(() => {
    if (!bundle) return;
    setIndexState((i) => {
      const safeIndex = prevForecastIndex(i, bundle.hours.length);
      rememberSelectedHour(safeIndex, bundle);
      return safeIndex;
    });
  }, [bundle, rememberSelectedHour]);

  const togglePlay = useCallback(() => setIsPlaying((p) => !p), []);

  // Auto-step when playing.
  useEffect(() => {
    if (!isPlaying || !bundle) return;
    const id = window.setInterval(() => {
      setIndexState((i) => {
        const safeIndex = nextForecastIndex(i, bundle.hours.length);
        rememberSelectedHour(safeIndex, bundle);
        if (safeIndex === i && i === bundle.hours.length - 1) {
          setIsPlaying(false);
        }
        return safeIndex;
      });
    }, PLAY_INTERVAL_MS);
    intervalRef.current = id;
    return () => {
      if (intervalRef.current !== null) window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    };
  }, [isPlaying, bundle, rememberSelectedHour]);

  // Keyboard shortcuts (left/right/space)
  useEffect(() => {
    if (!bundle) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.target && (e.target as HTMLElement).tagName === 'INPUT') return;
      if (e.key === 'ArrowRight') { e.preventDefault(); next(); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); prev(); }
      else if (e.key === ' ') { e.preventDefault(); togglePlay(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [bundle, next, prev, togglePlay]);

  const snapshot = bundle ? bundle.hours[index] ?? null : null;

  return {
    index,
    snapshot,
    isPlaying,
    setIndex,
    next,
    prev,
    togglePlay,
    setPlaying: setIsPlaying,
  };
}
