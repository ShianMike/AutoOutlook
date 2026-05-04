import { useCallback, useEffect, useRef, useState } from 'react';
import type { ForecastBundle } from '../types/forecast';
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

export function useAutoForecast(): AutoForecastState {
  const [bundle, setBundle] = useState<ForecastBundle | null>(null);
  const [status, setStatus] = useState<FetchStatus>('idle');
  const [attempted, setAttempted] = useState<FetchResult['attemptedProviders']>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [lastFetchedAt, setLastFetchedAt] = useState<Date | null>(null);

  const isMountedRef = useRef(true);
  const inflightRef = useRef<Promise<void> | null>(null);

  const doFetch = useCallback(async () => {
    if (inflightRef.current) return inflightRef.current;
    setStatus('loading');
    const job = (async () => {
      try {
        const result = await fetchLatestForecast();
        if (!isMountedRef.current) return;
        setBundle(result.bundle);
        setAttempted(result.attemptedProviders);
        setLastFetchedAt(new Date());
        setStatus('success');
        setErrorMsg(null);
      } catch (err) {
        if (!isMountedRef.current) return;
        setStatus('error');
        setErrorMsg(err instanceof Error ? err.message : String(err));
      }
    })();
    inflightRef.current = job;
    try {
      await job;
    } finally {
      inflightRef.current = null;
    }
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    void doFetch();
    const id = setInterval(() => { void doFetch(); }, REFRESH_INTERVAL_MS);
    return () => {
      isMountedRef.current = false;
      clearInterval(id);
    };
  }, [doFetch]);

  return {
    bundle,
    status,
    attempted,
    errorMsg,
    lastFetchedAt,
    refreshIntervalMs: REFRESH_INTERVAL_MS,
    refreshNow: () => { void doFetch(); },
  };
}
