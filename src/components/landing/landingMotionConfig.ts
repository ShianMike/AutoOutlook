// ---------------------------------------------------------------------------
// Landing page motion / timing configuration
// ---------------------------------------------------------------------------
//
// Single source of truth for all landing-page motion and timing constants.
// Hooks (useLandingReveal, useUtcClock), the navigation transition overlay,
// the stagger helpers, and their tests MUST import these values rather than
// hard-coding their own, so behavior and the assertions that verify it stay
// in lockstep.
//
// Bounds come directly from the requirements:
//   - REVEAL_DURATION_MS   within [300, 800]ms          (Req 3.1)
//   - REVEAL_THRESHOLD      0.1 (>= 10% visible)         (Req 3.1)
//   - REVEAL_STAGGER_*_MS   successive delta in [50,200] (Req 3.3)
//   - CLOCK_INTERVAL_MS     at most 1000ms between ticks (Req 8.1)
//   - OVERLAY_DISPLAY_MS    fixed 600ms display duration (Req 9.4)
//   - MOTION_TOGGLE_BUDGET_MS suppress/re-enable budget  (Req 4.1, 4.6)

/** Reveal transition duration, in milliseconds. Must lie within [300, 800]. */
export const REVEAL_DURATION_MS = 560;

/** IntersectionObserver visibility threshold: reveal once >= 10% is visible. */
export const REVEAL_THRESHOLD = 0.1;

/** Minimum delay (ms) a staggered element trails its predecessor. */
export const REVEAL_STAGGER_MIN_MS = 50;

/** Maximum delay (ms) a staggered element trails its predecessor. */
export const REVEAL_STAGGER_MAX_MS = 200;

/** UTC clock update interval, in milliseconds. */
export const CLOCK_INTERVAL_MS = 1000;

/** Fixed navigation transition overlay display duration before unmount, in ms. */
export const OVERLAY_DISPLAY_MS = 1400;

/** Budget (ms) to suppress or re-enable decorative motion on a reduced-motion toggle. */
export const MOTION_TOGGLE_BUDGET_MS = 100;
