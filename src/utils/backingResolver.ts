import type { OutlookArtifactState } from '../hooks/useOutlookArtifacts';
import { isEmptyOutlook } from './cycleIdentity';

/**
 * SPC backing comparison resolver (Requirement 6).
 *
 * The Risk Archive lets a user compare AutoOutlook's pure model outlook
 * ("Our Model") against the SPC-blended outlook ("SPC Blend") for a single
 * archived event. Previously the archive supplied a single
 * `mergedArtifactsOverride`, which ignored the backing toggle and produced the
 * broken comparison. The fix supplies BOTH variants and selects between them by
 * the toggle:
 *
 *   effectiveMerged = spcBacked ? override.blend : override.pure
 *
 * This module holds the pure resolution logic so it can be unit- and
 * property-tested in isolation from `OutlookMapPanel`.
 */

/** The two user-selectable comparison modes for the SPC backing. */
export type BackingMode = 'ourModel' | 'spcBlend';

/**
 * The first-view default when a user first views an Archive_Event.
 * Defaults to the SPC blend ("SPC Blend").
 */
export const DEFAULT_BACKING_MODE: BackingMode = 'spcBlend';

/**
 * Both backing artifact variants for a single archived event.
 *
 * - `pure` is displayed for the "Our Model" mode (Requirement 6.2).
 * - `blend` is displayed for the "SPC Blend" mode (Requirement 6.3).
 */
export interface MergedArtifactsOverride {
  /** Pure AutoOutlook outlook — "Our Model". */
  pure: OutlookArtifactState;
  /** SPC-blended AutoOutlook outlook — "SPC Blend". */
  blend: OutlookArtifactState;
}

/**
 * A backing selection expressed either as an explicit {@link BackingMode} or as
 * the boolean `spcBacked` toggle used by `OutlookMapPanel`, where `true`
 * corresponds to the SPC blend and `false` to the pure model.
 */
export type BackingSelection = BackingMode | boolean;

/** The result of resolving a backing selection against the override variants. */
export interface BackingResolution {
  /** The normalized mode that was selected. */
  mode: BackingMode;
  /**
   * The outlook to display. When the selected mode's data is available this is
   * that mode's outlook. When it is unavailable this is the previously
   * displayed outlook, which is retained (Requirement 6.5, 6.7).
   */
  effective: OutlookArtifactState | null;
  /**
   * `true` when the selected mode's data is unavailable, so the caller can
   * surface the "unavailable comparison" message while continuing to show the
   * retained outlook.
   */
  unavailable: boolean;
}

/**
 * Normalize a {@link BackingSelection} to an explicit {@link BackingMode}.
 *
 * The boolean form mirrors the `spcBacked` toggle: `true` -> `'spcBlend'`,
 * `false` -> `'ourModel'`.
 */
export function normalizeBackingSelection(selection: BackingSelection): BackingMode {
  if (typeof selection === 'boolean') {
    return selection ? 'spcBlend' : 'ourModel';
  }
  return selection;
}

/** Convert a {@link BackingMode} to the `spcBacked` boolean toggle value. */
export function backingModeToSpcBacked(mode: BackingMode): boolean {
  return mode === 'spcBlend';
}

/**
 * Select the override variant for a mode:
 * `effectiveMerged = spcBacked ? override.blend : override.pure`.
 */
export function selectOverrideForMode(
  override: MergedArtifactsOverride,
  mode: BackingMode,
): OutlookArtifactState {
  return mode === 'spcBlend' ? override.blend : override.pure;
}

/**
 * True when a backing artifact state carries usable outlook data.
 *
 * A backing is unavailable when it is empty (no meaningful outlook data) or its
 * load resolved to a non-displayable status (`missing`, `error`, or `failed`).
 */
export function isBackingAvailable(
  state: OutlookArtifactState | null | undefined,
): boolean {
  if (!state) return false;
  if (state.status === 'missing' || state.status === 'error' || state.status === 'failed') {
    return false;
  }
  return !isEmptyOutlook(state);
}

/**
 * Resolve a backing selection to the outlook that should be displayed.
 *
 * Behavior (Requirement 6.2, 6.3, 6.5, 6.6, 6.7):
 * - The selection is normalized to a {@link BackingMode}; `'ourModel'` resolves
 *   to `override.pure` and `'spcBlend'` resolves to `override.blend`.
 * - When the selected mode's data is available, it is returned as the effective
 *   outlook and `unavailable` is `false`.
 * - When the selected mode's data is unavailable (empty, missing, error, failed,
 *   or no override supplied), the `previouslyDisplayed` outlook is retained as
 *   the effective outlook and `unavailable` is `true`.
 *
 * First-view defaulting is the caller's responsibility via
 * {@link DEFAULT_BACKING_MODE}; passing that mode with a `null`
 * `previouslyDisplayed` yields the pure outlook on first view (Requirement 6.6).
 */
export function resolveBacking(
  selection: BackingSelection,
  override: MergedArtifactsOverride | null | undefined,
  previouslyDisplayed?: OutlookArtifactState | null,
): BackingResolution {
  const mode = normalizeBackingSelection(selection);

  if (override) {
    const selected = selectOverrideForMode(override, mode);
    if (isBackingAvailable(selected)) {
      return { mode, effective: selected, unavailable: false };
    }
  }

  // Selected mode unavailable: retain the previously displayed outlook.
  return { mode, effective: previouslyDisplayed ?? null, unavailable: true };
}
