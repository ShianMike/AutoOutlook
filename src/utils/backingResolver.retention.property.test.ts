import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  resolveBacking,
  isBackingAvailable,
  normalizeBackingSelection,
  selectOverrideForMode,
  type BackingMode,
  type BackingSelection,
  type MergedArtifactsOverride,
} from './backingResolver';
import type {
  OutlookArtifacts,
  OutlookArtifactFeatureCollection,
} from '../types/outlookArtifacts';
import type { OutlookArtifactState, ArtifactStatus } from '../hooks/useOutlookArtifacts';

// Feature: spc-hazard-outlook-archive, Property 10: Unavailable backing retains
// the prior outlook.
//
// For any archive event and any backing selection whose data is unavailable,
// the resolver SHALL retain the outlook displayed before the unavailable mode
// was selected (i.e. effective === previouslyDisplayed and unavailable === true).
//
// Validates: Requirements 6.5, 6.7

/** Build a risk-polygon collection carrying at least one meaningful feature. */
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

/** A non-empty, ready outlook state carrying real risk features. */
const availableStateArb: fc.Arbitrary<OutlookArtifactState> = fc
  .array(fc.constantFrom('MRGL', 'SLGT', 'ENH', 'MDT', 'HIGH'), {
    minLength: 1,
    maxLength: 4,
  })
  .map((categories): OutlookArtifactState => {
    const artifacts: OutlookArtifacts = {
      metadata: { generatedAtISO: '2020-01-01T00:00:00.000Z', cycle: 'c' },
      riskPolygons: riskCollection(categories),
    };
    return { status: 'ready', artifacts, message: null };
  });

/**
 * An "unavailable" backing state. Covers every way `isBackingAvailable` reports
 * a mode as unavailable:
 *   - a non-displayable status (`missing`, `error`, `failed`), regardless of
 *     any artifacts it carries, and
 *   - an empty outlook (no artifacts, or artifacts with neither risk-polygon
 *     features nor probability-tile hours) under an otherwise displayable
 *     status.
 */
const unavailableStateArb: fc.Arbitrary<OutlookArtifactState> = fc.oneof(
  // Non-displayable statuses.
  fc
    .constantFrom<ArtifactStatus>('missing', 'error', 'failed')
    .map((status): OutlookArtifactState => ({ status, artifacts: null, message: null })),
  // Displayable status but empty artifacts (no meaningful data).
  fc.constantFrom<ArtifactStatus>('ready', 'loading', 'pending').map(
    (status): OutlookArtifactState => ({
      status,
      artifacts: {
        metadata: { generatedAtISO: '2020-01-01T00:00:00.000Z', cycle: 'c' },
        riskPolygons: { type: 'FeatureCollection', features: [] },
      },
      message: null,
    }),
  ),
  // No artifacts at all.
  fc
    .constantFrom<ArtifactStatus>('ready', 'loading', 'pending')
    .map((status): OutlookArtifactState => ({ status, artifacts: null, message: null })),
);

/**
 * A prior displayed outlook: either a real outlook, an unavailable-shaped
 * state, or nothing at all (first view). Whatever it is, an unavailable
 * selection must retain it unchanged.
 */
const previouslyDisplayedArb: fc.Arbitrary<OutlookArtifactState | null> = fc.oneof(
  availableStateArb,
  unavailableStateArb,
  fc.constant<OutlookArtifactState | null>(null),
);

/** A backing selection, expressed as an explicit mode or the boolean toggle. */
const selectionArb: fc.Arbitrary<BackingSelection> = fc.oneof(
  fc.constantFrom<BackingMode>('ourModel', 'spcBlend'),
  fc.boolean(),
);

/**
 * Build an override whose SELECTED mode is guaranteed unavailable. The other
 * (non-selected) variant may be anything, proving the resolver keys off the
 * selected mode only.
 */
function overrideWithUnavailableSelected(
  mode: BackingMode,
  unavailable: OutlookArtifactState,
  other: OutlookArtifactState,
): MergedArtifactsOverride {
  return mode === 'spcBlend'
    ? { pure: other, blend: unavailable }
    : { pure: unavailable, blend: other };
}

describe('resolveBacking unavailable-backing retention (Property 10)', () => {
  it('retains the previously displayed outlook when the selected mode is unavailable', () => {
    fc.assert(
      fc.property(
        selectionArb,
        unavailableStateArb,
        // Non-selected variant may be available or not — it must not be chosen.
        fc.oneof(availableStateArb, unavailableStateArb),
        previouslyDisplayedArb,
        (selection, unavailableSelected, otherVariant, previouslyDisplayed) => {
          const mode = normalizeBackingSelection(selection);
          const override = overrideWithUnavailableSelected(
            mode,
            unavailableSelected,
            otherVariant,
          );

          // Precondition sanity: the selected mode's variant is truly unavailable.
          expect(isBackingAvailable(selectOverrideForMode(override, mode))).toBe(false);

          const result = resolveBacking(selection, override, previouslyDisplayed);

          // Requirement 6.5 / 6.7: the selected mode is flagged unavailable and
          // the prior outlook is retained exactly (identity-preserving).
          expect(result.unavailable).toBe(true);
          expect(result.effective).toBe(previouslyDisplayed ?? null);
          expect(result.mode).toBe(mode);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('retains the previously displayed outlook when no override is supplied', () => {
    fc.assert(
      fc.property(
        selectionArb,
        previouslyDisplayedArb,
        fc.constantFrom<null | undefined>(null, undefined),
        (selection, previouslyDisplayed, missingOverride) => {
          const result = resolveBacking(selection, missingOverride, previouslyDisplayed);

          // With no override, the selected mode has no data: retain the prior outlook.
          expect(result.unavailable).toBe(true);
          expect(result.effective).toBe(previouslyDisplayed ?? null);
          expect(result.mode).toBe(normalizeBackingSelection(selection));
        },
      ),
      { numRuns: 100 },
    );
  });
});
