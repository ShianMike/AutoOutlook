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
  SpcVerificationSummary,
  MergedD1VerificationSummary,
  SpcStormReport,
  SpcStormReportsResponse,
} from '../types/outlookArtifacts';
import { apiUrl } from '../utils/apiBase';

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

function incrementalCacheKey(incremental: OutlookIncrementalIndex): string {
  return [
    incremental.cycle,
    incremental.cycleTimeISO ?? '',
    incremental.generatedAtISO ?? '',
    incremental.featureSchemaHash ?? '',
  ].join('|');
}

export function useOutlookArtifacts(
  selectedForecastHour?: number,
  selectedValidTimeISO?: string,
  activeRegion: ActiveRegion = 'conus',
  refreshMs = 15 * 1000,
): OutlookArtifactState {
  const [state, setState] = useState<OutlookArtifactState>(INITIAL_STATE);
  const probabilityHourCacheRef = useRef<Map<number, OutlookProbabilityHour>>(new Map());
  const riskPolygonCacheRef = useRef<Map<number, OutlookArtifactFeatureCollection>>(new Map());
  const mergedRiskPolygonsRef = useRef<OutlookArtifactFeatureCollection | undefined>(undefined);
  const prefetchingHoursRef = useRef<Set<number>>(new Set());
  const warmedRiskPolygonCyclesRef = useRef<Set<string>>(new Set());
  const cacheCycleRef = useRef<string | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const resetProbabilityCacheIfNeeded = (incremental: OutlookIncrementalIndex) => {
    const cacheKey = incrementalCacheKey(incremental);
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
              setState((previous) => ({
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
              setState((previous) => ({
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
          setState({
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
          });
        }
      } catch (error) {
        if (cancelled || controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : String(error);
        const nextState: OutlookArtifactState = {
          status: message === 'artifact_missing' ? 'missing' : 'error',
          artifacts: null,
          message: message === 'artifact_missing'
            ? 'Generated HRRR/XGBoost outlook artifacts are not available yet.'
            : `Generated outlook artifact fetch failed: ${message}`,
        };
        setState((previous) => preserveReadySelectedHour(previous, selectedForecastHour, nextState));
      } finally {
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
    };
  }, [refreshMs, selectedForecastHour, selectedValidTimeISO, activeRegion]);

  return state;
}

export function useMergedD1Verification(
  activeRegion: ActiveRegion = 'conus',
  selectedDate?: string,
): MergedD1VerificationSummary | null {
  const [data, setData] = useState<MergedD1VerificationSummary | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const url = selectedDate
      ? `/api/outlook/merged-d1-verification?date=${selectedDate}`
      : '/api/outlook/merged-d1-verification';
    fetchJson<MergedD1VerificationSummary>(
      url,
      controller.signal,
      activeRegion,
    )
      .then((result) => setData(result))
      .catch(() => setData(null));
    return () => controller.abort();
  }, [activeRegion, selectedDate]);

  return data;
}

export function useMergedD1Artifacts(
  activeRegion: ActiveRegion = 'conus',
  selectedDate?: string,
): OutlookArtifactState {
  const [state, setState] = useState<OutlookArtifactState>(INITIAL_STATE);

  useEffect(() => {
    const controller = new AbortController();
    setState(INITIAL_STATE);

    const load = async () => {
      try {
        const query = selectedDate ? `?date=${selectedDate}` : '';
        const [riskPolygons, mergedTile, verification] = await Promise.all([
          fetchJson<OutlookArtifactFeatureCollection>(`/api/outlook/merged-d1-risk-polygons${query}`, controller.signal, activeRegion),
          fetchJson<OutlookProbabilityTile>(`/api/outlook/merged-d1-probability-tile${query}`, controller.signal, activeRegion),
          fetchJson<MergedD1VerificationSummary>(`/api/outlook/merged-d1-verification${query}`, controller.signal, activeRegion),
        ]);

        const probabilityTiles: OutlookProbabilityTiles = {
          cycle: 'Merged D1',
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
          cycle: 'Merged D1 Outlook',
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
  }, [activeRegion, selectedDate]);

  return state;
}

export function useSpcStormReports(
  activeRegion: ActiveRegion = 'conus',
  selectedDate?: string,
): SpcStormReport[] {
  const [reports, setReports] = useState<SpcStormReport[]>([]);

  useEffect(() => {
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
  }, [activeRegion, selectedDate]);

  return reports;
}


