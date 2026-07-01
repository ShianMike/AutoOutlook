import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  cycleIdentity,
  sameCycleIdentity,
  guardReplacement,
  isEmptyOutlook,
} from './cycleIdentity';
import type {
  OutlookArtifacts,
  OutlookArtifactFeatureCollection,
  OutlookArtifactMetadata,
  OutlookIncrementalIndex,
  OutlookProbabilityHour,
} from '../types/outlookArtifacts';
import type { OutlookArtifactState } from '../hooks/useOutlookArtifacts';

// Feature: spc-hazard-outlook-archive, Property 4: Cache reuse preserves
// matching-cycle outlooks. For any cached outlook and any newly loaded outlook
// that shares the same cycle identity (issuing day, cycle timestamp, outlook
// type), the cache SHALL retain the cached outlook and SHALL reject a
// replacement that is empty or missing fields present in the retained outlook.
//
// Validates: Requirements 4.1, 4.2

/** The significant artifact fields whose presence guards a replacement. */
interface Presence {
  riskPolygons: boolean;
  aggregateRiskPolygons: boolean;
  probabilityTiles: boolean;
  timelineSummary: boolean;
  incrementalIndex: boolean;
}

const PRESENCE_KEYS: Array<keyof Presence> = [
  'riskPolygons',
  'aggregateRiskPolygons',
  'probabilityTiles',
  'timelineSummary',
  'incrementalIndex',
];

function featureCollection(hasFeatures: boolean): OutlookArtifactFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: hasFeatures
      ? [
          {
            type: 'Feature',
            geometry: { type: 'Polygon', coordinates: [] },
            properties: { category: 'SLGT', forecastHour: 12, validTimeISO: '2026-06-10T18:00:00Z' },
          },
        ]
      : [],
  };
}

function makeIndex(cycle: string, cycleTimeISO: string, generatedAtISO: string): OutlookIncrementalIndex {
  return {
    generatedAtISO,
    cycle,
    cycleTimeISO,
    mode: 'incremental',
    requestedForecastHours: [],
    readyForecastHours: [],
    failedForecastHours: [],
    pendingForecastHours: [],
    status: 'complete',
  } as OutlookIncrementalIndex;
}

/**
 * Build an outlook state whose {@link fieldPresence} matches `presence` exactly.
 * When `hasArtifacts` is false the state carries no artifacts (fully empty).
 */
function buildState(hasArtifacts: boolean, presence: Presence): OutlookArtifactState {
  if (!hasArtifacts) {
    return { status: 'ready', artifacts: null, message: null };
  }
  const metadata = { generatedAtISO: 'gen', cycle: 'cycle' } as OutlookArtifactMetadata;
  const artifacts: OutlookArtifacts = {
    metadata,
    riskPolygons: featureCollection(presence.riskPolygons),
  };
  if (presence.aggregateRiskPolygons) {
    artifacts.aggregateRiskPolygons = featureCollection(true);
  }
  if (presence.probabilityTiles) {
    artifacts.probabilityTiles = { cycle: 'cycle', hours: [{} as OutlookProbabilityHour] };
  }
  if (presence.timelineSummary) {
    artifacts.timelineSummary = { hours: [] };
  }
  if (presence.incrementalIndex) {
    artifacts.incrementalIndex = makeIndex('cycle', '2026-06-10T12:00:00Z', 'gen');
  }
  return { status: 'ready', artifacts, message: null };
}

/** Effective presence a state resolves to (null artifacts => all absent). */
function effectivePresence(hasArtifacts: boolean, presence: Presence): Presence {
  if (!hasArtifacts) {
    return {
      riskPolygons: false,
      aggregateRiskPolygons: false,
      probabilityTiles: false,
      timelineSummary: false,
      incrementalIndex: false,
    };
  }
  return presence;
}

/** Oracle for isEmptyOutlook: empty when no risk polygons and no prob tiles. */
function oracleEmpty(eff: Presence): boolean {
  return !eff.riskPolygons && !eff.probabilityTiles;
}

/** Oracle for guardReplacement derived directly from the property statement. */
function oracleGuard(prevEff: Presence, nextEff: Presence): boolean {
  if (oracleEmpty(nextEff)) return false; // reject empty replacement
  if (oracleEmpty(prevEff)) return true; // seed an absent cache
  for (const key of PRESENCE_KEYS) {
    if (prevEff[key] && !nextEff[key]) return false; // reject lossy replacement
  }
  return true;
}

const presenceArb: fc.Arbitrary<Presence> = fc.record({
  riskPolygons: fc.boolean(),
  aggregateRiskPolygons: fc.boolean(),
  probabilityTiles: fc.boolean(),
  timelineSummary: fc.boolean(),
  incrementalIndex: fc.boolean(),
});

const stateSpecArb = fc.record({
  hasArtifacts: fc.boolean(),
  presence: presenceArb,
});

describe('Property 4: cache reuse preserves matching-cycle outlooks', () => {
  it('reuses the cached outlook and rejects empty/lossy replacements on a matching cycle', () => {
    fc.assert(
      fc.property(
        // Durable cycle fields shared by the cached load and the refreshed load.
        fc.date({ min: new Date('2000-01-01T00:00:00Z'), max: new Date('2035-12-31T23:00:00Z') }),
        fc.string(),
        // Volatile field that must NOT affect identity across a refresh.
        fc.string(),
        fc.string(),
        stateSpecArb,
        stateSpecArb,
        (cycleDate, cycleFallback, genA, genB, prevSpec, nextSpec) => {
          const cycleTimeISO = cycleDate.toISOString();

          // Two loads of the same cycle (e.g. before and after a page refresh)
          // differ only in the volatile generatedAtISO field.
          const cachedIndex = makeIndex(cycleFallback, cycleTimeISO, genA);
          const reloadedIndex = makeIndex(cycleFallback, cycleTimeISO, genB);

          // The cycle identity is stable across the refresh.
          expect(sameCycleIdentity(cycleIdentity(cachedIndex), cycleIdentity(reloadedIndex))).toBe(true);

          const prev = buildState(prevSpec.hasArtifacts, prevSpec.presence);
          const next = buildState(nextSpec.hasArtifacts, nextSpec.presence);
          const prevEff = effectivePresence(prevSpec.hasArtifacts, prevSpec.presence);
          const nextEff = effectivePresence(nextSpec.hasArtifacts, nextSpec.presence);

          // isEmptyOutlook agrees with the oracle.
          expect(isEmptyOutlook(prev)).toBe(oracleEmpty(prevEff));
          expect(isEmptyOutlook(next)).toBe(oracleEmpty(nextEff));

          const allowed = guardReplacement(prev, next);

          // The guard decision matches the property statement exactly.
          expect(allowed).toBe(oracleGuard(prevEff, nextEff));

          // An empty replacement is always rejected (cache retains the cached outlook).
          if (isEmptyOutlook(next)) {
            expect(allowed).toBe(false);
          }

          // A replacement missing a field present in a non-empty retained cache
          // is always rejected.
          if (!isEmptyOutlook(prev)) {
            const lossy = PRESENCE_KEYS.some((key) => prevEff[key] && !nextEff[key]);
            if (lossy) {
              expect(allowed).toBe(false);
            }
          }

          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});
