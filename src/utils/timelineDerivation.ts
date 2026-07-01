// timelineDerivation: pure helpers that keep the Risk Timeline synchronized
// with the currently displayed merged outlook.
//
// These functions are intentionally free of React/DOM/state so they can be
// exercised directly by property-based tests. They cover:
//   - derivePeriodCategories: map displayed merged artifact hours to a risk
//     category per Timeline_Period, isolating an unresolvable period into a
//     distinct "no data" state while every other period keeps its category.
//   - highlightedPeriod: resolve the single Timeline_Period that contains the
//     currently Selected_Forecast_Hour.
//   - selectPeriod: a deterministic reducer that moves the selection to a
//     period's representative hour and resulting category in one step, so no
//     transient/intermediate category is ever emitted.
//
// Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6

import type { RiskCategory } from '../types/forecast';
import { RISK_META } from '../types/forecast';
import type { ArtifactRiskCategory, OutlookTimelineHourSummary } from '../types/outlookArtifacts';
import { HRRR_PERIOD_WINDOWS, type TimelinePeriod, type TimelinePeriodWindow } from './riskTimeline';

/**
 * Distinct sentinel representing a Timeline_Period whose displayed merged
 * outlook data cannot be resolved to a valid risk category (Requirement 7.3).
 * It is intentionally not a member of {@link RiskCategory}.
 */
export const NO_DATA = 'NO_DATA' as const;
export type NoData = typeof NO_DATA;

/** A resolved period category is either a real risk category or the no-data sentinel. */
export type TimelinePeriodCategory = RiskCategory | NoData;

/** Per-period risk category derived from the currently displayed merged outlook. */
export interface DerivedTimelinePeriod {
  period: TimelinePeriod;
  label: string;
  startHour: number;
  endHour: number;
  /** Forecast hour used when the period is clicked / selected. */
  representativeHour: number;
  /** Resolved risk category, or {@link NO_DATA} when unresolvable. */
  category: TimelinePeriodCategory;
  /** True when at least one displayed hour resolved to a valid category. */
  hasData: boolean;
}

/** Minimal shape required to locate the highlighted period. */
export interface TimelinePeriodBounds {
  period: TimelinePeriod;
  startHour: number;
  endHour: number;
}

/** Selection state tracked by the Risk Timeline. */
export interface TimelineSelectionState {
  /** The Selected_Forecast_Hour, or null when nothing is selected yet. */
  selectedForecastHour: number | null;
  /** The category currently displayed for the selection. */
  displayedCategory: TimelinePeriodCategory | null;
  /** The category displayed immediately before the most recent selection. */
  previousCategory: TimelinePeriodCategory | null;
}

/** A period a user can click, carrying its representative hour and resolved category. */
export interface SelectablePeriod {
  period: TimelinePeriod;
  representativeHour: number;
  category: TimelinePeriodCategory;
}

/**
 * Normalize an artifact risk category into the display {@link RiskCategory}
 * space, mirroring the mapping used by `buildRiskTimeline`. Returns undefined
 * for a missing or unrecognized category so the caller can treat the hour as
 * unresolved.
 */
export function normalizeArtifactCategory(
  category: ArtifactRiskCategory | undefined | null,
): RiskCategory | undefined {
  if (!category) return undefined;
  if (category === 'NONE') return 'TSTM';
  if (category === 'MDT') return 'MOD';
  if (category in RISK_META) return category as RiskCategory;
  return undefined;
}

function isResolvedHour(hour: OutlookTimelineHourSummary): boolean {
  return normalizeArtifactCategory(hour.category) !== undefined;
}

/**
 * Derive a risk category for every Timeline_Period from the displayed merged
 * artifact hours. Each period is resolved independently: a period with no
 * resolvable hour becomes {@link NO_DATA} while all other periods retain their
 * resolved categories (Requirements 7.1, 7.3).
 */
export function derivePeriodCategories(
  artifactHours: ReadonlyArray<OutlookTimelineHourSummary> = [],
  windows: ReadonlyArray<TimelinePeriodWindow> = HRRR_PERIOD_WINDOWS,
): DerivedTimelinePeriod[] {
  return windows.map((window) => {
    const inWindow = artifactHours.filter(
      (hour) => hour.forecastHour >= window.minHour && hour.forecastHour <= window.maxHour,
    );
    const resolved = inWindow.filter(isResolvedHour);

    if (resolved.length === 0) {
      return {
        period: window.period,
        label: window.label,
        startHour: window.minHour,
        endHour: window.maxHour,
        representativeHour: window.minHour,
        category: NO_DATA,
        hasData: false,
      };
    }

    const peak = peakHour(resolved);
    return {
      period: window.period,
      label: window.label,
      startHour: window.minHour,
      endHour: window.maxHour,
      representativeHour: peak.forecastHour,
      category: normalizeArtifactCategory(peak.category) as RiskCategory,
      hasData: true,
    };
  });
}

/**
 * Pick the representative hour for a resolved period: highest risk category,
 * breaking ties by higher coverage and then by the earliest forecast hour so
 * the result is fully deterministic.
 */
function peakHour(hours: ReadonlyArray<OutlookTimelineHourSummary>): OutlookTimelineHourSummary {
  return hours.reduce((best, current) => {
    const currentOrd = RISK_META[normalizeArtifactCategory(current.category) as RiskCategory].ord;
    const bestOrd = RISK_META[normalizeArtifactCategory(best.category) as RiskCategory].ord;
    if (currentOrd !== bestOrd) return currentOrd > bestOrd ? current : best;

    const currentCoverage = Number.isFinite(current.coverage) ? current.coverage : 0;
    const bestCoverage = Number.isFinite(best.coverage) ? best.coverage : 0;
    if (currentCoverage !== bestCoverage) return currentCoverage > bestCoverage ? current : best;

    return current.forecastHour < best.forecastHour ? current : best;
  });
}

/**
 * Resolve the single Timeline_Period that contains the Selected_Forecast_Hour.
 * Returns exactly one period id (the first period whose window contains the
 * hour) or null when the hour falls outside every window / is undefined
 * (Requirements 7.2, 7.6).
 */
export function highlightedPeriod(
  segments: ReadonlyArray<TimelinePeriodBounds>,
  selectedForecastHour: number | null | undefined,
): TimelinePeriod | null {
  if (selectedForecastHour == null || !Number.isFinite(selectedForecastHour)) {
    return null;
  }
  const match = segments.find(
    (segment) =>
      selectedForecastHour >= segment.startHour && selectedForecastHour <= segment.endHour,
  );
  return match ? match.period : null;
}

/**
 * Deterministic selection reducer. Produces the next selection state in a
 * single step: the Selected_Forecast_Hour becomes the period's representative
 * hour and the displayed category becomes the period's resolved category.
 * Because the transition is atomic, no intermediate category distinct from the
 * previous and resulting categories can ever be emitted (Requirements 7.4, 7.5).
 */
export function selectPeriod(
  state: TimelineSelectionState,
  period: SelectablePeriod,
): TimelineSelectionState {
  return {
    selectedForecastHour: period.representativeHour,
    displayedCategory: period.category,
    previousCategory: state.displayedCategory,
  };
}

/** Convenience: the initial (empty) timeline selection state. */
export function initialSelectionState(): TimelineSelectionState {
  return {
    selectedForecastHour: null,
    displayedCategory: null,
    previousCategory: null,
  };
}
