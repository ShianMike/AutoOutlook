import { useEffect, useState } from 'react';
import type { OutlookArtifacts, OutlookArtifactFeatureCollection, OutlookArtifactMetadata } from '../types/outlookArtifacts';

type ArtifactStatus = 'loading' | 'ready' | 'missing' | 'error';

interface OutlookArtifactState {
  status: ArtifactStatus;
  artifacts: OutlookArtifacts | null;
  message: string | null;
}

const INITIAL_STATE: OutlookArtifactState = {
  status: 'loading',
  artifacts: null,
  message: null,
};

async function fetchJson<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (response.status === 404) {
    throw new Error('artifact_missing');
  }
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function useOutlookArtifacts(refreshMs = 10 * 60 * 1000): OutlookArtifactState {
  const [state, setState] = useState<OutlookArtifactState>(INITIAL_STATE);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    const load = async () => {
      try {
        const [metadata, riskPolygons, aggregateRiskPolygons] = await Promise.all([
          fetchJson<OutlookArtifactMetadata>('/api/outlook/latest', controller.signal),
          fetchJson<OutlookArtifactFeatureCollection>('/api/outlook/risk-polygons', controller.signal),
          fetchJson<OutlookArtifactFeatureCollection>('/api/outlook/aggregate-risk-polygons', controller.signal).catch(() => undefined),
        ]);
        if (!cancelled) {
          setState({
            status: 'ready',
            artifacts: { metadata, riskPolygons, aggregateRiskPolygons },
            message: null,
          });
        }
      } catch (error) {
        if (cancelled || controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : String(error);
        setState({
          status: message === 'artifact_missing' ? 'missing' : 'error',
          artifacts: null,
          message: message === 'artifact_missing'
            ? 'Generated HRRR/XGBoost outlook artifacts are not available yet.'
            : `Generated outlook artifact fetch failed: ${message}`,
        });
      }
    };

    load();
    const interval = window.setInterval(load, refreshMs);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(interval);
    };
  }, [refreshMs]);

  return state;
}
