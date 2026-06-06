import { useCallback, useEffect, useRef, useState } from 'react';
import type { ActiveRegion, ForecastBundle } from '../types/forecast';
import { fetchLatestForecast, type FetchResult } from '../utils/fetchLatestForecast';

const REFRESH_INTERVAL_MS = 15 * 60 * 1000; // 15 minutes

export type FetchStatus = 'idle' | 'loading' | 'success' | 'error';

export interface AutoForecastState {
  bundle: ForecastBundle | null;
  status: FetchStatus;
  attempted: FetchResult['attemptedProviders'];
  errorMsg: string | null;
  lastFetchedAt: Date | null;
  refreshIntervalMs: number;
  refreshNow: () => void;
}

export function useAutoForecast(activeRegion: ActiveRegion = 'conus', enabled = true): AutoForecastState {
  const [bundle, setBundle] = useState<ForecastBundle | null>(null);
  const [status, setStatus] = useState<FetchStatus>('idle');
  const [attempted, setAttempted] = useState<FetchResult['attemptedProviders']>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [lastFetchedAt, setLastFetchedAt] = useState<Date | null>(null);

  const isMountedRef = useRef(true);
  const enabledRef = useRef(enabled);
  const activeRegionRef = useRef(activeRegion);
  const previousRegionRef = useRef(activeRegion);
  const inflightRef = useRef<{ region: ActiveRegion; promise: Promise<void> } | null>(null);

  useEffect(() => {
    enabledRef.current = enabled;
    if (!enabled) {
      setBundle(null);
      setStatus('idle');
      setAttempted([]);
      setErrorMsg(null);
      setLastFetchedAt(null);
      previousRegionRef.current = activeRegion;
    }
  }, [activeRegion, enabled]);

  useEffect(() => {
    activeRegionRef.current = activeRegion;
    if (previousRegionRef.current !== activeRegion) {
      setBundle(null);
      setAttempted([]);
      setErrorMsg(null);
      setLastFetchedAt(null);
      previousRegionRef.current = activeRegion;
    }
  }, [activeRegion]);

  const doFetch = useCallback(async () => {
    if (!enabled) return;
    const requestRegion = activeRegion;
    const currentInflight = inflightRef.current;
    if (currentInflight?.region === requestRegion) return currentInflight.promise;
    setStatus('loading');
    const job = (async () => {
      try {
        const result = await fetchLatestForecast(requestRegion);
        if (!isMountedRef.current || !enabledRef.current || activeRegionRef.current !== requestRegion) return;
        setBundle(result.bundle);
        setAttempted(result.attemptedProviders);
        setLastFetchedAt(new Date());
        setStatus('success');
        setErrorMsg(null);
      } catch (err) {
        if (!isMountedRef.current || !enabledRef.current || activeRegionRef.current !== requestRegion) return;
        setStatus('error');
        setErrorMsg(err instanceof Error ? err.message : String(err));
      }
    })();
    inflightRef.current = { region: requestRegion, promise: job };
    try {
      await job;
    } finally {
      if (inflightRef.current?.promise === job) {
        inflightRef.current = null;
      }
    }
  }, [activeRegion, enabled]);

  useEffect(() => {
    isMountedRef.current = true;
    if (!enabled) {
      return () => {
        isMountedRef.current = false;
      };
    }
    void doFetch();
    const id = setInterval(() => { void doFetch(); }, REFRESH_INTERVAL_MS);
    return () => {
      isMountedRef.current = false;
      clearInterval(id);
    };
  }, [doFetch, enabled]);

  return {
    bundle,
    status,
    attempted,
    errorMsg,
    lastFetchedAt,
    refreshIntervalMs: REFRESH_INTERVAL_MS,
    refreshNow: () => {
      if (enabledRef.current) void doFetch();
    },
  };
}
