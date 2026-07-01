import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { highlightedPeriod, type TimelinePeriodBounds } from './timelineDerivation';
import type { TimelinePeriod } from './riskTimeline';

// Feature: spc-hazard-outlook-archive, Property 11: Timeline highlights exactly
// one period for the selected hour. For any set of timeline segments and any
// selected forecast hour that falls within the timeline range, exactly one
// period SHALL be highlighted, and it SHALL be the period whose hour window
// contains the selected forecast hour.
//
// Validates: Requirements 7.2, 7.6

/**
 * Generate a list of NON-OVERLAPPING, ascending timeline segments. Each segment
 * has an inclusive [startHour, endHour] window. Successive segments are laid out
 * with a random non-negative gap so that a segment's startHour is always
 * strictly greater than the previous segment's endHour, guaranteeing that at
 * most one segment can contain any given forecast hour. Each period id is unique
 * so we can verify that the correct period is highlighted.
 */
const segmentsArb: fc.Arbitrary<TimelinePeriodBounds[]> = fc
  .record({
    base: fc.integer({ min: 0, max: 48 }),
    // Each entry contributes a leading gap and a window width.
    parts: fc.array(
      fc.record({
        gap: fc.integer({ min: 0, max: 6 }),
        width: fc.integer({ min: 0, max: 8 }),
      }),
      { minLength: 0, maxLength: 8 },
    ),
  })
  .map(({ base, parts }) => {
    const segments: TimelinePeriodBounds[] = [];
    let cursor = base;
    parts.forEach((part, i) => {
      const startHour = cursor + part.gap;
      const endHour = startHour + part.width;
      segments.push({
        period: `p${i}` as TimelinePeriod,
        startHour,
        endHour,
      });
      // Next segment must start strictly after this endHour (inclusive bounds),
      // so advance the cursor past endHour to keep windows non-overlapping.
      cursor = endHour + 1;
    });
    return segments;
  });

/** Count how many segments contain the given hour within their inclusive window. */
function containingSegments(
  segments: ReadonlyArray<TimelinePeriodBounds>,
  hour: number,
): TimelinePeriodBounds[] {
  return segments.filter((s) => hour >= s.startHour && hour <= s.endHour);
}

describe('Property 11: timeline highlights exactly one period for the selected hour', () => {
  it('highlights exactly the single period whose window contains an in-range hour', () => {
    fc.assert(
      fc.property(
        segmentsArb.filter((segs) => segs.length > 0),
        fc.integer({ min: 0, max: 10_000 }),
        (segments, seed) => {
          // Pick a segment and a forecast hour guaranteed to fall within its
          // inclusive window, so the hour is within the timeline range.
          const target = segments[seed % segments.length];
          const span = target.endHour - target.startHour;
          const hour = target.startHour + (span === 0 ? 0 : seed % (span + 1));

          const matches = containingSegments(segments, hour);
          // Non-overlapping construction => at most one containing segment.
          expect(matches.length).toBe(1);

          const result = highlightedPeriod(segments, hour);
          // Exactly one period is highlighted and it is the containing period.
          expect(result).toBe(matches[0].period);
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });

  it('consistently returns the containing period or null across arbitrary hours', () => {
    fc.assert(
      fc.property(segmentsArb, fc.integer({ min: -20, max: 80 }), (segments, hour) => {
        const matches = containingSegments(segments, hour);
        const result = highlightedPeriod(segments, hour);

        if (matches.length === 0) {
          // Hour falls outside every window => nothing highlighted.
          expect(result).toBeNull();
        } else {
          // Non-overlapping windows => exactly one match, and it is highlighted.
          expect(matches.length).toBe(1);
          expect(result).toBe(matches[0].period);
        }
        return true;
      }),
      { numRuns: 100 },
    );
  });

  it('never highlights a period for an absent or non-finite selected hour', () => {
    const nonHourArb = fc.constantFrom<number | null | undefined>(
      null,
      undefined,
      Number.NaN,
      Number.POSITIVE_INFINITY,
      Number.NEGATIVE_INFINITY,
    );
    fc.assert(
      fc.property(segmentsArb, nonHourArb, (segments, hour) => {
        expect(highlightedPeriod(segments, hour)).toBeNull();
        return true;
      }),
      { numRuns: 100 },
    );
  });
});
