import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  selectPeriod,
  initialSelectionState,
  NO_DATA,
  type TimelineSelectionState,
  type SelectablePeriod,
  type TimelinePeriodCategory,
} from './timelineDerivation';
import type { RiskCategory } from '../types/forecast';
import type { TimelinePeriod } from './riskTimeline';

// Feature: spc-hazard-outlook-archive, Property 12: Period selection is
// single-valued and transition-clean. For any Timeline_Period click, the
// resulting selection SHALL set Selected_Forecast_Hour to that period's
// representative hour exactly once, and the displayed category SHALL move
// directly from the previous category to the resulting category with no
// intermediate category different from both.
//
// Validates: Requirements 7.4, 7.5

const RISK_CATEGORIES: RiskCategory[] = ['TSTM', 'MRGL', 'SLGT', 'ENH', 'MOD', 'HIGH'];

const TIMELINE_PERIODS: TimelinePeriod[] = [
  'f000_f006',
  'f007_f012',
  'f013_f018',
  'f019_f024',
  'f025_f036',
  'f037_f048',
];

/** A resolvable/derived category: a real risk category or the "no data" sentinel. */
const periodCategoryArb: fc.Arbitrary<TimelinePeriodCategory> = fc.constantFrom(
  ...RISK_CATEGORIES,
  NO_DATA,
);

/**
 * A prior selection state: selected hour, displayed and previous categories may
 * each be null (never selected yet) or any resolvable category.
 */
const stateArb: fc.Arbitrary<TimelineSelectionState> = fc.record({
  selectedForecastHour: fc.option(fc.integer({ min: 0, max: 48 }), { nil: null }),
  displayedCategory: fc.option(periodCategoryArb, { nil: null }),
  previousCategory: fc.option(periodCategoryArb, { nil: null }),
});

/** A period the user can click, carrying its representative hour and resolved category. */
const periodArb: fc.Arbitrary<SelectablePeriod> = fc.record({
  period: fc.constantFrom(...TIMELINE_PERIODS),
  representativeHour: fc.integer({ min: 0, max: 48 }),
  category: periodCategoryArb,
});

describe('Property 12: period selection is single-valued and transition-clean', () => {
  it('sets the representative hour once and transitions directly prev -> resulting', () => {
    fc.assert(
      fc.property(stateArb, periodArb, (state, period) => {
        const result = selectPeriod(state, period);

        // --- Selected_Forecast_Hour is set to the period's representative hour (Req 7.4). ---
        expect(result.selectedForecastHour).toBe(period.representativeHour);

        // --- Displayed category is exactly the resulting (period) category (Req 7.5). ---
        expect(result.displayedCategory).toBe(period.category);

        // --- Previous category is exactly the prior displayed category, so the
        // transition is a direct prev -> resulting move with no third value (Req 7.5). ---
        expect(result.previousCategory).toBe(state.displayedCategory);

        // --- Transition-clean: the only categories present in the result are the
        // previous and the resulting categories; no intermediate distinct from both. ---
        const emitted: TimelinePeriodCategory[] = [];
        if (result.previousCategory !== null) emitted.push(result.previousCategory);
        if (result.displayedCategory !== null) emitted.push(result.displayedCategory);
        const allowed = new Set<TimelinePeriodCategory | null>([
          state.displayedCategory,
          period.category,
        ]);
        for (const cat of emitted) {
          expect(allowed.has(cat)).toBe(true);
        }

        // --- Determinism: recomputing from the same inputs yields the same state. ---
        const again = selectPeriod(state, period);
        expect(again).toEqual(result);

        return true;
      }),
      { numRuns: 100 },
    );
  });

  it('is idempotent in displayed category when the same period is re-selected', () => {
    fc.assert(
      fc.property(stateArb, periodArb, (state, period) => {
        const first = selectPeriod(state, period);
        const second = selectPeriod(first, period);

        // Re-clicking the same period keeps the same hour and displayed category...
        expect(second.selectedForecastHour).toBe(period.representativeHour);
        expect(second.displayedCategory).toBe(period.category);
        // ...and the previous category is now the (identical) resulting category,
        // so no category distinct from the resulting one is ever displayed.
        expect(second.previousCategory).toBe(period.category);

        return true;
      }),
      { numRuns: 100 },
    );
  });

  it('sets the hour exactly once from the initial (empty) state', () => {
    fc.assert(
      fc.property(periodArb, (period) => {
        const result = selectPeriod(initialSelectionState(), period);
        expect(result.selectedForecastHour).toBe(period.representativeHour);
        expect(result.displayedCategory).toBe(period.category);
        expect(result.previousCategory).toBeNull();
        return true;
      }),
      { numRuns: 100 },
    );
  });
});
