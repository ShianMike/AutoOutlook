// ---------------------------------------------------------------------------
// useReducedMotion
// ---------------------------------------------------------------------------
//
// Centralizes reduced-motion detection so both the reveal system and the
// decorative-animation gating react to runtime OS/browser changes rather than
// reading the preference only once at mount.
//
//   - Reads `window.matchMedia('(prefers-reduced-motion: reduce)')`.
//   - Subscribes to its `change` event and cleans up the listener on unmount.
//   - SSR-safe: returns `false` when `window` is undefined.
//
// Requirements: 4.1, 4.6

import { useEffect, useState } from 'react';

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

/** Reads the current reduced-motion preference. Safe when `window` is absent. */
function getReducedMotionPreference(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

/**
 * Returns the live reduced-motion preference, updating whenever the OS/browser
 * preference changes. Returns `false` during SSR (no `window`).
 */
export function useReducedMotion(): boolean {
  const [reducedMotion, setReducedMotion] = useState<boolean>(getReducedMotionPreference);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }

    const mediaQuery = window.matchMedia(REDUCED_MOTION_QUERY);

    // Re-sync in case the preference changed between the initial render and the
    // effect running (e.g. hydration), then listen for subsequent changes.
    setReducedMotion(mediaQuery.matches);

    const handleChange = (event: MediaQueryListEvent) => {
      setReducedMotion(event.matches);
    };

    mediaQuery.addEventListener('change', handleChange);

    return () => {
      mediaQuery.removeEventListener('change', handleChange);
    };
  }, []);

  return reducedMotion;
}

export default useReducedMotion;
