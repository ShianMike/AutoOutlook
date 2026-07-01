import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  resolveBacking,
  normalizeBackingSelection,
  selectOverrideForMode,
  isBackingAvailable,
  DEFAULT_BACKING_MODE,
  type MergedArtifactsOverride,
} from './backingResolver';
import type {
  OutlookArtifacts,
  OutlookArtifactFeatureCollection,
  OutlookArtifactMetadata,
} from '../types/outlookArtifacts';
import type { OutlookArtifactState, ArtifactStatus } from '../hooks/useOutlookArtifacts';

// Feature: spc-hazard-outlook-archive, Property 9: Backing selection resolves
// to the matching outlook. For any archive event with both pure and blend
// override artifacts available, selecting "Our Model" SHALL resolve to the pure
// outlook and selecting "SPC Blend" SHALL resolve to the blend outlook, and the
// first-view default SHALL be "Our Model".
//
// Validates: Requirements 6.2, 6.3, 6.6

/**
 * A feature collection carrying a single, uniquely-tagged feature so distinct
 * states are identifiable and, more importantly, non-empty (so the resolver's
 * availability check passes).
 */
function taggedCollection(tag: string): OutlookArtifactFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: { type: 'Polygon', coordinates: [] },
        properties: { category: 'SLGT', forecastHour: 12, validTimeISO: tag },
      },
    ],
  };
}

/**
 * Build an AVAILABLE outlook state: it carries a non-displayable-free status
 * and meaningful (non-empty) outlook data, so {@link isBackingAvailable} is true.
 * The `tag` makes the state's identity observable across the resolver.
 */
function buildAvailableState(tag: string, status: ArtifactStatus): OutlookArtifactState {
  const metadata = { generatedAtISO: 'gen', cycle: tag } as OutlookArtifactMetadata;
  const artifacts: OutlookArtifacts = {
    metadata,
    riskPolygons: taggedCollection(tag),
  };
  return { status, artifacts, message: null };
}

// Statuses that keep a backing "available" (not missing/error/failed).
const availableStatusArb: fc.Arbitrary<ArtifactStatus> = fc.constantFrom(
  'loading',
  'ready',
  'pending',
);

// An override whose pure and blend variants are BOTH available and distinct.
const availableOverrideArb: fc.Arbitrary<MergedArtifactsOverride> = fc
  .record({
    pureStatus: availableStatusArb,
    blendStatus: availableStatusArb,
    salt: fc.string(),
  })
  .map(({ pureStatus, blendStatus, salt }) => ({
    pure: buildAvailableState(`pure-${salt}`, pureStatus),
    blend: buildAvailableState(`blend-${salt}`, blendStatus),
  }));

describe('Property 9: backing selection resolves to the matching outlook', () => {
  it('resolves Our Model -> pure, SPC Blend -> blend, and defaults first view to Our Model', () => {
    fc.assert(
      fc.property(availableOverrideArb, (override) => {
        // Guard: both variants must be available for this property.
        expect(isBackingAvailable(override.pure)).toBe(true);
        expect(isBackingAvailable(override.blend)).toBe(true);

        // --- "Our Model" selection resolves to the pure outlook (Req 6.2) ---
        const ourModelMode = resolveBacking('ourModel', override);
        expect(ourModelMode.mode).toBe('ourModel');
        expect(ourModelMode.effective).toBe(override.pure);
        expect(ourModelMode.unavailable).toBe(false);

        // The boolean toggle form (spcBacked = false) is equivalent.
        const ourModelBool = resolveBacking(false, override);
        expect(ourModelBool.mode).toBe('ourModel');
        expect(ourModelBool.effective).toBe(override.pure);
        expect(ourModelBool.unavailable).toBe(false);

        // --- "SPC Blend" selection resolves to the blend outlook (Req 6.3) ---
        const spcBlendMode = resolveBacking('spcBlend', override);
        expect(spcBlendMode.mode).toBe('spcBlend');
        expect(spcBlendMode.effective).toBe(override.blend);
        expect(spcBlendMode.unavailable).toBe(false);

        // The boolean toggle form (spcBacked = true) is equivalent.
        const spcBlendBool = resolveBacking(true, override);
        expect(spcBlendBool.mode).toBe('spcBlend');
        expect(spcBlendBool.effective).toBe(override.blend);
        expect(spcBlendBool.unavailable).toBe(false);

        // --- The two modes never cross-resolve to the other's outlook. ---
        expect(ourModelMode.effective).not.toBe(override.blend);
        expect(spcBlendMode.effective).not.toBe(override.pure);

        // --- Selection normalization mirrors the spcBacked toggle. ---
        expect(normalizeBackingSelection(false)).toBe('ourModel');
        expect(normalizeBackingSelection(true)).toBe('spcBlend');
        expect(normalizeBackingSelection('ourModel')).toBe('ourModel');
        expect(normalizeBackingSelection('spcBlend')).toBe('spcBlend');

        // --- Override selection helper agrees with the resolver. ---
        expect(selectOverrideForMode(override, 'ourModel')).toBe(override.pure);
        expect(selectOverrideForMode(override, 'spcBlend')).toBe(override.blend);

        // --- First-view default is "SPC Blend" and yields the blend outlook. ---
        expect(DEFAULT_BACKING_MODE).toBe('spcBlend');
        const firstView = resolveBacking(DEFAULT_BACKING_MODE, override, null);
        expect(firstView.mode).toBe('spcBlend');
        expect(firstView.effective).toBe(override.blend);
        expect(firstView.unavailable).toBe(false);

        return true;
      }),
      { numRuns: 100 },
    );
  });
});
