import type {
  OutlookArtifacts,
  OutlookIncrementalIndex,
} from '../types/outlookArtifacts';
import type { OutlookArtifactState } from '../hooks/useOutlookArtifacts';

/**
 * Cycle identity for the outlook cache (Requirement 4).
 *
 * The identity is derived ONLY from durable cycle fields — the issuing day,
 * the cycle timestamp, and the outlook type. It deliberately excludes volatile
 * fields such as `generatedAtISO` and `featureSchemaHash`, which change on
 * every regeneration/refresh and were the source of the cache-corruption
 * defect (the cache was being evicted on every refresh even when the underlying
 * cycle was unchanged).
 */
export interface CycleIdentity {
  /** Calendar day the cycle was issued, e.g. "2026-06-10". */
  issuingDay: string;
  /** Durable cycle timestamp (ISO 8601). */
  cycleTimeISO: string;
  /** Which outlook the cycle belongs to. */
  outlookType: 'day1' | 'day2' | 'incremental';
}

/** Field used to key/order a cycle when a cycle timestamp is unavailable. */
function fallbackCycle(incremental: OutlookIncrementalIndex): string {
  return incremental.cycle ?? '';
}

/**
 * Derive the issuing day (calendar date) from a cycle timestamp.
 *
 * Returns the `YYYY-MM-DD` portion of a valid ISO timestamp. Falls back to the
 * raw cycle string when the timestamp is missing or unparseable so that the
 * identity remains stable and comparable.
 */
export function issuingDayFromCycle(
  cycleTimeISO: string | undefined,
  fallback: string,
): string {
  if (cycleTimeISO) {
    const parsedMs = Date.parse(cycleTimeISO);
    if (Number.isFinite(parsedMs)) {
      return new Date(parsedMs).toISOString().slice(0, 10);
    }
    // Unparseable but present: use the leading date-like prefix if any.
    const match = /^(\d{4}-\d{2}-\d{2})/.exec(cycleTimeISO);
    if (match) return match[1];
  }
  return fallback;
}

/**
 * Resolve the outlook type for a loaded outlook index.
 *
 * The incremental index always reports `mode: 'incremental'`, so the live
 * incremental outlook maps to the `'incremental'` cycle type. The `'day1'` and
 * `'day2'` identities are reserved for merged outlooks that carry an explicit
 * day marker on their metadata.
 */
export function outlookTypeFromIncremental(
  _incremental: OutlookIncrementalIndex,
): CycleIdentity['outlookType'] {
  return 'incremental';
}

/**
 * Build the durable {@link CycleIdentity} for a loaded outlook index.
 */
export function cycleIdentity(incremental: OutlookIncrementalIndex): CycleIdentity {
  const fallback = fallbackCycle(incremental);
  const cycleTimeISO = incremental.cycleTimeISO ?? fallback;
  return {
    issuingDay: issuingDayFromCycle(incremental.cycleTimeISO, fallback),
    cycleTimeISO,
    outlookType: outlookTypeFromIncremental(incremental),
  };
}

/**
 * Stable cache key for a loaded outlook, derived only from durable cycle
 * fields. Two loads of the same cycle (even across a page refresh) produce the
 * same key; a genuinely different cycle produces a different key.
 */
export function cycleIdentityKey(incremental: OutlookIncrementalIndex): string {
  const identity = cycleIdentity(incremental);
  return [identity.outlookType, identity.issuingDay, identity.cycleTimeISO].join('|');
}

/**
 * True when two cycle identities refer to the same durable cycle.
 */
export function sameCycleIdentity(a: CycleIdentity, b: CycleIdentity): boolean {
  return (
    a.outlookType === b.outlookType
    && a.issuingDay === b.issuingDay
    && a.cycleTimeISO === b.cycleTimeISO
  );
}

/**
 * Presence flags for the significant artifact fields of an outlook. A field is
 * "present" only when it carries meaningful (non-empty) data.
 */
interface OutlookFieldPresence {
  riskPolygons: boolean;
  aggregateRiskPolygons: boolean;
  probabilityTiles: boolean;
  timelineSummary: boolean;
  incrementalIndex: boolean;
}

function hasFeatures(collection: { features?: unknown[] } | null | undefined): boolean {
  return Boolean(collection?.features && collection.features.length > 0);
}

function fieldPresence(artifacts: OutlookArtifacts | null | undefined): OutlookFieldPresence {
  return {
    riskPolygons: hasFeatures(artifacts?.riskPolygons),
    aggregateRiskPolygons: hasFeatures(artifacts?.aggregateRiskPolygons),
    probabilityTiles: Boolean(
      artifacts?.probabilityTiles && artifacts.probabilityTiles.hours.length > 0,
    ),
    timelineSummary: Boolean(artifacts?.timelineSummary),
    incrementalIndex: Boolean(artifacts?.incrementalIndex),
  };
}

/**
 * An outlook state is an "empty replacement" when it carries no meaningful
 * outlook data — no artifacts at all, or artifacts with neither risk polygon
 * features nor any probability-tile hours.
 */
export function isEmptyOutlook(state: OutlookArtifactState | null | undefined): boolean {
  if (!state || !state.artifacts) return true;
  const presence = fieldPresence(state.artifacts);
  return !presence.riskPolygons && !presence.probabilityTiles;
}

/**
 * Decide whether `next` may overwrite `previous` in the cache (Requirement 4.2).
 *
 * When a newly loaded outlook shares the retained outlook's cycle identity, the
 * cache must reuse the retained outlook and reject any replacement that:
 *   - is empty (carries no meaningful outlook data), or
 *   - is missing a significant field that the retained outlook has.
 *
 * When there is no retained outlook, any non-empty replacement is accepted so a
 * fresh cache can be seeded (Requirement 4.8).
 *
 * @returns `true` when `next` is allowed to replace `previous`, `false` to
 *          reject the replacement and keep `previous`.
 */
export function guardReplacement(
  previous: OutlookArtifactState | null | undefined,
  next: OutlookArtifactState | null | undefined,
): boolean {
  // An empty replacement is never allowed to overwrite anything.
  if (isEmptyOutlook(next)) return false;

  // No retained outlook: accept any non-empty replacement.
  if (isEmptyOutlook(previous)) return true;

  const previousPresence = fieldPresence(previous!.artifacts);
  const nextPresence = fieldPresence(next!.artifacts);

  // Reject when the replacement is missing a field present in the retained cache.
  const keys = Object.keys(previousPresence) as Array<keyof OutlookFieldPresence>;
  for (const key of keys) {
    if (previousPresence[key] && !nextPresence[key]) {
      return false;
    }
  }

  return true;
}
