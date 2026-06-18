# Requirements Document

## Introduction

The AutoOutlook landing page (`src/components/landing/LandingPage.tsx`) is the
public entry point to the application. It presents hero copy, a live telemetry
panel, a risk-category ramp, capability and pipeline sections, hazard cards, a
provider-chain section, a tech-stack section, a final call-to-action, and a
footer. Motion is currently delivered through a scroll-reveal system
(`useLandingReveal` backed by `IntersectionObserver`), CSS keyframe animations
(drift fields, sweep lines, ticker, heat-cell pulses, panel glow), and a
navigation-time `ViewTransitionOverlay`. The page already respects
`prefers-reduced-motion` for many animations.

This feature refreshes the user experience, animation quality, and visual design
of the landing page while preserving the existing brutalist / retro design
language (heavy ink borders, hard offset shadows, mono/display typography,
signal accent colors, paper/ink palette). The refresh targets four outcomes:
clearer and more guided UX, more polished and cohesive animation, full
accessibility compliance for motion and interaction, and measurable performance
and responsiveness across viewports — all without changing the page's
informational content or routing behavior.

The scope is limited to the landing page and its directly-owned styles and
motion utilities. Backend data, dashboard, docs, and changelog views are out of
scope except where the shared navigation transition (`ViewTransitionOverlay`) is
triggered from the landing page.

## Glossary

- **Landing_Page**: The React component tree rooted at `LandingPage.tsx` and the
  sections it renders (navigation, hero, ticker, risk ramp, capabilities,
  pipeline, hazards, provider chain, tech stack, final CTA, sponsor, footer).
- **Reveal_System**: The scroll-triggered visibility mechanism implemented by
  `useLandingReveal` that sets `data-landing-visible` on elements marked with
  `data-landing-reveal` as they enter the viewport.
- **Decorative_Animation**: Any non-essential, continuously-running visual motion
  that conveys no information, including drift fields, front lines, sweep lines,
  panel glow, heat-cell pulses, stat shimmer, and the marquee ticker.
- **Reduced_Motion_Mode**: The state in which the operating system or browser
  reports `prefers-reduced-motion: reduce`.
- **Brutalist_Design_System**: The existing visual language defined in
  `src/index.css` and Tailwind config, comprising the paper/ink palette, signal
  accent colors (amber, cyan, lime, red, violet), heavy `border-ink` outlines,
  hard offset `shadow-retro` shadows, and the display/mono/sans type scale.
- **Interactive_Control**: Any landing-page element a user can activate with
  pointer, keyboard, or assistive technology, including navigation links,
  in-page anchor links, and call-to-action buttons.
- **Viewport_Class**: A responsive breakpoint band — mobile (width below 640px),
  tablet (640px to 1023px), and desktop (1024px and above).
- **First_Meaningful_Paint**: The point at which the hero headline, primary
  call-to-action, and navigation are rendered and legible.
- **CLS**: Cumulative Layout Shift, the standard Core Web Vitals metric for
  unexpected layout movement.

## Requirements

### Requirement 1: Preserve Brutalist Visual Identity

**User Story:** As a returning visitor, I want the refreshed landing page to keep
the recognizable brutalist / retro look, so that the product identity stays
consistent.

#### Acceptance Criteria

1. THE Landing_Page SHALL render every section using only color values defined in the Brutalist_Design_System palette tokens (paper, ink, and the signal accent tokens), and SHALL NOT apply any color value that is not present in those existing palette tokens.
2. WHEN a card, panel, or primary button is rendered, THE Landing_Page SHALL apply the Brutalist_Design_System ink border treatment and the hard offset shadow treatment to that element using the existing border and shadow token values, with no blurred or soft shadow applied.
3. THE Landing_Page SHALL apply the Brutalist_Design_System display type role to all headings, the mono type role to all labels, and the sans type role to all body copy, using no type role outside these three defined roles.
4. WHERE a new visual element is added during the refresh, THE Landing_Page SHALL style that element using the appropriate existing Brutalist_Design_System color, border, shadow, and type tokens that match the element's styling needs.
5. IF a new visual element added during the refresh cannot be styled with an existing Brutalist_Design_System token for any of its color, border, shadow, or type properties, THEN THE Landing_Page SHALL fall back to the corresponding default Brutalist_Design_System token for that property rather than introducing a new value.
6. IF the corresponding default Brutalist_Design_System token is itself unavailable for a property, THEN THE Landing_Page SHALL apply the nearest defined token within the same token category (color, border, shadow, or type) and SHALL NOT introduce any color, border, shadow, or type value outside the Brutalist_Design_System.

### Requirement 2: Guided User Experience and Content Hierarchy

**User Story:** As a first-time visitor, I want a clear path through the page, so
that I understand what AutoOutlook does and how to start using it.

#### Acceptance Criteria

1. WHILE the Viewport_Class is desktop, THE Landing_Page SHALL present the hero
   headline, a one-line product summary of at most 140 characters on a single
   line, and the primary call-to-action fully within the initial viewport height
   with no vertical scrolling required.
2. THE Landing_Page SHALL provide exactly one primary call-to-action that routes
   to the dashboard view and exactly one secondary call-to-action that routes to
   the docs view, where exactly one control uses the primary button styling and
   the secondary call-to-action uses a visually distinct non-primary styling so
   that no two controls compete as primary.
3. WHEN a visitor activates an in-page navigation anchor, THE Landing_Page SHALL
   scroll to the corresponding section within 600 milliseconds with the section
   heading top offset positioned at or below the sticky navigation bar height.
4. IF a visitor activates an in-page navigation anchor whose target section is
   not present, THEN THE Landing_Page SHALL retain the current scroll position
   and indicate that the target is unavailable.
5. THE Landing_Page SHALL maintain one fixed ordering of sections from hero
   through footer, preserved identically across all Viewport_Class values.
6. WHERE a section introduces a distinct topic, THE Landing_Page SHALL display a
   section heading composed of exactly one non-empty tag label and exactly one
   non-empty title.
7. WHILE the Viewport_Class is mobile or tablet, THE Landing_Page SHALL NOT
   present the desktop hero presentation, retaining the desktop-only hero layout
   exclusively at the desktop Viewport_Class.

### Requirement 3: Cohesive Entrance and Scroll-Reveal Animation

**User Story:** As a visitor scrolling the page, I want content to animate in
smoothly and consistently, so that the experience feels polished rather than
distracting.

#### Acceptance Criteria

1. WHEN at least 10% of an element marked for reveal is within the viewport, THE
   Reveal_System SHALL transition that element from its hidden state to its
   visible state exactly once over a duration between 300 and 800 milliseconds.
2. WHEN an element becomes visible, THE Reveal_System SHALL set the visible state
   on that element and SHALL stop observing that element so that no further
   reveal transitions are applied to it.
3. WHEN two or more revealable elements share a section, THE Reveal_System SHALL
   apply staggered reveal delays in document order, with each successive element
   delayed between 50 and 200 milliseconds after the previous element.
4. IF the browser does not support `IntersectionObserver`, THEN THE Reveal_System
   SHALL display all revealable elements in their visible state with no reveal
   transition applied.
5. WHEN an entrance animation completes for an element, THE Landing_Page SHALL
   render that element at 100% opacity, at its final document position, and with
   no residual transform offset.

### Requirement 4: Accessible Motion and Reduced-Motion Support

**User Story:** As a visitor who is sensitive to motion, I want animations
suppressed when I request reduced motion, so that I can use the page comfortably.

#### Acceptance Criteria

1. WHILE Reduced_Motion_Mode is active, THE Landing_Page SHALL render every
   Decorative_Animation in a non-animating static state, completing suppression
   within 100 milliseconds of Reduced_Motion_Mode becoming active.
2. WHILE Reduced_Motion_Mode is active, THE Landing_Page SHALL display all
   revealable and hero elements in their visible state at 100% opacity with a
   transform offset of 0 pixels on both the horizontal and vertical axes.
3. WHEN Reduced_Motion_Mode becomes active, THE Reveal_System SHALL set every
   revealable element to its visible state.
4. WHILE Reduced_Motion_Mode is active, THE Reveal_System SHALL NOT register any
   scroll observers for revealable elements.
5. WHERE a continuously looping Decorative_Animation exists, THE Landing_Page
   SHALL mark its host element as decorative for assistive technology such that
   the element is excluded from the accessibility tree.
6. WHEN Reduced_Motion_Mode transitions from active to inactive, THE Landing_Page
   SHALL re-enable all Decorative_Animation within 100 milliseconds.

### Requirement 5: Keyboard and Assistive-Technology Accessibility

**User Story:** As a keyboard or screen-reader user, I want every control to be
reachable and understandable, so that I can navigate the page without a mouse.

#### Acceptance Criteria

1. THE Landing_Page SHALL expose every Interactive_Control to keyboard focus,
   with the sequential focus (tab) order matching the visual reading order of the
   page from top to bottom and left to right.
2. WHEN an Interactive_Control receives keyboard focus, THE Landing_Page SHALL
   display a visible focus indicator that meets a contrast ratio of at least 3:1
   against its adjacent background.
3. THE Landing_Page SHALL provide a non-empty accessible name for every
   Interactive_Control and for the navigation landmark, main landmark, and footer
   landmark.
4. THE Landing_Page SHALL render text and essential icons at a contrast ratio of
   at least 4.5:1 against their background for normal-size text and at least 3:1
   for large-size text, where large-size text is at least 18 point or 14 point
   bold.
5. THE Landing_Page SHALL order heading levels so that no heading level is skipped
   when descending from the page title through section headings.
6. WHEN an Interactive_Control has keyboard focus and the user presses its
   activation key (Enter or Space), THE Landing_Page SHALL invoke the action
   associated with that Interactive_Control.
7. WHILE keyboard focus is on any Interactive_Control, THE Landing_Page SHALL
   allow focus to move to the next and previous focusable element using standard
   keyboard navigation keys, without requiring a pointing device.

### Requirement 6: Responsive Layout Across Viewports

**User Story:** As a visitor on any device, I want the landing page to adapt to my
screen, so that content stays readable and usable.

#### Acceptance Criteria

1. WHILE the Viewport_Class is mobile, THE Landing_Page SHALL render all sections
   in a single-column layout with no horizontal scrolling of the page body (page
   body content width SHALL NOT exceed the viewport width).
2. WHILE the Viewport_Class is tablet or desktop, THE Landing_Page SHALL render
   multi-column section layouts of at least two columns using the configured
   responsive grid definitions, with no horizontal scrolling of the page body.
3. WHERE a hero element (headline, body copy, or call-to-action button) is
   displayed at the current Viewport_Class, THE Landing_Page SHALL keep that
   element fully visible within the viewport width without horizontal clipping.
4. WHEN the viewport width changes across a breakpoint boundary, THE Landing_Page
   SHALL complete re-flow of its layout to the target Viewport_Class within 500
   milliseconds without clipping or overlapping content.
5. WHILE the Viewport_Class is mobile, THE Landing_Page SHALL keep all
   Interactive_Control hit targets at least 44 by 44 CSS pixels.
6. IF a content element (text or media) has an intrinsic width greater than the
   available viewport width at the current Viewport_Class, THEN THE Landing_Page
   SHALL constrain that element to the available width by wrapping text or scaling
   media so that no horizontal scrolling of the page body occurs.

### Requirement 7: Performance and Layout Stability

**User Story:** As a visitor, I want the page to load and animate smoothly, so
that the experience feels fast and stable.

#### Acceptance Criteria

1. THE Landing_Page SHALL restrict Decorative_Animation to compositor-friendly
   properties limited to transform and opacity, and SHALL NOT animate any other
   CSS property.
2. WHEN the Landing_Page mounts, THE Landing_Page SHALL reset the document
   vertical scroll offset to 0 within 100 milliseconds of mount completion.
3. THE Landing_Page SHALL produce a Cumulative Layout Shift (CLS) score of at
   most 0.1, where a score of exactly 0.1 is acceptable, measured from load start
   until 5 seconds after mount completion at desktop Viewport_Class.
4. WHILE no element is entering the viewport, THE Landing_Page SHALL NOT
   contribute any additional layout shift from reveal-related recalculation
   during that interval.
5. WHERE a Decorative_Animation is offscreen or its host section is not visible,
   THE Landing_Page SHALL allow that animation to begin and SHALL pause it once
   it is detected as offscreen, maintaining a scroll frame duration of at most
   16.7 milliseconds (at least 60 frames per second) at desktop Viewport_Class.
6. WHILE a Decorative_Animation is visible, THE Landing_Page SHALL render that
   animation at a frame duration of at most 16.7 milliseconds (at least 60 frames
   per second) at desktop Viewport_Class.
7. WHILE a layout shift is occurring elsewhere on the page, THE Landing_Page
   SHALL allow Decorative_Animation to continue running independently and SHALL
   NOT pause Decorative_Animation in response to that layout shift.

### Requirement 8: Live Telemetry and Clock Behavior

**User Story:** As a visitor, I want the live UTC clock and telemetry panel to
update accurately, so that the page feels operational and current.

#### Acceptance Criteria

1. WHILE the Landing_Page is mounted, THE Landing_Page SHALL update the displayed
   UTC clock value at least once every 1000 milliseconds.
2. WHEN the Landing_Page updates the UTC clock value, THE Landing_Page SHALL set
   it to the current UTC time within 1000 milliseconds of the host system clock.
3. WHEN the Landing_Page unmounts, THE Landing_Page SHALL clear the clock update
   interval before completing unmount such that no further clock updates occur.
4. THE Landing_Page SHALL format the UTC clock value as two-digit zero-padded
   hours (00 to 23), two-digit zero-padded minutes (00 to 59), and two-digit
   zero-padded seconds (00 to 59), separated by colons and terminated with the
   `Z` suffix.
5. WHILE Reduced_Motion_Mode is active, THE Landing_Page SHALL continue to update
   the UTC clock value at least once every 1000 milliseconds.

### Requirement 9: Consistent Navigation Transition

**User Story:** As a visitor moving between views, I want the navigation
transition to behave consistently, so that route changes feel intentional.

#### Acceptance Criteria

1. WHEN a visitor activates a control that changes the active view, THE
   Landing_Page SHALL route the navigation through the shared view-link handler.
2. WHEN the active view changes, THE Landing_Page SHALL trigger the shared
   navigation transition overlay for the destination view.
3. WHILE Reduced_Motion_Mode is active, THE navigation transition overlay SHALL
   present its destination state without looping decorative motion.
4. WHEN the navigation transition overlay has been displayed for its fixed
   display duration of 600 milliseconds, THE navigation transition overlay SHALL
   unmount.
5. IF the visitor activates another view-changing control while a navigation
   transition overlay is already active, THEN THE Landing_Page SHALL route the
   navigation to the most recently requested destination view through the shared
   view-link handler and present a single navigation transition overlay for that
   destination.
6. IF routing the navigation through the shared view-link handler fails to
   resolve the destination view, THEN THE Landing_Page SHALL retain the current
   active view and present an indication to the visitor that the view change did
   not complete.
