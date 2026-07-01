import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { orderArchiveEvents } from './archiveEventOrdering';
import type { HistoricalEnhPlusEvent } from '../data/historicalEnhPlusVerification';

// Feature: spc-hazard-outlook-archive, Property 7: Archive events are
// de-duplicated by issued date with live precedence.
//
// For any combined set of live and catalog archive events, the displayed result
// SHALL contain at most one event per Issued_Date, and when a live and a catalog
// event share an Issued_Date, the retained event SHALL be the live one.
//
// Validates: Requirements 5.3, 5.4

/**
 * Distinguishing marker used only by this test to tell live-sourced events apart
 * from catalog-sourced events in assertions. `orderArchiveEvents` never inspects
 * it; it is carried alongside the real `HistoricalEnhPlusEvent` fields so the
 * retained event's origin can be checked after de-duplication.
 */
type EventSource = 'live' | 'catalog';

interface MarkedEvent extends HistoricalEnhPlusEvent {
  __source: EventSource;
  __uid: number;
}

/**
 * Build a minimal marked event. Only `eventDate` (the Issued_Date) is meaningful
 * to the module under test; every other field is filled with an inert default so
 * the object satisfies the `HistoricalEnhPlusEvent` shape.
 */
function makeEvent(uid: number, source: EventSource, eventDate: string): MarkedEvent {
  const empty = { type: 'FeatureCollection' as const, features: [] };
  return {
    id: `${source}-${uid}`,
    label: `${source} event ${uid}`,
    eventDate,
    cycleTimeISO: '2020-01-01T12:00:00.000Z',
    eventWindowStartISO: '2020-01-01T12:00:00.000Z',
    eventWindowEndISO: '2020-01-02T12:00:00.000Z',
    forecastHours: [],
    maxSpcCategory: 'ENH',
    sourceArtifactDir: '/tmp',
    summary: {},
    riskPolygons: empty as HistoricalEnhPlusEvent['riskPolygons'],
    hazardProbabilityShapes: empty as HistoricalEnhPlusEvent['hazardProbabilityShapes'],
    riskPolygonsPure: empty as HistoricalEnhPlusEvent['riskPolygonsPure'],
    hazardProbabilityShapesPure: empty as HistoricalEnhPlusEvent['hazardProbabilityShapesPure'],
    spcDay1: empty as HistoricalEnhPlusEvent['spcDay1'],
    spcHazardProbabilityShapes: empty as HistoricalEnhPlusEvent['spcHazardProbabilityShapes'],
    stormReports: [],
    __source: source,
    __uid: uid,
  };
}

/**
 * A small pool of valid ISO dates. Drawing from a constrained pool guarantees
 * frequent Issued_Date collisions between the live and catalog sets, which is
 * exactly the condition Property 7 governs.
 */
const DATE_POOL = [
  '2020-01-01',
  '2020-01-02',
  '2020-02-15',
  '2021-06-10',
  '2021-06-11',
  '2022-12-31',
  '2023-03-05',
  '2024-07-04',
];

const dateArb = fc.constantFrom(...DATE_POOL);

/** Parse a valid pool date to its comparable timestamp. */
function parsedDate(value: string): number {
  return Date.parse(value);
}

describe('orderArchiveEvents de-duplication with live precedence (Property 7)', () => {
  it('retains at most one event per Issued_Date, preferring live on collisions', () => {
    fc.assert(
      fc.property(
        fc.array(dateArb, { maxLength: 12 }),
        fc.array(dateArb, { maxLength: 12 }),
        (liveDates, catalogDates) => {
          let uid = 0;
          const liveEvents = liveDates.map((d) => makeEvent(uid++, 'live', d));
          const catalogEvents = catalogDates.map((d) => makeEvent(uid++, 'catalog', d));

          const result = orderArchiveEvents(liveEvents, catalogEvents) as MarkedEvent[];

          // Requirement 5.3: at most one event per parsed Issued_Date.
          const keys = result.map((e) => String(parsedDate(e.eventDate)));
          expect(new Set(keys).size).toBe(keys.length);

          // Requirement 5.4: whenever a date exists in the live set, the retained
          // event for that date must be the live one (live precedence on ties).
          const liveDateKeys = new Set(liveDates.map((d) => String(parsedDate(d))));
          for (const event of result) {
            const key = String(parsedDate(event.eventDate));
            if (liveDateKeys.has(key)) {
              expect(event.__source).toBe('live');
            }
          }

          // Coverage check: every retained date came from one of the inputs, and
          // the retained-date set equals the union of live and catalog dates.
          const catalogDateKeys = new Set(catalogDates.map((d) => String(parsedDate(d))));
          const expectedKeys = new Set<string>([...liveDateKeys, ...catalogDateKeys]);
          expect(new Set(keys)).toEqual(expectedKeys);
        },
      ),
      { numRuns: 100 },
    );
  });
});
