# Retro UI — System Design

A small, opinionated **neo-brutalist** design system for dashboards and
information-dense web apps. Heavy borders, hard offset shadows, no rounded
corners, monospace accents, and CRT scanlines.

The system is intentionally minimal — a few tokens, a few CSS utility classes,
and five React primitives. Everything else composes from those.

---

## 1. Aesthetic in one sentence

> Operational terminal poster: ink-on-paper, sharp corners, offset drop
> shadows, monospace labels, signal-colored highlights only when they carry
> meaning.

This look is defined as much by what it refuses as what it includes. See the
[anti-rules](#9-anti-rules) at the bottom.

---

## 2. File layout

| Concern                  | File                              |
| ------------------------ | --------------------------------- |
| Color / shadow / font tokens | `tailwind.config.ts`          |
| Component-level CSS classes  | `src/index.css` (`@layer components`) |
| React primitives         | `src/components/retro/`           |
| Domain → visual mapping  | `src/types/<domain>.ts` (`*_META` records) |

Feature components consume tokens + primitives + domain meta records; they
should never hard-code hex colors or pixel shadows.

---

## 3. Color tokens

Declared once in `tailwind.config.ts` under `theme.extend.colors`.

### 3.1 Base

| Token   | Hex       | Use                                                 |
| ------- | --------- | --------------------------------------------------- |
| `paper` | `#f5f1e8` | Page background, default surface                    |
| `ink`   | `#111111` | Text, borders, hard shadows, primary emphasis       |
| `navy`  | `#0e1a3a` | Optional secondary dark surface (rare)              |

### 3.2 Signal palette

Used for non-ranked semantic accents — status pills, callouts, chart series.
Each color has a clear meaning; don't pick them for decoration.

| Token            | Hex       | Meaning              |
| ---------------- | --------- | -------------------- |
| `signal.red`     | `#ef3b2c` | alert / danger       |
| `signal.amber`   | `#f7b500` | warning              |
| `signal.orange`  | `#ff8c00` | heightened state     |
| `signal.lime`    | `#9ad62a` | go / nominal         |
| `signal.cyan`    | `#16c1ff` | info / data          |
| `signal.violet`  | `#7a0177` | extreme              |

### 3.3 Domain ramps (optional)

If your app has an ordered scale (severity tiers, priority levels, etc.),
declare a dedicated namespaced ramp so the visual order is enforced by the
token system rather than scattered through components:

```ts
// tailwind.config.ts
colors: {
  scale: {
    t0: '#9ad62a',  // lowest
    t1: '#f7b500',
    t2: '#ff8c00',
    t3: '#ef3b2c',
    t4: '#b30000',
    t5: '#7a0177',  // highest
  },
}
```

Pair each tier with a domain meta record (see [§8](#8-semantic-meta-layer))
that exposes a Tailwind class fragment, an integer rank, and a short label.

---

## 4. Typography

Three variable web fonts, each with a job:

| Family          | Tailwind utility | Use                                            |
| --------------- | ---------------- | ---------------------------------------------- |
| Space Grotesk   | `font-display`   | Headlines, KPI numbers, chip labels            |
| Inter           | `font-sans`      | Body copy, paragraphs                          |
| JetBrains Mono  | `font-mono`      | Metadata, timestamps, status badges, axis labels |

Idiomatic patterns:

```html
<!-- Display headline -->
<h2 class="font-display font-extrabold uppercase tracking-tight">…</h2>

<!-- Terminal-style metadata caption -->
<span class="font-mono text-[10px] uppercase tracking-[0.3em] opacity-70">…</span>
```

The wide letter-tracking on mono text is what gives the "command terminal"
feel — keep it generous (`tracking-[0.25em]` to `tracking-[0.3em]`).

---

## 5. Border & shadow language

Two hard rules enforced everywhere:

### 5.1 Borders are thick and solid `ink`

Border thickness is the primary signal of hierarchy.

| Class            | Use                                         |
| ---------------- | ------------------------------------------- |
| `border-[2px]`   | Small chips, inline pills                   |
| `border-[3px]`   | Default cards, panels, controls             |
| `border-[4px]`   | Hero / top-level sections                   |

### 5.2 Shadows are HARD offsets, not blurred

Declared in `theme.extend.boxShadow`:

| Token              | Value                          | Use                  |
| ------------------ | ------------------------------ | -------------------- |
| `shadow-retro-sm`  | `3px 3px 0 0 #111111`          | Buttons, chips       |
| `shadow-retro`     | `6px 6px 0 0 #111111`          | Standard cards       |
| `shadow-retro-lg` | `10px 10px 0 0 #111111`        | Hero sections        |
| `shadow-retro-inset` | `inset 4px 4px 0 0 #111111`  | Pressed / sunken state |

### 5.3 No rounded corners

There is no `rounded-*` anywhere in the system. Sharp corners are part of
the identity. If a component needs to feel softer, give it more padding,
not a radius.

---

## 6. Motion

Three keyframes, all defined in `tailwind.config.ts`:

| Animation     | Duration | Purpose                                |
| ------------- | -------- | -------------------------------------- |
| `pulse-dot`   | 1.4s     | Live-status indicator dot              |
| `scan`        | 6s       | Vertical scanline sweep on key cards   |
| `ticker`      | 40s      | Horizontal marquee in headers          |

All animations must honor `prefers-reduced-motion`. The simplest approach is
to wrap each `@keyframes` use in a `@media (prefers-reduced-motion: no-preference)`
guard, or globally disable them in `index.css`:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
```

---

## 7. CSS utility classes (`@layer components`)

These live in `src/index.css` and compose the tokens above. They exist so
feature components stay declarative — `<div class="retro-card">` rather than
seven Tailwind utilities repeated everywhere.

| Class                | What it builds                                         |
| -------------------- | ------------------------------------------------------ |
| `.retro-card`        | `bg-paper border-[3px] border-ink shadow-retro`        |
| `.retro-card-lg`     | Hero variant with `border-[4px] shadow-retro-lg`       |
| `.retro-panel`       | Container without shadow (for nested / full-bleed)     |
| `.retro-badge`       | Small mono pill, thin border, tiny shadow              |
| `.retro-chip`        | Display-font chip, used for rank / category labels     |
| `.retro-button`      | Interactive control with tactile hover + press transforms |
| `.retro-divider`     | 3px solid ink horizontal rule                          |
| `.retro-tick`        | Slim vertical tick (slider marks, scale ticks)         |
| `.retro-grid-bg`     | 24px engineering-paper grid overlay                    |
| `.retro-scanline`    | CRT 4-px repeating-line overlay via `::after` + `mix-blend-mode: multiply` |

### 7.1 The "tactile button"

`.retro-button` simulates physical button travel:

```css
.retro-button:hover  { transform: translate(-1px, -1px); box-shadow: 4px 4px 0 0 #111; }
.retro-button:active { transform: translate( 2px,  2px); box-shadow: 1px 1px 0 0 #111; }
```

The button "pops up" on hover and "sinks in" on press. This is the only place
the design system uses transforms — keep it scoped to interactive elements.

### 7.2 Ambient body effects

Three things applied at `<body>` level in `index.css`:

- 18px dotted-pattern background via `radial-gradient` (engineering-paper feel)
- Black-on-paper text selection (`::selection`)
- 3px ink focus ring with 3px offset on `*:focus-visible` (accessibility)

---

## 8. React primitives (`src/components/retro/`)

Thin wrappers around the CSS layer. Five primitives, no more.

| Primitive       | Key props                                                | Purpose                                       |
| --------------- | -------------------------------------------------------- | --------------------------------------------- |
| `RetroBadge`    | `tone`, `pulse?`                                         | Status pill with optional animated dot        |
| `RetroButton`   | `primary?`, `iconOnly?` + native button props            | Tactile button with hover / press transforms  |
| `RetroPanel`    | `title`, `eyebrow`, `badge`, `size`, `scanline?`         | Section with an ink header bar                |
| `RetroCard`     | `size`, `tone`, `scanline?`                              | Bare container, no header                     |
| `RetroDivider`  | `vertical?`, `thickness: 2 \| 3 \| 4`                    | Solid ink rule                                |

### 8.1 When to use a primitive vs. a bespoke `<section>`

- **Use a primitive** for repeating chrome (chips, buttons, status pills).
- **Roll a bespoke `<section>`** for top-level feature surfaces where the
  layout is highly specific. Apply the design language directly with
  `border-[4px] border-ink shadow-retro-lg` — primitives are a convenience,
  not a wall.

---

## 9. Semantic meta layer

This is the seam between the visual system and your domain. The rule is:

> Components must not hard-code colors or pixel values for domain concepts.
> They look them up in a `*_META` record.

Pattern:

```ts
// src/types/<domain>.ts
export const TIER_META: Record<
  TierKey,
  { label: string; ord: number; tw: string; chipText: string }
> = {
  T0: { label: 'Low',      ord: 0, tw: 'bg-scale-t0 text-ink',   chipText: 'T0' },
  T1: { label: 'Moderate', ord: 1, tw: 'bg-scale-t1 text-ink',   chipText: 'T1' },
  T2: { label: 'High',     ord: 2, tw: 'bg-scale-t2 text-paper', chipText: 'T2' },
  // …
};
```

Fields explained:

- **`label`** — human-readable name for screens and accessibility
- **`ord`** — integer rank; lets timeline / comparison code sort tiers without
  stringly-typed comparisons
- **`tw`** — Tailwind class fragment (`bg-… text-…`) that paints the surface;
  components apply it with `className={TIER_META[key].tw}`
- **`chipText`** — short uppercase label for badges and chips

For icon-driven domain concepts, prefer a single Unicode glyph over an icon
library — it fits the terminal-poster aesthetic and avoids a dependency:

```ts
export const KIND_META: Record<Kind, { label: string; glyph: string }> = {
  alpha: { label: 'Alpha', glyph: '◆' },
  beta:  { label: 'Beta',  glyph: '➤' },
  gamma: { label: 'Gamma', glyph: '≋' },
};
```

---

## 10. Layout language

### 10.1 Page chrome

- **Top**: full-width ink-colored `CommandHeader` with a ticker marquee
- **Bottom**: full-width ink-colored footer
- **Between**: dashboard body, no `max-w-*` cap (full viewport width)

### 10.2 Dashboard body

The canonical layout for an information-dense view:

```tsx
<div className="flex-1 w-full grid grid-cols-1 lg:grid-cols-[320px_minmax(0,1fr)] gap-4 p-4">
  <aside className="flex flex-col gap-4 lg:sticky lg:top-4 lg:self-start lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
    {/* operational panels: status, readiness, controls */}
  </aside>
  <main className="flex flex-col gap-4 min-w-0">
    {/* primary content surfaces, stacked */}
  </main>
</div>
```

Key constraints:

- **No artificial `max-w-*`** on the body container — operational dashboards
  earn their density by using the full viewport
- **Sticky left rail** at `lg+` for control / status panels; collapses to a
  single column on smaller viewports
- **`min-w-0` on the main column** — prevents grid blowout when child SVGs
  or long text would otherwise stretch the column past its track
- Inside the main column, every block is a self-contained bordered section
  stacked with `flex flex-col gap-4`. **No cards-within-cards-within-cards** —
  depth is conveyed by border thickness + shadow size, not nesting

---

## 11. Accessibility checklist

- Focus ring: 3px ink outline with 3px offset, applied globally in `index.css`
- Color contrast: every `bg-*` token in this system has a paired text token
  (`text-paper` or `text-ink`) chosen to meet WCAG AA at 14pt bold
- Motion: every animation must respect `prefers-reduced-motion`
- Decoration: scanline / grid backgrounds use `aria-hidden` siblings or CSS
  `::after` pseudo-elements so screen readers don't see them
- Buttons: keep `RetroButton`'s tactile transforms below 3px — anything
  larger reads as a layout shift and can disorient users

---

## 12. Anti-rules

The design language is defined as much by what it refuses. Don't introduce
any of the following without an explicit, written reason:

- ❌ Rounded corners (`rounded-*`)
- ❌ Soft / blurred shadows
- ❌ Glassmorphism / backdrop blur
- ❌ Gradient hero backgrounds
- ❌ Icon libraries with hundreds of glyphs (use 4–8 hand-picked Unicode chars)
- ❌ Generic SaaS dashboard chrome (sidebar nav with avatars, breadcrumbs,
  notification bells)
- ❌ Tabs / accordions for primary content — show everything, scroll
- ❌ Modals for anything except destructive confirmation
- ❌ Hex codes inline in components — use tokens
- ❌ More than 3 levels of border / shadow hierarchy on a single screen

When in doubt, lean toward fewer surfaces, thicker borders, and harder shadows.
