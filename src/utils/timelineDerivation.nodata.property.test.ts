import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  derivePeriodCategories,
  NO_DATA,
  normalizeArtifactCategory,
} from './timelineDerivation';
import { HRRR_PERIOD_WINDOWS } from './riskTimeline';
import { RISK_META } from '../types/forecast';
import type { ArtifactRiskCategory, OutlookTimelineHourSummary } from '../types/outlookArtifacts';

// Feature: spc-hazard-outlook-archive, Property 13: Unresolvable timeline
// periods isolate the "no data" state. For any merged outlook data where one
// period cannot be resolved to a risk category, that period SHALL render a
// "no data" state distinct from all valid risk categories while every other
// period retains its resolved category.
//
// Validates: Requirements 7.3

/** Artifact categories that normalize to a valid RiskCategory. */
const RESOLVABLE_CATEGORIES: ArtifactRiskCategory[] = [
  'NONE',
  'TSTM',
  'MRGL',
  'SLGT',
  'ENH',
  'MDT',
  'MOD',
  'HIGH',
];

/** Values that normalizeArtifactCategory cannot resolve to a RiskCategory. */
const UNRESOLVABLE_CATEGORIES = ['', 'UNKNOWN', 'ZZZ', 'n/a', 'severe'] as unknown as ArtifactRiskCategory[];

/** The set of valid display risk categories the "no data" state must differ from. */
const VALID_RISK_CATEGORIES = new Set(Object.keys(RISK_META));

interface WindowPlan {
  /** 'resolved' -> at least one hour resolves; otherwise the period is unresolvable. */
  mode: 'resolved' | 'empty' | 'invalid';
  hours: Array<{ forecastHour: number; category: ArtifactRiskCategory; coverage: number }>;
}

function makeHour(
  forecastHour: number,
  category: ArtifactRiskCategory,
  coverage: number,
): OutlookTimelineHourSummary {
  return {
    forecastHour,
    category,
    peakHazardProbability: 0,
    significantSevere: false,
    coverage,
  };
}

/** Build a plan for a single timeline window. */
function windowPlanArb(minHour: number, maxHour: number): fc.Arbitrary<WindowPlan> {
  const fhArb = fc.integer({ min: minHour, max: maxHour });
  const coverageArb = fc.double({ min: 0, max: 1, noNaN: true });

  const resolvedHours = fc.array(
    fc.record({
      forecastHour: fhArb,
      category: fc.constantFrom(...RESOLVABLE_CATEGORIES),
      coverage: coverageArb,
    }),
    { minLength: 1, maxLength: 4 },
  );

  const invalidHours = fc.array(
    fc.record({
      forecastHour: fhArb,
      category: fc.constantFrom(...UNRESOLVABLE_CATEGORIES),
      coverage: coverageArb,
    }),
    { minLength: 1, maxLength: 4 },
  );

  return fc.oneof(
    resolvedHours.map((hours) => ({ mode: 'resolved' as const, hours })),
    invalidHours.map((hours) => ({ mode: 'invalid' as const, hours })),
    fc.constant({ mode: 'empty' as const, hours: [] }),
  );
}

// One plan per HRRR window, guaranteeing at least one unresolvable period so the
// isolation guarantee is always exercised.
const plansArb: fc.Arbitrary<WindowPlan[]> = fc
  .tuple(...HRRR_PERIOD_WINDOWS.map((w) => windowPlanArb(w.minHour, w.maxHour)))
  .chain((plans) => {
    const list = plans as WindowPlan[];
    const hasUnresolved = list.some((p) => p.mode !== 'resolved');
    if (hasUnresolved) {
      return fc.constant(list);
    }
    // Force at least one period to be unresolvable by emptying a chosen index.
    return fc.integer({ min: 0, max: list.length - 1 }).map((idx) => {
      const forced = list.slice();
      forced[idx] = { mode: 'empty', hours: [] };
      return forced;
    });
  });

describe('Property 13: unresolvable timeline periods isolate the "no data" state', () => {
  it('renders NO_DATA (distinct from every risk category) only for unresolvable periods while other periods keep their resolved category', () => {
    // Sanity: the sentinel is distinct from all valid risk categories.
    expect(VALID_RISK_CATEGORIES.has(NO_DATA as unknown as string)).toBe(false);

    fc.assert(
      fc.property(plansArb, (plans) => {
        const artifactHours: OutlookTimelineHourSummary[] = plans.flatMap((plan) =>
          plan.hours.map((h) => makeHour(h.forecastHour, h.category, h.coverage)),
        );

        const derived = derivePeriodCategories(artifactHours);

        // One derived period per window, in order.
        expect(derived.length).toBe(HRRR_PERIOD_WINDOWS.length);

        // A window resolves iff at least one of its hours normalizes to a category.
        const expectResolved = plans.map((plan) =>
          plan.hours.some((h) => normalizeArtifactCategory(h.category) !== undefined),
        );

        // At least one period is genuinely unresolvable.
        expect(expectResolved.some((r) => !r)).toBe(true);

        derived.forEach((period, i) => {
          if (expectResolved[i]) {
            // Resolved period retains a real risk category, never the no-data state.
            expect(period.category).not.toBe(NO_DATA);
            expect(VALID_RISK_CATEGORIES.has(period.category as string)).toBe(true);
            expect(period.hasData).toBe(true);
          } else {
            // Unresolvable period isolates the distinct "no data" state.
            expect(period.category).toBe(NO_DATA);
            expect(VALID_RISK_CATEGORIES.has(period.category as string)).toBe(false);
            expect(period.hasData).toBe(false);
          }
        });

        return true;
      }),
      { numRuns: 100 },
    );
  });
});
