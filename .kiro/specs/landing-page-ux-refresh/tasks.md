# Implementation Plan: Landing Page UX Refresh

## Overview

This plan refreshes the AutoOutlook landing page UX, animation, accessibility,
and responsiveness in TypeScript/React, working within the existing files
(`src/components/landing/LandingPage.tsx`, `src/components/ViewTransitionOverlay.tsx`,
`src/index.css`, `tailwind.config.ts`). Work proceeds from shared foundations
(motion timing constants, token resolver) outward to the motion hooks, content
hierarchy, accessibility, navigation transition, and responsive layout, wiring
each piece into `LandingPage` / `App.tsx` as it lands. Each property from the
design is turned into a single fast-check property test placed next to the code
it validates. Test sub-tasks are marked optional with `*`.

## Tasks

- [x] 1. Establish motion/timing foundation and token resolver
  - [x] 1.1 Create the shared motion config constants module
    - Add a `landingMotionConfig.ts` (co-located with the landing component) exporting
      `REVEAL_DURATION_MS` (within [300, 800], e.g. 560), `REVEAL_THRESHOLD` (0.1),
      `REVEAL_STAGGER_MIN_MS` (50), `REVEAL_STAGGER_MAX_MS` (200),
      `CLOCK_INTERVAL_MS` (1000), `OVERLAY_DISPLAY_MS` (600), `MOTION_TOGGLE_BUDGET_MS` (100)
    - Make these the single source of truth consumed by hooks, overlay, and tests
    - _Requirements: 3.1, 8.1, 9.4_

  - [x] 1.2 Implement the brutalist token resolver with default fallback
    - Add a `resolveToken(property, value)` helper that maps a requested color/border/shadow/type
      value to an allowed `tailwind.config.ts` / `index.css` token, returning the matching token
      when present and the property's default token (color → `ink`, shadow → `shadow-retro`, etc.)
      otherwise; never returns a value outside the token set
    - When both the matching token and the property's default token are unavailable, return the
      nearest defined token in the same category so the result stays within the token set
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

  - [ ]* 1.3 Write property test for token resolver fallback
    - **Property 1: Token fallback always yields an allowed token**
    - **Validates: Requirements 1.4, 1.5, 1.6**

- [x] 2. Implement reduced-motion and reveal hooks
  - [x] 2.1 Implement `useReducedMotion` hook
    - Read `window.matchMedia('(prefers-reduced-motion: reduce)')`, subscribe to its `change`
      event, clean up on unmount, and return `false` when `window` is undefined (SSR-safe)
    - _Requirements: 4.1, 4.6_

  - [x] 2.2 Revise `useLandingReveal(reducedMotion)` hook
    - When `reducedMotion` is true OR `IntersectionObserver` is unavailable: set
      `data-landing-visible="true"` on every `[data-landing-reveal]` element and register no observers
    - Otherwise observe each target with `threshold: 0.1`, `rootMargin: '0px 0px -10% 0px'`, set
      visible on intersection and `unobserve` (reveal once)
    - Re-run the effect when `reducedMotion` changes to reveal-all on toggle-to-reduce and re-arm
      observers for not-yet-revealed elements on toggle-off
    - _Requirements: 3.1, 3.2, 3.4, 4.2, 4.3, 4.4_

  - [x] 2.3 Implement the clamped stagger helper
    - Add `staggerDelay(index, stepMs, baseMs?)` returning a CSS custom-property style whose
      successive per-element delta is clamped into [50, 200]ms; keep `heatDelay` as a pure
      index→delay function used only for compositor `animation-delay`
    - _Requirements: 3.3_

  - [ ]* 2.4 Write property test for reveal-once behavior
    - **Property 4: Reveal happens exactly once per element**
    - **Validates: Requirements 3.2**

  - [ ]* 2.5 Write property test for reveal timing bands
    - **Property 5: Reveal timing stays within the configured bands**
    - **Validates: Requirements 3.1**

  - [ ]* 2.6 Write property test for stagger ordering and bounds
    - **Property 6: Stagger delays are ordered and bounded**
    - **Validates: Requirements 3.3**

  - [ ]* 2.7 Write property test for reduced-motion forcing visibility
    - **Property 7: Reduced motion forces all revealables visible**
    - **Validates: Requirements 4.2, 4.3**

  - [ ]* 2.8 Write unit tests for reveal fallback and observer registration
    - No-`IntersectionObserver` path reveals all (3.4); reduced-motion path registers no observers (4.4);
      completed reveal renders at full opacity / no transform offset (3.5)
    - _Requirements: 3.4, 3.5, 4.4_

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement UTC clock and scroll-reset behavior
  - [x] 4.1 Consolidate `useUtcClock` hook
    - Use a 1000ms `setInterval` cleared on unmount; format `timeFull` as
      `HH:MM:SSZ` (two-digit zero-padded, colon-separated, `Z` suffix) and keep updating
      regardless of reduced-motion state
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 4.2 Reset document scroll to top on mount
    - On `LandingPage` mount, reset the vertical scroll offset to 0
    - _Requirements: 7.2_

  - [ ]* 4.3 Write property test for UTC clock formatting
    - **Property 13: UTC clock formatting maps to padded UTC components**
    - **Validates: Requirements 8.2, 8.4**

  - [ ]* 4.4 Write unit tests for clock interval lifecycle and scroll reset
    - Updates at least every 1000ms while mounted and under reduced motion (8.1, 8.5);
      interval cleared on unmount (8.3); scroll resets to top on mount (7.2)
    - _Requirements: 7.2, 8.1, 8.3, 8.5_

- [x] 5. Refine hero content hierarchy and CTAs
  - [x] 5.1 Add single-line product summary and condense hero layout
    - Add a dedicated one-line product-summary element (≤140 chars, no line break) and condense
      headline + summary + primary CTA to fit within the initial desktop viewport height; gate the
      desktop hero presentation to the desktop breakpoint so it is not presented on mobile/tablet
    - _Requirements: 2.1, 2.7_

  - [x] 5.2 Reduce hero to exactly one primary + one secondary CTA
    - Render exactly one primary CTA routing to `#dashboard` and one secondary CTA routing to `#docs`,
      where only the primary CTA uses primary styling and the secondary uses a visually distinct
      non-primary styling; demote "2026 Risk Archive" and "How it works" out of the primary action row
    - _Requirements: 2.2_

  - [ ]* 5.3 Write property test for hero summary constraint
    - **Property 2: Hero product summary is a single short line**
    - **Validates: Requirements 2.1**

  - [ ]* 5.4 Write unit tests for hero CTA configuration
    - Exactly one primary CTA → `#dashboard` and exactly one secondary CTA → `#docs`
    - _Requirements: 2.2_

- [x] 6. Implement navigation landmark, guarded in-page scroll, and section headings
  - [x] 6.1 Add labeled navigation landmark and named landmarks
    - Wrap primary links in `<nav aria-label="Primary">`; ensure named `main` and `footer` landmarks
      and non-empty accessible names on all interactive controls
    - _Requirements: 5.3_

  - [x] 6.2 Implement guarded `scrollToSection` helper
    - If `document.getElementById(id)` exists, `scrollIntoView({ block: 'start' })` honoring
      `scroll-mt-20`; if absent, leave scroll position unchanged and set a transient `role="status"`
      "section unavailable" indication
    - _Requirements: 2.3, 2.4_

  - [x] 6.3 Enforce fixed section ordering and one-tag/one-title headings
    - Keep hero→footer ordering identical across viewports; render each section heading as exactly
      one non-empty tag label plus one non-empty title; keep heading levels non-skipping
    - _Requirements: 2.5, 2.6, 5.5_

  - [ ]* 6.4 Write property test for section heading composition
    - **Property 3: Every section heading has exactly one tag and one title**
    - **Validates: Requirements 2.6**

  - [ ]* 6.5 Write property test for heading-level ordering
    - **Property 11: Heading levels never skip a level**
    - **Validates: Requirements 5.5**

  - [ ]* 6.6 Write unit tests for guarded anchor navigation
    - Anchor activation scrolls to present id (2.3); missing id leaves scroll unchanged and shows
      unavailable status (2.4); keyboard Enter/Space invokes the control action (5.6)
    - _Requirements: 2.3, 2.4, 5.6_

- [x] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Enforce accessibility names, contrast, and decorative-host gating
  - [x] 8.1 Mark looping decorative hosts as decorative
    - Set `aria-hidden="true"` on every element hosting a continuously-looping decorative animation
      so it is excluded from the accessibility tree
    - _Requirements: 4.5_

  - [x] 8.2 Verify and correct token text-contrast pairings
    - Audit foreground/background token pairings used for text and adjust to meet contrast thresholds;
      ensure visible focus indicators meet ≥3:1 against adjacent background
    - _Requirements: 5.2, 5.4_

  - [ ]* 8.3 Write property test for decorative-host aria-hidden gating
    - **Property 8: Looping decorative hosts are hidden from assistive technology**
    - **Validates: Requirements 4.5**

  - [ ]* 8.4 Write property test for control/landmark accessible names
    - **Property 9: Every control and landmark has a non-empty accessible name**
    - **Validates: Requirements 5.3**

  - [ ]* 8.5 Write property test for text token contrast thresholds
    - **Property 10: Text token pairings meet contrast thresholds**
    - **Validates: Requirements 5.4**

  - [ ]* 8.6 Write unit tests for keyboard focus order and activation
    - Tab order matches visual reading order (5.1); Enter/Space invokes control action (5.6);
      forward/backward focus movement works without a pointer (5.7); jest-axe scan passes
    - _Requirements: 5.1, 5.6, 5.7_

- [x] 9. Convert decorative animations to compositor-only motion
  - [x] 9.1 Re-express heat-cell pulse and other decorative animations
    - Re-express `landingHeatPulse` (and any non-conforming keyframes) so they animate only
      `transform`/`opacity`; update `tailwind.config.ts` keyframes and `index.css` accordingly
    - _Requirements: 7.1_

  - [ ]* 9.2 Write property test for decorative animation property limits
    - **Property 12: Decorative animations animate only transform and opacity**
    - **Validates: Requirements 7.1**

- [x] 10. Implement consistent navigation transition and routing
  - [x] 10.1 Make overlay display duration a fixed 600ms constant
    - Drive `ViewTransitionOverlay` display/unmount from `OVERLAY_DISPLAY_MS` (600ms single source
      of truth); suppress looping decorative motion under reduced motion (load bar shown filled)
    - _Requirements: 9.2, 9.3, 9.4_

  - [x] 10.2 Coalesce rapid re-navigation to a single latest overlay
    - Key the overlay on the latest requested `view` plus a monotonically increasing `cycle` bumped
      by `App.tsx` on each view change; coalesce intermediate requests to one overlay for the most
      recent destination, routing through the shared `viewLinkHandler`
    - _Requirements: 9.1, 9.5_

  - [x] 10.3 Handle unresolved route destinations
    - When `navigateView` cannot resolve a destination, retain the current active view and surface a
      non-blocking indication that the view change did not complete; leave no overlay mounted for an
      unresolved destination
    - _Requirements: 9.6_

  - [ ]* 10.4 Write property test for route target normalization
    - **Property 14: Route targets normalize to a canonical destination**
    - **Validates: Requirements 9.1**

  - [ ]* 10.5 Write property test for rapid-navigation overlay coalescing
    - **Property 15: Rapid navigation coalesces to a single latest overlay**
    - **Validates: Requirements 9.5**

  - [ ]* 10.6 Write unit tests for overlay lifecycle and failure handling
    - Single overlay mounts on a view change (9.2); overlay unmounts after 600ms (9.4); unresolved
      route retains view and shows failure indication (9.6)
    - _Requirements: 9.2, 9.4, 9.6_

- [x] 11. Implement responsive layout and mobile hit targets
  - [x] 11.1 Apply responsive grid and single-column mobile layout
    - Ensure mobile renders single-column with no horizontal body overflow; tablet/desktop render
      ≥2-column grids; constrain over-wide content via wrapping/scaling; keep hero elements unclipped
    - _Requirements: 6.1, 6.2, 6.3, 6.6_

  - [x] 11.2 Enforce ≥44×44 CSS px mobile hit targets
    - Apply minimum 44×44 CSS px sizing to all interactive controls at the mobile breakpoint
    - _Requirements: 6.5_

- [x] 12. Wire components together and finalize integration
  - [x] 12.1 Integrate hooks, helpers, and sections into `LandingPage` and `App.tsx`
    - Wire `useReducedMotion` into `useLandingReveal` and decorative gating; connect nav/hero/footer
      controls through `viewLinkHandler` and `scrollToSection`; mount the coalesced overlay from
      `App.tsx`; confirm no orphaned code remains
    - _Requirements: 9.1, 9.2_

  - [ ]* 12.2 Write integration / e2e + visual tests for layout, motion, and performance
    - Playwright coverage for responsive layout and overflow (6.1–6.4, 6.6), rendered hit-target size
      (6.5), in-page scroll timing/offset (2.3), motion suppression timing (4.1, 4.6), overlay visual
      suppression (9.3), CLS ≤ 0.1 (7.3, 7.4), 60fps frame budget (7.5, 7.6), and decorative
      motion running independently during layout shifts (7.7)
    - _Requirements: 2.3, 4.1, 4.6, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.3, 7.4, 7.5, 7.6, 7.7, 9.3_

- [x] 13. Final checkpoint - Ensure all tests pass
  - Run `vitest --run` (unit + property suite) and the Playwright e2e/visual suite; confirm CLS and
    frame-rate budgets. Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP.
- Each task references specific requirements for traceability.
- Checkpoints ensure incremental validation.
- Property tests validate universal correctness properties using fast-check, minimum 100 iterations
  each, tagged `Feature: landing-page-ux-refresh, Property {number}: {property_text}`.
- Unit tests validate specific examples and edge cases; e2e/visual tests cover what cannot be
  asserted in a DOM-only environment.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1", "2.3", "4.1", "9.1"] },
    { "id": 2, "tasks": ["2.2", "2.4", "2.5", "2.6", "2.7", "4.2", "4.3", "9.2"] },
    { "id": 3, "tasks": ["2.8", "4.4", "5.1", "5.2", "8.1", "8.2", "10.1"] },
    { "id": 4, "tasks": ["5.3", "5.4", "6.1", "6.2", "6.3", "8.3", "8.4", "8.5", "10.2", "10.3"] },
    { "id": 5, "tasks": ["6.4", "6.5", "6.6", "8.6", "10.4", "10.5", "10.6", "11.1", "11.2"] },
    { "id": 6, "tasks": ["12.1"] },
    { "id": 7, "tasks": ["12.2"] }
  ]
}
```
