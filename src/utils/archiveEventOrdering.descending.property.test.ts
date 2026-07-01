import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { orderArchiveEvents } from './archiveEventOrdering';
import type { HistoricalEnhPlusEvent } from '../data/historicalEnhPlusVerification';

// Feature: spc-hazard-outlook-archive, Property 6: Archive events are ordered
// strictly descending by issued date. For any combined set of live and catalog
// archive events, the displayed result SHALL be sorted so each event's
// Issued_Date is greater than or equal to the next, across the entire combined
// set.
//
// Validates: Requirements 5.1, 5.2

/**
 * Build a minimal HistoricalEnhPlusEvent whose only field relevant to ordering
 * is `eventDate` (the Issued_Date). `orderArchiveEvents` reads nothing else, so
 * the remaining fields are filled with harmless placeholders and cast.
 */
function makeEvent(id: string, eventDate: unknown): HistoricalEnhPlusEvent {
  return { id, eventDate } as unknown as HistoricalEnhPlusEvent;
}

/** Parse an Issued_Date the same way the module under test does. */
function issuedAt(event: HistoricalEnhPlusEvent): number {
  return Date.parse((event.eventDate as string).trim());
}

// A generator that produces a mix of valid parseable dates (YYYY-MM-DD and full
// ISO timestamps) plus invalid/edge values, so the ordering guarantee is
// exercised across the entire realistic input space.
const validDateArb: fc.Arbitrary<string> = fc
  .date({ min: new Date('1990-01-01T00:00:00Z'), max: new Date('2040-12-31T23:59:59Z') })
  .map((d) => {
    // Roughly half plain YYYY-MM-DD, half full ISO timestamps.
    const iso = d.toISOString();
    return d.getUTCMilliseconds() % 2 === 0 ? iso.slice(0, 10) : iso;
  });

const invalidDateArb: fc.Arbitrary<unknown> = fc.oneof(
  fc.constant(undefined),
  fc.constant(null),
  fc.constant(''),
  fc.constant('   '),
  fc.constant('not-a-date'),
  fc.integer(),
);

const dateValueArb: fc.Arbitrary<unknown> = fc.oneof(
  { weight: 4, arbitrary: validDateArb },
  { weight: 1, arbitrary: invalidDateArb },
);

const eventListArb: fc.Arbitrary<HistoricalEnhPlusEvent[]> = fc
  .array(dateValueArb, { maxLength: 40 })
  .map((values) => values.map((value, i) => makeEvent(`e${i}`, value)));

describe('Property 6: archive events are ordered descending by issued date', () => {
  it('produces a result sorted non-increasing by Issued_Date across the combined set', () => {
    fc.assert(
      fc.property(eventListArb, eventListArb, (liveEvents, catalogEvents) => {
        const result = orderArchiveEvents(liveEvents, catalogEvents);

        // Every consecutive pair must be in non-increasing Issued_Date order,
        // across the entire merged live + catalog set (Requirements 5.1, 5.2).
        for (let i = 1; i < result.length; i += 1) {
          const prev = issuedAt(result[i - 1]);
          const curr = issuedAt(result[i]);
          expect(Number.isNaN(prev)).toBe(false);
          expect(Number.isNaN(curr)).toBe(false);
          expect(prev).toBeGreaterThanOrEqual(curr);
        }

        return true;
      }),
      { numRuns: 100 },
    );
  });
});
