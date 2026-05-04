// Provider chain: backend -> Open-Meteo -> mock. First successful one wins.
// Each step has a timeout; on failure we fall through to the next provider.

import type { ForecastBundle, ForecastProvider } from '../types/forecast';
import { pythonBackendProvider } from './providers/pythonBackendProvider';
import { openMeteoProvider } from './providers/openMeteoProvider';
import { mockProvider } from './providers/mockProvider';

const PROVIDER_TIMEOUT_MS = 8000;
const BACKEND_TIMEOUT_MS = 120000;

function withTimeout<T>(p: Promise<T>, ms: number, controller: AbortController): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const id = setTimeout(() => {
      controller.abort();
      reject(new Error(`Provider timed out after ${ms}ms`));
    }, ms);
    p.then((v) => { clearTimeout(id); resolve(v); })
     .catch((e) => { clearTimeout(id); reject(e); });
  });
}

async function tryProvider(p: ForecastProvider): Promise<ForecastBundle> {
  const ctrl = new AbortController();
  const timeoutMs = p.id === 'backend' ? BACKEND_TIMEOUT_MS : PROVIDER_TIMEOUT_MS;
  return withTimeout(p.fetchBundle(ctrl.signal), timeoutMs, ctrl);
}

export interface FetchResult {
  bundle: ForecastBundle;
  attemptedProviders: { id: ForecastProvider['id']; ok: boolean; error?: string }[];
}

/**
 * Fetch the latest forecast bundle by trying providers in order.
 * Always succeeds (mock is the final fallback).
 */
export async function fetchLatestForecast(): Promise<FetchResult> {
  const chain: ForecastProvider[] = [pythonBackendProvider, openMeteoProvider, mockProvider];
  const attempted: FetchResult['attemptedProviders'] = [];
  for (const provider of chain) {
    try {
      const bundle = await tryProvider(provider);
      attempted.push({ id: provider.id, ok: true });
      // Tag a fallback marker if we didn't reach the primary.
      if (provider.id !== 'backend' && bundle.source === 'live') {
        return { bundle: { ...bundle, source: 'live' }, attemptedProviders: attempted };
      }
      if (provider.id === 'mock') {
        return {
          bundle: { ...bundle, source: 'fallback' },
          attemptedProviders: attempted,
        };
      }
      return { bundle, attemptedProviders: attempted };
    } catch (err) {
      attempted.push({
        id: provider.id,
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      });
      // continue to next provider
    }
  }
  // Safety: should never get here because mockProvider can't fail.
  throw new Error('All providers failed');
}
