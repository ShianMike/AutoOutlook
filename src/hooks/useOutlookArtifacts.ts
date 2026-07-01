import { useEffect, useRef, useState } from 'react';
import type { ActiveRegion } from '../types/forecast';
import type {
  OutlookArtifacts,
  OutlookArtifactFeatureCollection,
  OutlookArtifactMetadata,
  OutlookIncrementalSummary,
  OutlookIncrementalIndex,
  OutlookProbabilityHour,
  OutlookProbabilityTile,
  OutlookProbabilityTiles,
  OutlookProbabilityShapeFeatureCollection,
  SpcCategoryFeatureCollection,
  SpcVerificationSummary,
  MergedD1VerificationSummary,
  SpcStormReport,
  SpcStormReportsResponse,
} from '../types/outlookArtifacts';
import type { HistoricalEnhPlusEvent } from '../data/historicalEnhPlusVerification';
import { apiUrl } from '../utils/apiBase';
import {
  cycleIdentity,
  cycleIdentityKey,
  guardReplacement,
  sameCycleIdentity,
} from '../utils/cycleIdentity';

export type ArtifactStatus = 'loading' | 'ready' | 'missing' | 'error' | 'pending' | 'failed';

export interface OutlookArtifactState {
  status: ArtifactStatus;
  artifacts: OutlookArtifacts | null;
  message: string | null;
}

const INITIAL_STATE: OutlookArtifactState = {
  status: 'loading',
  artifacts: null,
  message: null,
};

const HOUR_MS = 60 * 60 * 1000;
const VALID_TIME_TOLERANCE_MS = 20 * 60 * 1000;
const PREFETCH_RADIUS = 6;

async function fetchJson<T>(url: string, signal?: AbortSignal, activeRegion?: ActiveRegion): Promise<T> {
  const separator = url.includes('?') ? '&' : '?';
  const finalUrl = activeRegion ? `${url}${separator}region=${activeRegion}` : url;
  const response = await fetch(apiUrl(finalUrl), { signal, cache: 'no-store' });
  if (response.status === 404) {
    throw new Error('artifact_missing');
  }
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export interface FetchWithTimeoutRetriesOptions {
  /** Per-attempt timeout in milliseconds. Defaults to 10000 (10s). */
  timeoutMs?: number;
  /** Number of retries after the first attempt. Defaults to 2 (3 total attempts). */
  retries?: number;
  /** Optional caller-owned signal (e.g. unmount) that aborts all remaining attempts. */
  signal?: AbortSignal;
  /** Optional active region forwarded to the request. */
  activeRegion?: ActiveRegion;
}

/**
 * Wraps {@link fetchJson} with a per-attempt timeout (via AbortController) and
 * bounded retries. Each attempt is aborted if it exceeds `timeoutMs`; on failure
 * the request is retried up to `retries` times (so `retries + 1` attempts total)
 * before the last error is rethrown. A definitive not-found (`artifact_missing`,
 * HTTP 404) is not retried since retrying cannot change the result, and an
 * external abort short-circuits the remaining attempts.
 */
async function fetchWithTimeoutRetries<T>(
  url: string,
  options: FetchWithTimeoutRetriesOptions = {},
): Promise<T> {
  const { timeoutMs = 10000, retries = 2, signal: externalSignal, activeRegion } = options;
  const maxAttempts = retries + 1;
  let lastError: unknown;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (externalSignal?.aborted) {
      throw new DOMException('Aborted', 'AbortError');
    }

    const controller = new AbortController();
    const onExternalAbort = () => controller.abort();
    externalSignal?.addEventListener('abort', onExternalAbort, { once: true });
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
      return await fetchJson<T>(url, controller.signal, activeRegion);
    } catch (error) {
      lastError = error;
      // A definitive not-found will not change across retries.
      if (error instanceof Error && error.message === 'artifact_missing') {
        throw error;
      }
      // Respect an external cancellation rather than retrying.
      if (externalSignal?.aborted) {
        throw error;
      }
    } finally {
      clearTimeout(timeoutId);
      externalSignal?.removeEventListener('abort', onExternalAbort);
    }
  }

  throw lastError instanceof Error ? lastError : new Error('fetch_failed');
}

async function fetchOptionalSpcVerification(
  signal: AbortSignal | undefined,
  activeRegion: ActiveRegion,
): Promise<SpcVerificationSummary | undefined> {
  return fetchJson<SpcVerificationSummary>('/api/outlook/verification', signal, activeRegion)
    .catch(() => undefined);
}

function forecastHourLabel(hour: number | undefined): string {
  return hour === undefined ? 'F--' : `F${String(hour).padStart(2, '0')}`;
}

function resolveArtifactForecastHour(
  cycleTimeISO: string | undefined,
  selectedForecastHour: number | undefined,
  selectedValidTimeISO: string | undefined,
  availableForecastHours?: number[],
): number | undefined {
  if (cycleTimeISO && selectedValidTimeISO) {
    const cycleMs = Date.parse(cycleTimeISO);
    const selectedMs = Date.parse(selectedValidTimeISO);
    if (Number.isFinite(cycleMs) && Number.isFinite(selectedMs)) {
      const rawHours = (selectedMs - cycleMs) / HOUR_MS;
      const roundedHours = Math.round(rawHours);
      const closeToWholeHour = Math.abs(selectedMs - (cycleMs + roundedHours * HOUR_MS)) <= VALID_TIME_TOLERANCE_MS;
      if (closeToWholeHour) {
        if (!availableForecastHours?.length || availableForecastHours.includes(roundedHours)) return roundedHours;
        return roundedHours;
      }
    }
  }
  if (
    selectedForecastHour !== undefined
    && (!availableForecastHours?.length || availableForecastHours.includes(selectedForecastHour))
  ) {
    return selectedForecastHour;
  }
  return selectedForecastHour;
}

function displayRiskPolygonsForSelectedHour(
  collection: OutlookArtifactFeatureCollection,
  selectedForecastHour: number,
  selectedValidTimeISO: string | undefined,
): OutlookArtifactFeatureCollection {
  return {
    ...collection,
    features: collection.features.map((feature) => ({
      ...feature,
      properties: {
        ...feature.properties,
        forecastHour: selectedForecastHour,
        validTimeISO: selectedValidTimeISO ?? feature.properties.validTimeISO,
      },
    })),
  };
}

function mergeRiskPolygonCache(
  cache: Map<number, OutlookArtifactFeatureCollection>,
): OutlookArtifactFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: Array.from(cache.entries())
      .sort(([a], [b]) => a - b)
      .flatMap(([, collection]) => collection.features),
  };
}

function displayProbabilityHourForSelectedHour(
  tile: OutlookProbabilityTile,
  selectedForecastHour: number,
  selectedValidTimeISO: string | undefined,
  categoryCounts?: Record<string, number>,
): OutlookProbabilityHour {
  const displayTile: OutlookProbabilityTile = {
    ...tile,
    forecastHour: selectedForecastHour,
    validTimeISO: selectedValidTimeISO ?? tile.validTimeISO,
  };
  return {
    forecastHour: selectedForecastHour,
    validTimeISO: displayTile.validTimeISO,
    categoryCounts,
    tile: displayTile,
  };
}

function displayedValidTimeISO(
  selectedForecastHour: number | undefined,
  selectedValidTimeISO: string | undefined,
  displayForecastHour: number,
): string | undefined {
  if (selectedForecastHour === undefined || !selectedValidTimeISO) return undefined;
  const selectedMs = Date.parse(selectedValidTimeISO);
  if (!Number.isFinite(selectedMs)) return undefined;
  return new Date(selectedMs + (displayForecastHour - selectedForecastHour) * HOUR_MS).toISOString();
}

function displayForecastHourForTile(
  tile: OutlookProbabilityTile,
  selectedForecastHour: number | undefined,
  selectedValidTimeISO: string | undefined,
): number {
  if (selectedForecastHour === undefined || !selectedValidTimeISO) return tile.forecastHour;
  const selectedMs = Date.parse(selectedValidTimeISO);
  const tileMs = Date.parse(tile.validTimeISO);
  if (!Number.isFinite(selectedMs) || !Number.isFinite(tileMs)) return tile.forecastHour;
  const displayBaseMs = selectedMs - selectedForecastHour * HOUR_MS;
  const rawHour = (tileMs - displayBaseMs) / HOUR_MS;
  const roundedHour = Math.round(rawHour);
  if (Math.abs(tileMs - (displayBaseMs + roundedHour * HOUR_MS)) <= VALID_TIME_TOLERANCE_MS) {
    return roundedHour;
  }
  return tile.forecastHour;
}

function displayProbabilityHourFromArtifactHour(
  hour: OutlookProbabilityHour,
  selectedForecastHour: number | undefined,
  selectedValidTimeISO: string | undefined,
): OutlookProbabilityHour {
  const displayHour = displayForecastHourForTile(hour.tile, selectedForecastHour, selectedValidTimeISO);
  return displayProbabilityHourForSelectedHour(
    hour.tile,
    displayHour,
    displayedValidTimeISO(selectedForecastHour, selectedValidTimeISO, displayHour) ?? hour.validTimeISO,
    hour.categoryCounts,
  );
}

function mergeProbabilityHours(
  ...hourGroups: Array<OutlookProbabilityHour[] | undefined>
): OutlookProbabilityHour[] {
  const byForecastHour = new Map<number, OutlookProbabilityHour>();
  hourGroups.forEach((hours) => {
    hours?.forEach((hour) => {
      byForecastHour.set(hour.forecastHour, hour);
    });
  });
  return Array.from(byForecastHour.values()).sort((a, b) => a.forecastHour - b.forecastHour);
}

function probabilityTilesFromIncremental(
  incremental: OutlookIncrementalIndex,
  hours: OutlookProbabilityHour[],
): OutlookProbabilityTiles {
  return {
    cycle: incremental.cycle,
    featureSchemaHash: incremental.featureSchemaHash,
    riskLabels: ['NONE', 'TSTM', 'MRGL', 'SLGT', 'ENH', 'MDT', 'HIGH'],
    gridStride: incremental.gridStride,
    tileStride: incremental.tileStride,
    environmentalCapsApplied: true,
    categoryConsistencyCapsApplied: true,
    hours,
  };
}

function selectProbabilityHourForDisplayedTime(
  probabilityTiles: OutlookProbabilityTiles | undefined,
  metadata: OutlookArtifactMetadata,
  selectedForecastHour: number | undefined,
  selectedValidTimeISO: string | undefined,
): OutlookProbabilityHour | undefined {
  if (!probabilityTiles || selectedForecastHour === undefined) return undefined;
  const bySelectedHour = probabilityTiles.hours.find((hour) => hour.forecastHour === selectedForecastHour);
  if (bySelectedHour) return bySelectedHour;
  if (selectedValidTimeISO) {
    const selectedMs = Date.parse(selectedValidTimeISO);
    if (Number.isFinite(selectedMs)) {
      const byValidTime = probabilityTiles.hours.find((hour) => {
        const hourMs = Date.parse(hour.validTimeISO);
        return Number.isFinite(hourMs) && Math.abs(hourMs - selectedMs) <= VALID_TIME_TOLERANCE_MS;
      });
      if (byValidTime) return byValidTime;
    }
  }
  const artifactHour = resolveArtifactForecastHour(
    metadata.cycleTimeISO,
    selectedForecastHour,
    selectedValidTimeISO,
    probabilityTiles.hours.map((hour) => hour.forecastHour),
  );
  const byArtifactHour = probabilityTiles.hours.find((hour) => hour.forecastHour === artifactHour);
  return byArtifactHour ?? probabilityTiles.hours.find((hour) => hour.forecastHour === selectedForecastHour);
}

function probabilityTilesWithDisplayedHour(
  probabilityTiles: OutlookProbabilityTiles | undefined,
  metadata: OutlookArtifactMetadata,
  selectedForecastHour: number | undefined,
  selectedValidTimeISO: string | undefined,
): OutlookProbabilityTiles | undefined {
  const matchedHour = selectProbabilityHourForDisplayedTime(
    probabilityTiles,
    metadata,
    selectedForecastHour,
    selectedValidTimeISO,
  );
  if (!probabilityTiles || !matchedHour || selectedForecastHour === undefined) return probabilityTiles;
  const displayHour = displayProbabilityHourForSelectedHour(
    matchedHour.tile,
    selectedForecastHour,
    selectedValidTimeISO,
    matchedHour.categoryCounts,
  );
  return {
    ...probabilityTiles,
    hours: [
      displayHour,
      ...probabilityTiles.hours.filter((hour) => hour.forecastHour !== selectedForecastHour),
    ],
  };
}

function hasTileForDisplayedHour(
  artifacts: OutlookArtifacts | null,
  selectedForecastHour: number | undefined,
): boolean {
  if (selectedForecastHour === undefined) return false;
  return Boolean(artifacts?.probabilityTiles?.hours.some((hour) => hour.forecastHour === selectedForecastHour));
}

function preserveReadySelectedHour(
  previous: OutlookArtifactState,
  selectedForecastHour: number | undefined,
  next: OutlookArtifactState,
): OutlookArtifactState {
  if (previous.status === 'ready' && hasTileForDisplayedHour(previous.artifacts, selectedForecastHour)) {
    return previous;
  }
  return next;
}

/** Reload resilience: a reload that does not settle within this window after a
 * refresh is treated as failed (Requirement 4.6). */
const RELOAD_TIMEOUT_MS = 30 * 1000;

/**
 * Human-readable label identifying the affected outlook day for a per-day
 * reload status message (Requirement 4.7). Derived from the durable cycle
 * identity of the last valid outlook.
 */
function reloadDayLabel(state: OutlookArtifactState | null): string {
  const incremental = state?.artifacts?.incrementalIndex;
  if (incremental) {
    const identity = cycleIdentity(incremental);
    const typeLabel = identity.outlookType === 'day2'
      ? 'Day 2'
      : identity.outlookType === 'day1'
        ? 'Day 1'
        : 'Current';
    return identity.issuingDay ? `${typeLabel} (${identity.issuingDay})` : typeLabel;
  }
  return 'Current';
}

/**
 * Reconcile a newly computed `ready` outlook against the retained state
 * (Requirement 4.1, 4.2, 4.3, 4.4, 4.8).
 *
 * When the newly loaded outlook shares the retained outlook's cycle identity,
 * a replacement that is empty or missing fields present in the retained cache
 * is rejected via {@link guardReplacement}, so the retained geometries and
 * category values continue to display unchanged. When the cycle identity
 * differs (a genuinely new cycle) or no retained outlook exists, the new
 * outlook is stored as-is.
 */
function reconcileReadyState(
  previous: OutlookArtifactState,
  next: OutlookArtifactState,
): OutlookArtifactState {
  const previousIncremental = previous.artifacts?.incrementalIndex;
  const nextIncremental = next.artifacts?.incrementalIndex;
  if (
    previous.status === 'ready'
    && previousIncremental
    && nextIncremental
    && sameCycleIdentity(cycleIdentity(previousIncremental), cycleIdentity(nextIncremental))
    && !guardReplacement(previous, next)
  ) {
    // Same cycle, but the replacement is empty or drops a field the retained
    // cache has: reuse the retained outlook (Requirement 4.2).
    return previous;
  }
  return next;
}

export function useOutlookArtifacts(
  selectedForecastHour?: number,
  selectedValidTimeISO?: string,
  activeRegion: ActiveRegion = 'conus',
  refreshMs = 15 * 1000,
  enabled = true,
): OutlookArtifactState {
  const [state, setState] = useState<OutlookArtifactState>(INITIAL_STATE);
  const probabilityHourCacheRef = useRef<Map<number, OutlookProbabilityHour>>(new Map());
  const riskPolygonCacheRef = useRef<Map<number, OutlookArtifactFeatureCollection>>(new Map());
  const mergedRiskPolygonsRef = useRef<OutlookArtifactFeatureCollection | undefined>(undefined);
  const prefetchingHoursRef = useRef<Set<number>>(new Set());
  const warmedRiskPolygonCyclesRef = useRef<Set<string>>(new Set());
  const cacheCycleRef = useRef<string | null>(null);
  const isMountedRef = useRef(true);
  // Requirement 4.5/4.7: remember the last valid (ready) outlook so it can keep
  // displaying during a reload and be retained if the reload fails.
  const lastValidStateRef = useRef<OutlookArtifactState | null>(null);
  // Requirement 4.6: pending reload-timeout timer, cleared on settle/unmount.
  const reloadTimerRef = useRef<number | null>(null);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Track the last valid outlook for reload resilience (Requirement 4.5, 4.7).
  useEffect(() => {
    if (state.status === 'ready' && state.artifacts) {
      lastValidStateRef.current = state;
    }
  }, [state]);

  const resetProbabilityCacheIfNeeded = (incremental: OutlookIncrementalIndex) => {
    // Requirement 4.1/4.4: key the cache on the durable cycle identity so it
    // survives a page refresh when the cycle is unchanged, and is only cleared
    // (replaced) when a genuinely different cycle loads.
    const cacheKey = cycleIdentityKey(incremental);
    if (cacheCycleRef.current !== cacheKey) {
      probabilityHourCacheRef.current.clear();
      riskPolygonCacheRef.current.clear();
      mergedRiskPolygonsRef.current = undefined;
      prefetchingHoursRef.current.clear();
      cacheCycleRef.current = cacheKey;
    }
    return cacheKey;
  };

  const cacheProbabilityHours = (hours: OutlookProbabilityHour[]) => {
    hours.forEach((hour) => probabilityHourCacheRef.current.set(hour.forecastHour, hour));
    return mergeProbabilityHours(Array.from(probabilityHourCacheRef.current.values()));
  };

  const mergeCachedHoursIntoState = (
    previous: OutlookArtifactState,
    incremental: OutlookIncrementalIndex,
    hours: OutlookProbabilityHour[],
  ): OutlookArtifactState => {
    const cachedHours = mergeProbabilityHours(
      Array.from(probabilityHourCacheRef.current.values()),
      hours,
    );
    if (!previous.artifacts) return previous;
    const existingTiles = previous.artifacts.probabilityTiles;
    const probabilityTiles = existingTiles
      ? {
          ...existingTiles,
          hours: mergeProbabilityHours(existingTiles.hours, cachedHours),
        }
      : probabilityTilesFromIncremental(incremental, cachedHours);
    return {
      ...previous,
      artifacts: {
        ...previous.artifacts,
        probabilityTiles,
      },
    };
  };

  useEffect(() => {
    if (!enabled) {
      setState(INITIAL_STATE);
      return undefined;
    }

    let cancelled = false;
    let loadInFlight = false;
    const controller = new AbortController();

    const showCachedSelectedHour = () => {
      if (selectedForecastHour === undefined) return;
      const cachedSelectedHour = probabilityHourCacheRef.current.get(selectedForecastHour);
      setState((previous) => {
        const incremental = previous.artifacts?.incrementalIndex;
        if (!incremental) return previous;
        const ready = incremental.readyForecastHours ?? [];
        const failed = incremental.failedForecastHours ?? [];
        const pending = incremental.pendingForecastHours ?? [];
        const requested = incremental.requestedForecastHours ?? [];
        const available = requested.length > 0
          ? requested
          : Array.from(new Set([...ready, ...failed, ...pending]));
        const artifactForecastHour = resolveArtifactForecastHour(
          incremental.cycleTimeISO,
          selectedForecastHour,
          selectedValidTimeISO,
          available,
        );
        const cachedRiskPolygons = artifactForecastHour !== undefined
          ? riskPolygonCacheRef.current.get(artifactForecastHour)
          : undefined;
        if (!cachedSelectedHour && !cachedRiskPolygons) return previous;
        let next = cachedSelectedHour
          ? mergeCachedHoursIntoState(previous, incremental, [cachedSelectedHour])
          : previous;
        if (cachedRiskPolygons && next.artifacts) {
          next = {
            status: 'ready',
            artifacts: {
              ...next.artifacts,
              riskPolygons: displayRiskPolygonsForSelectedHour(
                cachedRiskPolygons,
                selectedForecastHour,
                selectedValidTimeISO,
              ),
              selectedArtifactForecastHour: artifactForecastHour,
              selectedHourStatus: 'ready',
            },
            message: null,
          };
        }
        return next;
      });
    };

    const warmMergedRiskPolygons = async (
      incremental: OutlookIncrementalIndex,
      cacheKey: string,
    ) => {
      if (warmedRiskPolygonCyclesRef.current.has(cacheKey)) return;
      warmedRiskPolygonCyclesRef.current.add(cacheKey);
      const readyHours = [...(incremental.readyForecastHours ?? [])]
        .map((hour) => Number(hour))
        .filter((hour) => Number.isFinite(hour) && hour >= 0 && hour <= 96)
        .sort((a, b) => {
          const selected = selectedForecastHour ?? 0;
          return Math.abs(a - selected) - Math.abs(b - selected) || a - b;
        });
      if (readyHours.length === 0) {
        warmedRiskPolygonCyclesRef.current.delete(cacheKey);
        return;
      }
      await Promise.allSettled(readyHours.map(async (forecastHour) => {
        if (riskPolygonCacheRef.current.has(forecastHour)) return;
        const riskPolygons = await fetchJson<OutlookArtifactFeatureCollection>(
          `/api/outlook/incremental/hour/${forecastHour}/risk-polygons`,
          undefined,
          activeRegion,
        );
        if (!isMountedRef.current || cacheCycleRef.current !== cacheKey) return;
        riskPolygonCacheRef.current.set(forecastHour, riskPolygons);
        const mergedRiskPolygons = mergeRiskPolygonCache(riskPolygonCacheRef.current);
        mergedRiskPolygonsRef.current = mergedRiskPolygons;
        setState((previous) => {
          if (!previous.artifacts) return previous;
          return {
            ...previous,
            artifacts: {
              ...previous.artifacts,
              aggregateRiskPolygons: mergedRiskPolygons,
            },
          };
        });
        showCachedSelectedHour();
      }));
    };

    const prefetchNeighborProbabilityTiles = async (
      incremental: OutlookIncrementalIndex,
      cacheKey: string,
    ) => {
      if (selectedForecastHour === undefined) return;
      const ready = incremental.readyForecastHours ?? [];
      const failed = incremental.failedForecastHours ?? [];
      const pending = incremental.pendingForecastHours ?? [];
      const requested = incremental.requestedForecastHours ?? [];
      const available = requested.length > 0
        ? requested
        : Array.from(new Set([...ready, ...failed, ...pending]));
      const targetDisplayHours = Array.from({ length: PREFETCH_RADIUS * 2 + 1 }, (_, index) =>
        selectedForecastHour - PREFETCH_RADIUS + index,
      )
        .filter((hour) => hour >= 0 && hour <= 96 && hour !== selectedForecastHour)
        .sort((a, b) => Math.abs(a - selectedForecastHour) - Math.abs(b - selectedForecastHour));

      await Promise.allSettled(targetDisplayHours.map(async (displayHour) => {
        if (cacheCycleRef.current !== cacheKey) return;
        if (probabilityHourCacheRef.current.has(displayHour) || prefetchingHoursRef.current.has(displayHour)) return;
        const displayValidTime = displayedValidTimeISO(selectedForecastHour, selectedValidTimeISO, displayHour);
        const artifactHour = resolveArtifactForecastHour(
          incremental.cycleTimeISO,
          displayHour,
          displayValidTime,
          available,
        );
        if (artifactHour === undefined || !ready.includes(artifactHour)) return;
        prefetchingHoursRef.current.add(displayHour);
        try {
          const [tile, hourMetadata] = await Promise.all([
            fetchJson<OutlookProbabilityTile>(`/api/outlook/incremental/hour/${artifactHour}/probability-tile`, undefined, activeRegion),
            fetchJson<OutlookArtifactMetadata>(`/api/outlook/incremental/hour/${artifactHour}/metadata`, undefined, activeRegion).catch(() => undefined),
          ]);
          if (!isMountedRef.current || cacheCycleRef.current !== cacheKey) return;
          const probabilityHour = displayProbabilityHourForSelectedHour(
            tile,
            displayHour,
            displayValidTime,
            hourMetadata?.categoryCounts ?? hourMetadata?.aggregateCategoryCounts,
          );
          cacheProbabilityHours([probabilityHour]);
          if (!isMountedRef.current) return;
          setState((previous) => mergeCachedHoursIntoState(previous, incremental, [probabilityHour]));
        } finally {
          prefetchingHoursRef.current.delete(displayHour);
        }
      }));
    };

    const load = async () => {
      if (loadInFlight) return;
      loadInFlight = true;
      // Requirement 4.6: if this reload does not settle within 30s, treat it as
      // failed while retaining the last valid outlook and surfacing a per-day
      // status message (Requirement 4.7).
      if (reloadTimerRef.current !== null) {
        window.clearTimeout(reloadTimerRef.current);
      }
      reloadTimerRef.current = window.setTimeout(() => {
        if (cancelled) return;
        const retained = lastValidStateRef.current;
        if (retained?.artifacts) {
          setState({
            status: 'failed',
            artifacts: retained.artifacts,
            message: `${reloadDayLabel(retained)} outlook reload did not complete within 30 seconds; showing the last valid outlook.`,
          });
        }
      }, RELOAD_TIMEOUT_MS);
      if (!cancelled) {
        showCachedSelectedHour();
        setState((previous) => {
          if (hasTileForDisplayedHour(previous.artifacts, selectedForecastHour)) return previous;
          if (previous.status === 'loading') return previous;
          return {
            status: 'loading',
            artifacts: previous.artifacts,
            message: null,
          };
        });
      }
      try {
        const incremental = await fetchJson<OutlookIncrementalIndex>('/api/outlook/incremental', controller.signal, activeRegion)
          .catch(() => undefined);
        if (incremental && selectedForecastHour !== undefined) {
          const spcVerification = incremental.spcVerification
            ?? await fetchOptionalSpcVerification(controller.signal, activeRegion);
          const incrementalMetadata: OutlookIncrementalIndex = spcVerification
            ? { ...incremental, spcVerification }
            : incremental;
          const cacheKey = resetProbabilityCacheIfNeeded(incremental);
          void warmMergedRiskPolygons(incremental, cacheKey);
          const ready = incremental.readyForecastHours ?? [];
          const failed = incremental.failedForecastHours ?? [];
          const pending = incremental.pendingForecastHours ?? [];
          const requested = incremental.requestedForecastHours ?? [];
          const available = requested.length > 0
            ? requested
            : Array.from(new Set([...ready, ...failed, ...pending]));
          const artifactForecastHour = resolveArtifactForecastHour(
            incremental.cycleTimeISO,
            selectedForecastHour,
            selectedValidTimeISO,
            available,
          );
          const selectedLabel = forecastHourLabel(selectedForecastHour);
          const artifactLabel = forecastHourLabel(artifactForecastHour);
          const hourContext = artifactForecastHour !== selectedForecastHour
            ? `${selectedLabel} valid time maps to generated ${artifactLabel}.`
            : selectedLabel;
          if (artifactForecastHour !== undefined && ready.includes(artifactForecastHour)) {
            const cachedSelectedHour = probabilityHourCacheRef.current.get(selectedForecastHour);
            if (cachedSelectedHour && !cancelled) {
              setState((previous) => mergeCachedHoursIntoState(previous, incremental, [cachedSelectedHour]));
            }
            void prefetchNeighborProbabilityTiles(incremental, cacheKey);
            const [riskPolygons, hourMetadata, timelineSummary] = await Promise.all([
              fetchJson<OutlookArtifactFeatureCollection>(`/api/outlook/incremental/hour/${artifactForecastHour}/risk-polygons`, controller.signal, activeRegion),
              fetchJson<OutlookArtifactMetadata>(`/api/outlook/incremental/hour/${artifactForecastHour}/metadata`, controller.signal, activeRegion).catch(() => undefined),
              fetchJson<OutlookIncrementalSummary>('/api/outlook/incremental/summary', controller.signal, activeRegion).catch(() => undefined),
            ]);
            riskPolygonCacheRef.current.set(artifactForecastHour, riskPolygons);
            const displayRiskPolygons = displayRiskPolygonsForSelectedHour(riskPolygons, selectedForecastHour, selectedValidTimeISO);
            const readyMetadata: OutlookArtifactMetadata = {
              ...incrementalMetadata,
              ...hourMetadata,
              spcVerification: hourMetadata?.spcVerification ?? incremental.spcVerification ?? spcVerification ?? null,
              mode: 'incremental',
              selectedArtifactForecastHour: artifactForecastHour,
              artifactForecastHour,
            };
            const cachedHours = mergeProbabilityHours(Array.from(probabilityHourCacheRef.current.values()));
            const cachedProbabilityTiles = cachedHours.length > 0
              ? probabilityTilesFromIncremental(incremental, cachedHours)
              : undefined;
            if (!cancelled) {
              setState((previous) => reconcileReadyState(previous, {
                status: 'ready',
                artifacts: {
                  metadata: readyMetadata,
                  riskPolygons: displayRiskPolygons,
                  aggregateRiskPolygons: previous.artifacts?.aggregateRiskPolygons ?? mergedRiskPolygonsRef.current,
                  probabilityTiles: cachedProbabilityTiles,
                  timelineSummary,
                  incrementalIndex: incremental,
                  selectedArtifactForecastHour: artifactForecastHour,
                  selectedHourStatus: 'ready',
                },
                message: null,
              }));
            }
            let tile: OutlookProbabilityTile;
            try {
              tile = await fetchJson<OutlookProbabilityTile>(`/api/outlook/incremental/hour/${artifactForecastHour}/probability-tile`, controller.signal, activeRegion);
            } catch {
              return;
            }
            if (!cancelled) {
              const probabilityHour = displayProbabilityHourForSelectedHour(
                tile,
                selectedForecastHour,
                selectedValidTimeISO,
                hourMetadata?.categoryCounts ?? hourMetadata?.aggregateCategoryCounts,
              );
              const nextCachedHours = cacheProbabilityHours([probabilityHour]);
              const probabilityTiles = probabilityTilesFromIncremental(incremental, nextCachedHours);
              setState((previous) => reconcileReadyState(previous, {
                status: 'ready',
                artifacts: {
                  metadata: {
                    ...readyMetadata,
                    artifactValidTimeISO: tile.validTimeISO,
                  },
                  riskPolygons: previous.artifacts?.riskPolygons ?? displayRiskPolygons,
                  aggregateRiskPolygons: previous.artifacts?.aggregateRiskPolygons ?? mergedRiskPolygonsRef.current,
                  probabilityTiles,
                  timelineSummary: previous.artifacts?.timelineSummary ?? timelineSummary,
                  incrementalIndex: incremental,
                  selectedArtifactForecastHour: artifactForecastHour,
                  selectedHourStatus: 'ready',
                },
                message: null,
              }));
            }
            return;
          }
          if (artifactForecastHour !== undefined && pending.includes(artifactForecastHour)) {
            if (!cancelled) {
              const nextState: OutlookArtifactState = {
                status: 'pending',
                artifacts: {
                  metadata: incrementalMetadata,
                  riskPolygons: { type: 'FeatureCollection', features: [] },
                  incrementalIndex: incremental,
                  selectedArtifactForecastHour: artifactForecastHour,
                  selectedHourStatus: 'pending',
                },
                message: `${hourContext} That generated hour is still generating.`,
              };
              setState((previous) => preserveReadySelectedHour(previous, selectedForecastHour, nextState));
            }
            return;
          }
          if (artifactForecastHour !== undefined && failed.includes(artifactForecastHour)) {
            const failure = incremental.failedHours?.find((item) => item.forecastHour === artifactForecastHour);
            if (!cancelled) {
              const nextState: OutlookArtifactState = {
                status: 'failed',
                artifacts: {
                  metadata: incrementalMetadata,
                  riskPolygons: { type: 'FeatureCollection', features: [] },
                  incrementalIndex: incremental,
                  selectedArtifactForecastHour: artifactForecastHour,
                  selectedHourStatus: 'failed',
                },
                message: failure?.error ?? `${hourContext} That generated hour failed to generate.`,
              };
              setState((previous) => preserveReadySelectedHour(previous, selectedForecastHour, nextState));
            }
            return;
          }
          if (artifactForecastHour === undefined || (requested.length > 0 && !requested.includes(artifactForecastHour))) {
            if (!cancelled) {
              const readyLabel = ready.length > 0
                ? ready.map((hour) => `F${String(hour).padStart(2, '0')}`).join(', ')
                : 'none';
              const nextState: OutlookArtifactState = {
                status: 'missing',
                artifacts: {
                  metadata: incrementalMetadata,
                  riskPolygons: { type: 'FeatureCollection', features: [] },
                  incrementalIndex: incremental,
                  selectedArtifactForecastHour: artifactForecastHour,
                  selectedHourStatus: 'missing',
                },
                message: `${hourContext} That generated hour has not been generated yet. Ready generated hours: ${readyLabel}.`,
              };
              setState((previous) => preserveReadySelectedHour(previous, selectedForecastHour, nextState));
            }
            return;
          }
        }

        const [metadata, riskPolygons, aggregateRiskPolygons, probabilityTiles, spcVerification] = await Promise.all([
          fetchJson<OutlookArtifactMetadata>('/api/outlook/latest', controller.signal, activeRegion),
          fetchJson<OutlookArtifactFeatureCollection>('/api/outlook/risk-polygons', controller.signal, activeRegion),
          fetchJson<OutlookArtifactFeatureCollection>('/api/outlook/aggregate-risk-polygons', controller.signal, activeRegion).catch(() => undefined),
          fetchJson<OutlookProbabilityTiles>('/api/outlook/probability-tiles', controller.signal, activeRegion).catch(() => undefined),
          fetchOptionalSpcVerification(controller.signal, activeRegion),
        ]);
        const displayProbabilityTiles = probabilityTilesWithDisplayedHour(
          probabilityTiles,
          metadata,
          selectedForecastHour,
          selectedValidTimeISO,
        );
        if (!cancelled) {
          setState((previous) => reconcileReadyState(previous, {
            status: 'ready',
            artifacts: {
              metadata: {
                ...metadata,
                spcVerification: metadata.spcVerification ?? spcVerification ?? null,
                mode: 'full',
              },
              riskPolygons,
              aggregateRiskPolygons,
              probabilityTiles: displayProbabilityTiles,
              selectedHourStatus: 'ready',
            },
            message: null,
          }));
        }
      } catch (error) {
        if (cancelled || controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : String(error);
        // Requirement 4.7: on reload failure, retain the last valid displayed
        // outlook and surface a per-day status message describing the failure.
        const retained = lastValidStateRef.current;
        if (retained?.artifacts) {
          setState({
            status: 'failed',
            artifacts: retained.artifacts,
            message: `${reloadDayLabel(retained)} outlook reload failed (${message}); showing the last valid outlook.`,
          });
          return;
        }
        const nextState: OutlookArtifactState = {
          status: message === 'artifact_missing' ? 'missing' : 'error',
          artifacts: null,
          message: message === 'artifact_missing'
            ? 'Generated HRRR/XGBoost outlook artifacts are not available yet.'
            : `Generated outlook artifact fetch failed: ${message}`,
        };
        setState((previous) => preserveReadySelectedHour(previous, selectedForecastHour, nextState));
      } finally {
        if (reloadTimerRef.current !== null) {
          window.clearTimeout(reloadTimerRef.current);
          reloadTimerRef.current = null;
        }
        loadInFlight = false;
      }
    };

    load();
    const interval = window.setInterval(load, refreshMs);
    return () => {
      cancelled = true;
      loadInFlight = false;
      controller.abort();
      window.clearInterval(interval);
      if (reloadTimerRef.current !== null) {
        window.clearTimeout(reloadTimerRef.current);
        reloadTimerRef.current = null;
      }
    };
  }, [refreshMs, selectedForecastHour, selectedValidTimeISO, activeRegion, enabled]);

  return state;
}

export function useMergedD1Verification(
  activeRegion: ActiveRegion = 'conus',
  selectedDate?: string,
  enabled = true,
  day: 1 | 2 = 1,
): MergedD1VerificationSummary | null {
  const [data, setData] = useState<MergedD1VerificationSummary | null>(null);

  useEffect(() => {
    if (!enabled) {
      setData(null);
      return undefined;
    }

    const controller = new AbortController();
    const base = `/api/outlook/merged-d${day}-verification`;
    const url = selectedDate ? `${base}?date=${selectedDate}` : base;
    fetchJson<MergedD1VerificationSummary>(
      url,
      controller.signal,
      activeRegion,
    )
      .then((result) => setData(result))
      .catch(() => setData(null));
    return () => controller.abort();
  }, [activeRegion, selectedDate, enabled, day]);

  return data;
}

export function useMergedD1Artifacts(
  activeRegion: ActiveRegion = 'conus',
  selectedDate?: string,
  options: { enabled?: boolean; day?: 1 | 2; backing?: 'pure' | 'blend' } = {},
): OutlookArtifactState {
  const [state, setState] = useState<OutlookArtifactState>(INITIAL_STATE);
  const enabled = options.enabled ?? true;
  const day = options.day ?? 1;
  const backing = options.backing ?? 'blend';

  useEffect(() => {
    if (!enabled) {
      setState(INITIAL_STATE);
      return undefined;
    }

    const controller = new AbortController();
    setState(INITIAL_STATE);

    const load = async () => {
      try {
        const params = new URLSearchParams();
        if (selectedDate) params.set('date', selectedDate);
        const verifyQuery = selectedDate ? `?date=${selectedDate}` : '';
        // Pure ("Our Model") outlook is requested with backing=pure; the SPC
        // blend (default) omits the param.
        if (backing === 'pure') params.set('backing', 'pure');
        const outlookQuery = params.toString() ? `?${params.toString()}` : '';
        const [riskPolygons, mergedTile, verification] = await Promise.all([
          fetchJson<OutlookArtifactFeatureCollection>(`/api/outlook/merged-d${day}-risk-polygons${outlookQuery}`, controller.signal, activeRegion),
          fetchJson<OutlookProbabilityTile>(`/api/outlook/merged-d${day}-probability-tile${outlookQuery}`, controller.signal, activeRegion),
          fetchJson<MergedD1VerificationSummary>(`/api/outlook/merged-d${day}-verification${verifyQuery}`, controller.signal, activeRegion),
        ]);

        const probabilityTiles: OutlookProbabilityTiles = {
          cycle: 'Merged Outlook',
          hours: [
            {
              forecastHour: 0,
              validTimeISO: mergedTile.validTimeISO,
              tile: mergedTile,
            },
          ],
        };

        const metadata: OutlookArtifactMetadata = {
          generatedAtISO: new Date().toISOString(),
          cycle: 'Merged Outlook',
          spcVerification: verification,
          mode: 'full',
        };

        setState({
          status: 'ready',
          artifacts: {
            metadata,
            riskPolygons,
            probabilityTiles,
            selectedArtifactForecastHour: 0,
            selectedHourStatus: 'ready',
          },
          message: null,
        });
      } catch (error) {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : String(error);
        setState({
          status: 'error',
          artifacts: null,
          message: `Failed to load merged D1 artifacts: ${message}`,
        });
      }
    };

    load();
    return () => controller.abort();
  }, [activeRegion, selectedDate, enabled, day, backing]);

  return state;
}

export function useSpcBackedHourArtifacts(
  activeRegion: ActiveRegion = 'conus',
  forecastHour: number | undefined,
  enabled = true,
): OutlookArtifactState {
  const [state, setState] = useState<OutlookArtifactState>(INITIAL_STATE);

  useEffect(() => {
    if (!enabled || forecastHour === undefined) {
      setState(INITIAL_STATE);
      return undefined;
    }

    const controller = new AbortController();
    setState(INITIAL_STATE);

    const load = async () => {
      try {
        const tile = await fetchJson<OutlookProbabilityTile>(
          `/api/outlook/incremental/hour/${forecastHour}/spc-backed-tile?mode=blend`,
          controller.signal,
          activeRegion,
        );
        const probabilityTiles: OutlookProbabilityTiles = {
          cycle: 'SPC-backed hourly',
          hours: [
            {
              forecastHour,
              validTimeISO: tile.validTimeISO,
              tile,
            },
          ],
        };
        const metadata: OutlookArtifactMetadata = {
          generatedAtISO: new Date().toISOString(),
          cycle: 'SPC-backed hourly',
          mode: 'full',
        };
        setState({
          status: 'ready',
          artifacts: {
            metadata,
            riskPolygons: tile.riskShapes ?? { type: 'FeatureCollection', features: [] },
            probabilityTiles,
            selectedArtifactForecastHour: forecastHour,
            selectedHourStatus: 'ready',
          },
          message: null,
        });
      } catch (error) {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : String(error);
        setState({
          status: 'error',
          artifacts: null,
          message: `Failed to load SPC-backed hour: ${message}`,
        });
      }
    };

    load();
    return () => controller.abort();
  }, [activeRegion, forecastHour, enabled]);

  return state;
}

/** State returned by {@link useSpcHazardShapes} for the live SPC hazard outlook. */
export interface SpcHazardShapesState {
  status: 'loading' | 'ready' | 'missing' | 'error';
  shapes: OutlookProbabilityShapeFeatureCollection | null;
  message: string | null;
}

const SPC_HAZARD_INITIAL_STATE: SpcHazardShapesState = {
  status: 'loading',
  shapes: null,
  message: null,
};

/**
 * Fetches the live SPC hazard outlook (tornado/hail/wind/thunder probability
 * shapes) from `/api/outlook/spc-hazard-shapes` (Requirement 1.6).
 *
 * The result starts as `loading`, becomes `ready` with the normalized shape
 * collection on success, maps HTTP 404 (`artifact_missing`) to `missing`, and
 * maps any other failure to `error`. On `missing`/`error` the caller keeps
 * showing the generated hazard outlook and preserves the selected hazard type.
 */
export function useSpcHazardShapes(
  activeRegion: ActiveRegion = 'conus',
  enabled = true,
): SpcHazardShapesState {
  const [state, setState] = useState<SpcHazardShapesState>(SPC_HAZARD_INITIAL_STATE);

  useEffect(() => {
    if (!enabled) {
      setState(SPC_HAZARD_INITIAL_STATE);
      return undefined;
    }

    const controller = new AbortController();
    setState(SPC_HAZARD_INITIAL_STATE);

    const load = async () => {
      try {
        const shapes = await fetchJson<OutlookProbabilityShapeFeatureCollection>(
          '/api/outlook/spc-hazard-shapes',
          controller.signal,
          activeRegion,
        );
        setState({ status: 'ready', shapes, message: null });
      } catch (error) {
        if (controller.signal.aborted) return;
        // A definitive not-found is distinct from a processing/transport error
        // so the view can surface an "unavailable" message versus an error.
        if (error instanceof Error && error.message === 'artifact_missing') {
          setState({
            status: 'missing',
            shapes: null,
            message: 'SPC hazard outlook unavailable for the selected outlook.',
          });
          return;
        }
        const message = error instanceof Error ? error.message : String(error);
        setState({
          status: 'error',
          shapes: null,
          message: `Failed to load SPC hazard outlook: ${message}`,
        });
      }
    };

    load();
    return () => controller.abort();
  }, [activeRegion, enabled]);

  return state;
}


export function useSpcStormReports(
  activeRegion: ActiveRegion = 'conus',
  selectedDate?: string,
  enabled = true,
): SpcStormReport[] {
  const [reports, setReports] = useState<SpcStormReport[]>([]);

  useEffect(() => {
    if (!enabled) {
      setReports([]);
      return undefined;
    }

    const controller = new AbortController();
    const query = selectedDate ? `?date=${selectedDate}` : '';
    
    fetchJson<SpcStormReportsResponse>(
      `/api/outlook/spc-storm-reports${query}`,
      controller.signal,
      activeRegion,
    )
      .then((res) => {
        if (res && Array.isArray(res.reports)) {
          setReports(res.reports);
        } else {
          setReports([]);
        }
      })
      .catch(() => {
        setReports([]);
      });

    return () => controller.abort();
  }, [activeRegion, selectedDate, enabled]);

  return reports;
}


interface EnhPlusArchiveIndexEntry {
  date: string;
  maxCategory?: string;
  autoMaxCategory?: string;
  spcMaxCategory?: string;
  reportCounts?: { tornado: number; hail: number; wind: number; total: number };
  windowStartISO?: string;
  windowEndISO?: string;
  updatedAtISO?: string;
}

const ENH_PLUS_EMPTY_FC = { type: 'FeatureCollection', features: [] };

function enhPlusCycleIso(windowStartISO: string | undefined): string {
  if (!windowStartISO) return '';
  const parsed = new Date(windowStartISO);
  if (Number.isNaN(parsed.getTime())) return windowStartISO;
  parsed.setUTCHours(0, 0, 0, 0);
  return parsed.toISOString().replace('.000Z', 'Z');
}

function enhPlusLabel(dateStr: string, spcLabel: string | undefined): string {
  const parsed = new Date(`${dateStr}T12:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return dateStr;
  const month = parsed.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' });
  const suffix = spcLabel === 'MDT' || spcLabel === 'MOD'
    ? ' (Moderate)'
    : spcLabel === 'HIGH'
      ? ' (High)'
      : '';
  return `${month} ${parsed.getUTCDate()}, ${parsed.getUTCFullYear()}${suffix}`;
}

/**
 * Live, auto-accumulating ENH+ risk archive. Returns events shaped like the
 * static historical catalog so the verification view can render them the same
 * way. Storm reports refresh on the backend as the convective day plays out.
 */
export function useEnhPlusArchiveEvents(
  activeRegion: ActiveRegion = 'conus',
  enabled = true,
): { events: HistoricalEnhPlusEvent[]; status: ArtifactStatus } {
  const [events, setEvents] = useState<HistoricalEnhPlusEvent[]>([]);
  const [status, setStatus] = useState<ArtifactStatus>('loading');

  useEffect(() => {
    if (!enabled) {
      setEvents([]);
      setStatus('missing');
      return undefined;
    }
    const controller = new AbortController();

    const load = async () => {
      setStatus('loading');
      try {
        const index = await fetchJson<{ dates?: EnhPlusArchiveIndexEntry[] }>(
          '/api/outlook/enh-plus-archive-available-dates',
          controller.signal,
          activeRegion,
        );
        const entries = Array.isArray(index.dates) ? index.dates : [];
        const built = await Promise.all(
          entries.map(async (entry) => {
            const query = `?date=${entry.date}`;
            const [verification, riskPolygons, hazardShapes, tile, spcDay1, spcHazardShapes, reports, riskPolygonsPure, hazardShapesPure] = await Promise.all([
              fetchJson<MergedD1VerificationSummary>(`/api/outlook/enh-plus-archive-verification${query}`, controller.signal, activeRegion).catch(() => null),
              fetchJson<OutlookArtifactFeatureCollection>(`/api/outlook/enh-plus-archive-risk-polygons${query}`, controller.signal, activeRegion).catch(() => null),
              fetchJson<OutlookProbabilityShapeFeatureCollection>(`/api/outlook/enh-plus-archive-hazard-shapes${query}`, controller.signal, activeRegion).catch(() => null),
              fetchJson<OutlookProbabilityTile>(`/api/outlook/enh-plus-archive-probability-tile${query}`, controller.signal, activeRegion).catch(() => null),
              fetchJson<SpcCategoryFeatureCollection>(`/api/outlook/enh-plus-archive-spc-category${query}`, controller.signal, activeRegion).catch(() => null),
              // Requirement 3.4: 10s timeout and up to 2 retries before the
              // archived SPC hazard outlook is treated as unavailable. A
              // failure, timeout, or 404 falls back to null so the archived
              // categorical outlook still displays (Requirement 3.6).
              fetchWithTimeoutRetries<OutlookProbabilityShapeFeatureCollection>(
                `/api/outlook/enh-plus-archive-spc-hazard-shapes${query}`,
                { timeoutMs: 10000, retries: 2, signal: controller.signal, activeRegion },
              ).catch(() => null),
              fetchJson<{ reports?: SpcStormReport[] }>(`/api/outlook/enh-plus-archive-storm-reports${query}`, controller.signal, activeRegion).catch(() => null),
              // Pure ("Our Model") merged outlook so the SPC backing toggle
              // works on live archive events. Older archives without the pure
              // artifact 404 and fall back to the blend outlook below.
              fetchJson<OutlookArtifactFeatureCollection>(`/api/outlook/enh-plus-archive-risk-polygons-pure${query}`, controller.signal, activeRegion).catch(() => null),
              fetchJson<OutlookProbabilityShapeFeatureCollection>(`/api/outlook/enh-plus-archive-hazard-shapes-pure${query}`, controller.signal, activeRegion).catch(() => null),
            ]);
            if (!verification || !riskPolygons) return null;
            const event: HistoricalEnhPlusEvent = {
              id: `enh-plus-archive-${entry.date}`,
              label: enhPlusLabel(entry.date, entry.spcMaxCategory),
              eventDate: entry.date,
              cycleTimeISO: enhPlusCycleIso(entry.windowStartISO),
              eventWindowStartISO: entry.windowStartISO ?? `${entry.date}T12:00:00Z`,
              eventWindowEndISO: entry.windowEndISO ?? '',
              forecastHours: [],
              maxSpcCategory: entry.spcMaxCategory || entry.maxCategory || 'ENH',
              gridStride: (verification as { gridStride?: number | null }).gridStride ?? null,
              tileStride: tile?.stride ?? null,
              tileShape: tile?.shape ?? [],
              sourceArtifactDir: 'live-enh-plus-archive',
              summary: verification as unknown as Record<string, unknown>,
              riskPolygons,
              hazardProbabilityShapes: (hazardShapes ?? ENH_PLUS_EMPTY_FC) as unknown as OutlookProbabilityShapeFeatureCollection,
              // Live archive events serve a separate pure ("Our Model")
              // outlook when available; older archives without it fall back to
              // the SPC-backed (blend) outlook so the toggle is a no-op there.
              riskPolygonsPure: riskPolygonsPure ?? riskPolygons,
              hazardProbabilityShapesPure: (hazardShapesPure ?? hazardShapes ?? ENH_PLUS_EMPTY_FC) as unknown as OutlookProbabilityShapeFeatureCollection,
              spcDay1: (spcDay1 ?? ENH_PLUS_EMPTY_FC) as unknown as SpcCategoryFeatureCollection,
              // Requirement 3.1/3.7/3.8: populate the archived SPC hazard
              // outlook from the normalized response (which carries
              // availableHazards/hazardsPresent for per-hazard partial
              // availability). Requirement 3.6: when unavailable, fall back to
              // the empty collection so the categorical outlook still displays.
              spcHazardProbabilityShapes: (spcHazardShapes ?? ENH_PLUS_EMPTY_FC) as unknown as OutlookProbabilityShapeFeatureCollection,
              stormReports: reports?.reports ?? [],
            };
            return event;
          }),
        );
        if (controller.signal.aborted) return;
        const valid = built.filter((item): item is HistoricalEnhPlusEvent => item !== null);
        setEvents(valid);
        setStatus(valid.length ? 'ready' : 'missing');
      } catch (error) {
        if (controller.signal.aborted) return;
        setEvents([]);
        setStatus('error');
      }
    };

    load();
    return () => controller.abort();
  }, [activeRegion, enabled]);

  return { events, status };
}
