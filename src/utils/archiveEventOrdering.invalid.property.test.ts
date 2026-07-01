import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { orderArchiveEvents } from './archiveEventOrdering';
import type { HistoricalEnhPlusEvent } from '../data/historicalEnhPlusVerification';

// Feature: spc-hazard-outlook-archive, Property 8: Invalid issued dates are
// excluded without dropping valid events. For any combined set of archive
// events, every event with a missing, null, or unparseable Issued_Date SHALL be
// excluded from the result, and every event with a valid Issued_Date SHALL be
// retained (subject to de-duplication) in descending order.
//
// Validates: Requirements 5.5

/**
 * Build a minimal HistoricalEnhPlusEvent. `orderArchiveEvents` only reads
 * `eventDate` (Issued_Date), so the remaining fields are filled with inert
 * placeholders and the object is cast to the full event shape.
 */
function makeEvent(id: string, eventDate: unknown): HistoricalEnhPlusEvent {
  return { id, eventDate } as unknown as HistoricalEnhPlusEvent;
}

/**
 * Oracle mirror of the module's parse rule: a valid Issued_Date is a non-empty
 * (after trimming) string that `Date.parse` accepts; anything else is invalid.
 */
function parseIssued(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (trimmed === '') return null;
  const parsed = Date.parse(trimmed);
  return Number.isNaN(parsed) ? null : parsed;
}

// Generators for values that MUST be treated as invalid Issued_Dates.
const invalidDateArb: fc.Arbitrary<unknown> = fc.oneof(
  fc.constant(undefined),
  fc.constant(null),
  fc.constant(''),
  fc.constant('   '),
  fc.constant('not-a-date'),
  fc.constant('2026-13-45'),
  fc.constant('garbage'),
  fc.integer(),
  fc.boolean(),
);

// Generator for values that MUST be treated as valid Issued_Dates: YYYY-MM-DD
// strings drawn from a bounded range.
const validDateArb: fc.Arbitrary<string> = fc
  .date({ min: new Date('2000-01-01T00:00:00Z'), max: new Date('2035-12-31T00:00:00Z') })
  .map((d) => d.toISOString().slice(0, 10));

// An event spec that is either valid or invalid, tagged so the test can assert.
const eventSpecArb = fc.oneof(
  fc.record({ valid: fc.constant(true), value: validDateArb as fc.Arbitrary<unknown> }),
  fc.record({ valid: fc.constant(false), value: invalidDateArb }),
);

describe('Property 8: invalid issued dates are excluded without dropping valid events', () => {
  it('excludes every invalid-date event and retains every valid-date event in descending order', () => {
    fc.assert(
      fc.property(
        fc.array(eventSpecArb, { maxLength: 40 }),
        fc.array(eventSpecArb, { maxLength: 40 }),
        (liveSpecs, catalogSpecs) => {
          const liveEvents = liveSpecs.map((spec, i) => makeEvent(`live-${i}`, spec.value));
          const catalogEvents = catalogSpecs.map((spec, i) => makeEvent(`cat-${i}`, spec.value));

          const result = orderArchiveEvents(liveEvents, catalogEvents);

          // Every event in the result has a parseable Issued_Date: no invalid
          // event survives.
          for (const event of result) {
            expect(parseIssued(event.eventDate)).not.toBeNull();
          }

          // The set of distinct valid Issued_Date values present in the input
          // must equal the set of Issued_Date values in the result. This proves
          // no valid date was dropped (beyond de-duplication) and no invalid
          // date leaked through.
          const expectedValidTimes = new Set<number>();
          for (const spec of [...liveSpecs, ...catalogSpecs]) {
            const t = parseIssued(spec.value);
            if (t !== null) expectedValidTimes.add(t);
          }
          const resultTimes = result.map((e) => parseIssued(e.eventDate) as number);
          expect(new Set(resultTimes)).toEqual(expectedValidTimes);

          // De-duplication: at most one event per Issued_Date.
          expect(resultTimes.length).toBe(new Set(resultTimes).size);

          // Descending order by Issued_Date.
          for (let i = 1; i < resultTimes.length; i += 1) {
            expect(resultTimes[i - 1]).toBeGreaterThanOrEqual(resultTimes[i]);
          }

          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});
