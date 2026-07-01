import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  cycleIdentity,
  cycleIdentityKey,
  sameCycleIdentity,
  guardReplacement,
  type CycleIdentity,
} from './cycleIdentity';
import type {
  OutlookArtifacts,
  OutlookArtifactFeatureCollection,
  OutlookIncrementalIndex,
} from '../types/outlookArtifacts';
import type { OutlookArtifactState } from '../hooks/useOutlookArtifacts';

// Feature: spc-hazard-outlook-archive, Property 5: Cache replaces on differing
// cycle identity, stores when absent.
//
// For any newly loaded outlook whose cycle identity differs from the cached
// outlook, the cache SHALL replace the cached outlook with the new one; and for
// any newly loaded outlook when no cache exists, the cache SHALL store it as the
// cached outlook for its cycle identity.
//
// Validates: Requirements 4.4, 4.8

/**
 * A cache entry pairs a durable cycle identity with the retained outlook state.
 */
interface CacheEntry {
  identity: CycleIdentity;
  state: OutlookArtifactState;
}

/**
 * Pure cache reconciliation mirroring the design's cache rules (Requirement 4):
 *   - no cache exists            -> store the newly loaded outlook   (4.8)
 *   - cycle identity differs     -> replace with the newly loaded    (4.4)
 *   - cycle identity matches     -> reuse, guarded by guardReplacement (4.1/4.2)
 *
 * Composed only from the module-under-test primitives.
 */
function reconcileCache(cached: CacheEntry | null, incoming: CacheEntry): CacheEntry {
  if (!cached) {
    // Requirement 4.8: no cache -> store the newly loaded outlook.
    return incoming;
  }
  if (!sameCycleIdentity(cached.identity, incoming.identity)) {
    // Requirement 4.4: differing identity -> replace with the newly loaded outlook.
    return incoming;
  }
  // Matching identity: reuse the cache, only accepting a valid replacement.
  return guardReplacement(cached.state, incoming.state)
    ? { identity: cached.identity, state: incoming.state }
    : cached;
}

/** Build a minimal, valid incremental index for a given cycle timestamp. */
function makeIndex(cycleTimeISO: string): OutlookIncrementalIndex {
  return {
    generatedAtISO: '2020-01-01T00:00:00.000Z',
    cycle: cycleTimeISO,
    cycleTimeISO,
    mode: 'incremental',
    requestedForecastHours: [],
    readyForecastHours: [],
    failedForecastHours: [],
    pendingForecastHours: [],
    status: 'complete',
  };
}

/** ISO timestamp at midnight UTC for a day offset from 2020-01-01. */
function isoForDay(dayOffset: number): string {
  const ms = Date.UTC(2020, 0, 1) + dayOffset * 24 * 60 * 60 * 1000;
  return new Date(ms).toISOString();
}

function riskCollection(categories: string[]): OutlookArtifactFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: categories.map((category, index) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'Polygon' as const,
        coordinates: [[[0, 0], [0, 1], [1, 1], [0, 0]]],
      },
      properties: {
        category: category as OutlookArtifactFeatureCollection['features'][number]['properties']['category'],
        forecastHour: index,
        validTimeISO: '2020-01-01T00:00:00.000Z',
      },
    })),
  };
}

/** Generator for an outlook state; may be empty or carry risk features. */
const stateArb: fc.Arbitrary<OutlookArtifactState> = fc.oneof(
  // Empty-ish states (no artifacts / no meaningful data).
  fc.constant<OutlookArtifactState>({ status: 'missing', artifacts: null, message: null }),
  // Non-empty states carrying at least one risk feature.
  fc.array(
    fc.constantFrom('MRGL', 'SLGT', 'ENH', 'MDT', 'HIGH'),
    { minLength: 1, maxLength: 4 },
  ).map((categories): OutlookArtifactState => {
    const artifacts: OutlookArtifacts = {
      metadata: {
        generatedAtISO: '2020-01-01T00:00:00.000Z',
        cycle: 'c',
      },
      riskPolygons: riskCollection(categories),
    };
    return { status: 'ready', artifacts, message: null };
  }),
);

describe('cycleIdentity cache replace/store behavior (Property 5)', () => {
  it('replaces the cached outlook when the newly loaded cycle identity differs', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 3650 }),
        fc.integer({ min: 0, max: 3650 }),
        stateArb,
        stateArb,
        (cachedDay, incomingDay, cachedState, incomingState) => {
          // Distinct calendar days guarantee distinct cycle identities.
          fc.pre(cachedDay !== incomingDay);

          const cachedIndex = makeIndex(isoForDay(cachedDay));
          const incomingIndex = makeIndex(isoForDay(incomingDay));

          const cachedIdentity = cycleIdentity(cachedIndex);
          const incomingIdentity = cycleIdentity(incomingIndex);

          // Precondition sanity: identities genuinely differ.
          expect(sameCycleIdentity(cachedIdentity, incomingIdentity)).toBe(false);
          expect(cycleIdentityKey(cachedIndex)).not.toBe(cycleIdentityKey(incomingIndex));

          const cached: CacheEntry = { identity: cachedIdentity, state: cachedState };
          const incoming: CacheEntry = { identity: incomingIdentity, state: incomingState };

          const result = reconcileCache(cached, incoming);

          // Requirement 4.4: the cache is replaced with the newly loaded outlook.
          expect(result.state).toBe(incomingState);
          expect(sameCycleIdentity(result.identity, incomingIdentity)).toBe(true);
          expect(result).toBe(incoming);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('stores the newly loaded outlook when no cache exists', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 3650 }),
        stateArb,
        (incomingDay, incomingState) => {
          const incomingIndex = makeIndex(isoForDay(incomingDay));
          const incomingIdentity = cycleIdentity(incomingIndex);
          const incoming: CacheEntry = { identity: incomingIdentity, state: incomingState };

          const result = reconcileCache(null, incoming);

          // Requirement 4.8: with no cache, the newly loaded outlook is stored
          // as the cached outlook for its cycle identity.
          expect(result.state).toBe(incomingState);
          expect(sameCycleIdentity(result.identity, incomingIdentity)).toBe(true);
          expect(cycleIdentityKey(incomingIndex)).toBe(
            [result.identity.outlookType, result.identity.issuingDay, result.identity.cycleTimeISO].join('|'),
          );
        },
      ),
      { numRuns: 100 },
    );
  });
});
