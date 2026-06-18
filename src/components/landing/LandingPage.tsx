import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';

import RetroBadge from '../retro/RetroBadge';
import { HAZARD_META, RISK_META, type HazardKey, type RiskCategory } from '../../types/forecast';
import { viewLinkHandler } from '../../utils/navigateView';
import { CLOCK_INTERVAL_MS, REVEAL_STAGGER_MAX_MS, REVEAL_STAGGER_MIN_MS, REVEAL_THRESHOLD } from './landingMotionConfig';
import { useReducedMotion } from './useReducedMotion';

// ---------------------------------------------------------------------------
// Static data
// ---------------------------------------------------------------------------

const RISK_ORDER: RiskCategory[] = ['TSTM', 'MRGL', 'SLGT', 'ENH', 'MOD', 'HIGH'];

const RISK_DESCRIPTORS: Record<RiskCategory, string> = {
  TSTM: 'Non-severe convection capable of lightning, brief gusty winds, and small hail.',
  MRGL: 'Isolated severe storms possible. Limited in coverage, intensity, and duration.',
  SLGT: 'Scattered severe storms expected. Short-lived or isolated intense cells.',
  ENH:  'Numerous severe storms possible. More persistent and widespread coverage.',
  MOD:  'Widespread severe storms likely. Long-track or intense storms anticipated.',
  HIGH: 'Severe weather outbreak expected. Long-track tornadoes or destructive derecho.',
};

const CAPABILITIES: { tag: string; title: string; body: string; accent: string }[] = [
  {
    tag: 'C-01',
    title: 'Categorical Outlook Map',
    body: 'Stepped risk polygons rendered in the SPC convention. TSTM now follows trained general-thunder probability support, while MRGL → HIGH stay ordered as concentric annuli.',
    accent: 'bg-risk-slgt',
  },
  {
    tag: 'C-02',
    title: 'Hazard Probability Board',
    body: 'Trained tornado, hail, damaging-wind, and general-thunder probabilities resolved per forecast hour. Non-thunder maps show one labeled CIG corridor with a two-line overlay style.',
    accent: 'bg-signal-red',
  },
  {
    tag: 'C-03',
    title: 'Risk Timeline',
    body: 'Morning · afternoon · evening · overnight risk curves stitched across the cycle window. Read the diurnal evolution of the convective threat without dragging the slider.',
    accent: 'bg-signal-amber',
  },
  {
    tag: 'C-04',
    title: 'SPC QC Console',
    body: 'Forecast bundles are checked against the official SPC Day 1 outlook with an agreement readout, displacement ratio, post-prediction leakage guard, and a full risk category ledger.',
    accent: 'bg-signal-cyan',
  },
  {
    tag: 'C-05',
    title: '2026 Risk Archive',
    body: 'Static 21-event ENH+ verification archive for March through May 2026, regenerated with trained v1.2 models across each 12Z-to-12Z Day 1 window. April 18 was removed from the selector.',
    accent: 'bg-signal-lime',
  },
  {
    tag: 'C-06',
    title: 'SPC Overlay Compare',
    body: 'Switch the map between AutoOutlook only, official SPC Day 1 only, or overlay comparison. QC hatches mark true agreement, underforecast, and overforecast regions.',
    accent: 'bg-signal-cyan',
  },
  {
    tag: 'C-07',
    title: 'Focused Operator Navigation',
    body: 'The sidebar now prioritizes the operational path: outlook map, primary forecast, hazards, parameters, timeline, discussion, SPC verification, and system status.',
    accent: 'bg-signal-violet',
  },
];

const PIPELINE_STEPS = [
  {
    code: '01',
    label: 'INGEST',
    title: 'Latest model cycle',
    body: 'Pulls the severe-weather fields for model schema v5: CAPE, CIN, moisture, shear, reflectivity, and 500-mb height.',
  },
  {
    code: '02',
    label: 'DERIVE',
    title: 'Ingredient diagnostics',
    body: 'Computes shear, helicity, STP, SCP, EHI, and SHIP across the grid, with the CONUS focus region auto-detected.',
  },
  {
    code: '03',
    label: 'INFER',
    title: 'Hazard probability',
    body: 'Four calibrated XGBoost heads score tornado, hail, wind, and thunder from 37 inputs.',
  },
  {
    code: '04',
    label: 'PUBLISH',
    title: 'Outlook bundle',
    body: 'Assembles risk polygons, probability tiles, CIG overlays, and metadata for hours f00–f48.',
  },
  {
    code: '05',
    label: 'VERIFY',
    title: 'SPC QC cross-check',
    body: 'Cross-checks the official SPC Day 1 outlook for agreement, displacement, and category counts.',
  },
];

const HAZARDS: { key: HazardKey; band: string; sigBand: string; copy: string }[] = [
  {
    key: 'tornado',
    band: '2 / 5 / 10 / 15 / 30 / 45 / 60 %',
    sigBand: 'SIG ≥10% EF2+',
    copy: 'Probability of a tornado within 25 mi of any point. Significant overlay tracks the conditional probability of EF2 or stronger.',
  },
  {
    key: 'hail',
    band: '5 / 15 / 30 / 45 / 60 %',
    sigBand: 'SIG ≥10% 2"+',
    copy: 'Probability of severe hail (≥1") within 25 mi. SIG layer activates once 2"+ stones become more than incidental.',
  },
  {
    key: 'wind',
    band: '5 / 15 / 30 / 45 / 60 %',
    sigBand: 'SIG ≥10% 74 mph+',
    copy: 'Probability of damaging convective wind (≥58 mph) within 25 mi. SIG layer flags potential derecho-class events.',
  },
  {
    key: 'flood',
    band: 'Marginal · Slight · Moderate · High',
    sigBand: '— ',
    copy: 'Excessive rainfall outlook derived from PWAT, storm motion, and total accumulation guidance over the forecast window.',
  },
];

// Single-line hero product summary (Req 2.1 / Property 2): at most 140
// characters and free of any line-break character so it renders on one line
// within the initial desktop viewport. Kept short enough to stay on a single
// line inside the desktop hero column without horizontal clipping (Req 6.3).
export const HERO_PRODUCT_SUMMARY =
  'From raw model data to verified SPC-style severe-weather outlook — fully automated.';

// Tech stack grouped into labeled layers so it reads as an organized stack
// rather than one undifferentiated wrap of pills.
const TECH_GROUPS: { label: string; items: string[] }[] = [
  { label: 'Frontend', items: ['VITE', 'REACT 18', 'TYPESCRIPT 5', 'TAILWIND 3'] },
  { label: 'Mapping & data', items: ['react-simple-maps', 'D3-GEO', 'GEOJSON', 'TOPOJSON', 'WEBP TILES'] },
  { label: 'Verification', items: ['SPC VERIFICATION', 'SPC OVERLAY QC', '2026 RISK ARCHIVE', 'SPC HAZARD OUTLOOKS', 'STORM REPORTS', 'CATEGORY LEDGER'] },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function useUtcClock() {
  const [now, setNow] = useState<Date>(() => new Date());
  useEffect(() => {
    // SSR-safe: guard against environments without `window`.
    if (typeof window === 'undefined') return undefined;
    // The clock is informational (not decorative), so it keeps ticking
    // regardless of reduced-motion state. CLOCK_INTERVAL_MS is the single
    // source of truth for the tick cadence (Req 8.1, 8.5).
    const id = window.setInterval(() => setNow(new Date()), CLOCK_INTERVAL_MS);
    // Clear on unmount so no clock update runs after teardown (Req 8.3).
    return () => window.clearInterval(id);
  }, []);
  return useMemo(() => {
    // Two-digit zero-padded UTC components, colon-separated, `Z`-terminated
    // -> `HH:MM:SSZ` (Req 8.2, 8.4).
    const hh = String(now.getUTCHours()).padStart(2, '0');
    const mm = String(now.getUTCMinutes()).padStart(2, '0');
    const ss = String(now.getUTCSeconds()).padStart(2, '0');
    const yyyy = now.getUTCFullYear();
    const mo = String(now.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(now.getUTCDate()).padStart(2, '0');
    return {
      time: `${hh}${mm}Z`,
      timeFull: `${hh}:${mm}:${ss}Z`,
      date: `${yyyy}-${mo}-${dd}`,
    };
  }, [now]);
}

// Drives the scroll-reveal system for elements marked with `data-landing-reveal`.
//
// Behavior (Req 3.1, 3.2, 3.4, 4.2, 4.3, 4.4):
//   - When `reducedMotion` is true OR `IntersectionObserver` is unavailable,
//     every reveal target is set visible immediately and NO observers are
//     registered (Req 3.4, 4.2, 4.3, 4.4).
//   - Otherwise each target is observed with `threshold: REVEAL_THRESHOLD`
//     (0.1, i.e. >= 10% visible) and `rootMargin: '0px 0px -10% 0px'`; on the
//     first intersection the element is set visible and immediately unobserved
//     so it reveals exactly once (Req 3.1, 3.2).
//
// The effect depends on `reducedMotion`, so a runtime toggle re-runs it:
// toggling to reduce reveals every element and drops observers, while toggling
// off re-arms observers for any element not yet revealed (already-revealed
// elements keep their `data-landing-visible` flag and are simply re-observed
// then immediately unobserved on their next intersection — they never animate
// again because the visible state is already set).
function useLandingReveal(reducedMotion: boolean) {
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;

    const targets = Array.from(document.querySelectorAll<HTMLElement>('[data-landing-reveal]'));
    if (!targets.length) return undefined;

    if (reducedMotion || !('IntersectionObserver' in window)) {
      targets.forEach((target) => {
        target.dataset.landingVisible = 'true';
      });
      return undefined;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const target = entry.target as HTMLElement;
          target.dataset.landingVisible = 'true';
          observer.unobserve(target);
        });
      },
      {
        rootMargin: '0px 0px -10% 0px',
        threshold: REVEAL_THRESHOLD,
      },
    );

    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, [reducedMotion]);
}

function revealDelay(ms: number): CSSProperties {
  return { '--landing-reveal-delay': `${ms}ms` } as CSSProperties;
}

// Clamp a per-element reveal stagger into the spec-compliant band so each
// successive revealable element trails its predecessor by 50–200ms (Req 3.3).
// The requested `stepMs` is clamped into [REVEAL_STAGGER_MIN_MS,
// REVEAL_STAGGER_MAX_MS] and applied as a constant per-index delta, which keeps
// the resulting delays monotonically non-decreasing and bounded (Property 6).
// Returns a CSS custom-property style consumed by the `--landing-reveal-delay`
// transition variable (distinct from `heatDelay`, which drives compositor
// `animation-delay` for the decorative heat grid).
export function staggerDelay(index: number, stepMs: number, baseMs = 0): CSSProperties {
  const step = Math.min(REVEAL_STAGGER_MAX_MS, Math.max(REVEAL_STAGGER_MIN_MS, stepMs));
  const safeIndex = Math.max(0, Math.floor(index));
  const delay = baseMs + safeIndex * step;
  return { '--landing-reveal-delay': `${delay}ms` } as CSSProperties;
}

function heatDelay(index: number, cols: number): CSSProperties {
  const row = Math.floor(index / cols);
  const col = index % cols;
  return { animationDelay: `${col * 24 + row * 70}ms` };
}

// Use the shared `viewLinkHandler` so internal links/buttons all route through
// the same logic that App.tsx listens to (hashchange).
const go = viewLinkHandler;

// How long the transient "section unavailable" indication stays visible before
// it clears itself (Req 2.4). Kept short so the status is unobtrusive.
const SECTION_STATUS_TIMEOUT_MS = 4000;

// Guarded in-page navigation (Req 2.3, 2.4 / design "Guarded in-page
// navigation").
//
// Accepts either a bare id (`capabilities`) or a hash form (`#capabilities`).
//   - When the target section exists, scroll it to the top of the viewport with
//     `scrollIntoView({ block: 'start' })`. Each in-page section carries
//     `scroll-mt-20`, so its heading lands at/just below the sticky nav bar
//     rather than underneath it (Req 2.3). Returns `{ ok: true }`.
//   - When the target is absent, the scroll position is left unchanged and
//     `{ ok: false }` is returned so callers can surface a transient,
//     accessible "section unavailable" indication (Req 2.4).
//
// SSR-safe: returns `{ ok: false }` when there is no `document`.
export function scrollToSection(id: string): { ok: boolean } {
  if (typeof document === 'undefined') return { ok: false };
  const targetId = id.startsWith('#') ? id.slice(1) : id;
  if (!targetId) return { ok: false };
  const target = document.getElementById(targetId);
  if (!target) return { ok: false };
  target.scrollIntoView({ block: 'start' });
  return { ok: true };
}

// Wires the guarded `scrollToSection` helper to a transient status message.
// Returns an anchor click handler factory plus the current unavailable-target
// label (or `null`). When the target is present the click scrolls to it and no
// status is shown; when it is absent the default anchor jump is prevented, the
// scroll position is left unchanged, and a transient status is set that clears
// itself after `SECTION_STATUS_TIMEOUT_MS` (Req 2.4).
function useGuardedSectionNav() {
  const [unavailableId, setUnavailableId] = useState<string | null>(null);
  const timeoutRef = useRef<number | null>(null);

  // Clear any pending timer on unmount so no state update runs after teardown.
  useEffect(() => () => {
    if (timeoutRef.current !== null && typeof window !== 'undefined') {
      window.clearTimeout(timeoutRef.current);
    }
  }, []);

  const onSectionLink = (id: string) => (event: { preventDefault: () => void }) => {
    // Take over the navigation so a missing target cannot trigger the browser's
    // default hash jump (which would otherwise move/clear scroll position).
    event.preventDefault();
    const { ok } = scrollToSection(id);
    if (ok) {
      // Target found and scrolled: clear any lingering unavailable status.
      setUnavailableId(null);
      return;
    }
    const targetId = id.startsWith('#') ? id.slice(1) : id;
    setUnavailableId(targetId);
    if (typeof window !== 'undefined') {
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
      timeoutRef.current = window.setTimeout(() => {
        setUnavailableId(null);
        timeoutRef.current = null;
      }, SECTION_STATUS_TIMEOUT_MS);
    }
  };

  return { unavailableId, onSectionLink };
}

// ---------------------------------------------------------------------------
// Section: top navigation
// ---------------------------------------------------------------------------

function LandingNav() {
  const clock = useUtcClock();
  const { unavailableId, onSectionLink } = useGuardedSectionNav();
  return (
    <header className="landing-nav sticky top-0 z-40 border-b-[3px] border-ink bg-paper">
      <div className="mx-auto flex max-w-[1400px] items-center gap-4 px-4 py-2.5 sm:px-6">
        <a href="#" onClick={go('')} aria-label="AutoOutlook home" className="flex items-center gap-3">
          <div className="border-[3px] border-ink bg-ink px-2 py-1 font-mono text-[10px] font-bold tracking-[0.3em] text-paper">
            AO/01
          </div>
          <div className="hidden flex-col leading-none sm:flex">
            <span className="font-display text-lg font-extrabold uppercase tracking-tight">
              Auto<span className="text-signal-amber">Outlook</span>
            </span>
            <span className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.25em] text-ink/60">
              Convective Risk Intelligence
            </span>
          </div>
        </a>

        {/*
          Primary navigation landmark (Req 5.3): the in-page and cross-view
          links are wrapped in a labeled `<nav aria-label="Primary">` so the
          navigation region is exposed to assistive technology with a non-empty
          accessible name. Each contained link carries non-empty text content,
          giving every control an accessible name.
        */}
        <nav aria-label="Primary" className="hidden flex-1 items-center justify-center gap-6 md:flex">
          <a href="#capabilities" onClick={onSectionLink('#capabilities')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Capabilities
          </a>
          <a href="#pipeline" onClick={onSectionLink('#pipeline')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Pipeline
          </a>
          <a href="#landing-hazards" onClick={onSectionLink('#landing-hazards')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Hazards
          </a>
          <a href="#stack" onClick={onSectionLink('#stack')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Stack
          </a>
          <a href="#changelog" onClick={go('#changelog')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Changelog
          </a>
          <a href="#docs-enh-verification" onClick={go('#docs-enh-verification')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Risk Archive
          </a>
          <a href="#docs" onClick={go('#docs')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Docs
          </a>
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <div className="hidden items-center gap-2 border-[2px] border-ink bg-paper px-2 py-1 font-mono text-[10px] uppercase tracking-[0.25em] text-ink shadow-retro-sm sm:flex">
            <span className="inline-block h-2 w-2 animate-pulse-dot rounded-full bg-signal-lime" aria-hidden />
            <span>UTC {clock.timeFull}</span>
          </div>
          <a
            href="#dashboard"
            onClick={go('#dashboard')}
            className="retro-button retro-button-primary whitespace-nowrap text-[11px]"
          >
            Launch Dashboard ▸
          </a>
        </div>
      </div>

      {/*
        Transient in-page-navigation status (Req 2.4). The live region is always
        present in the DOM so assistive technology reliably announces changes;
        it is visually shown only while an in-page anchor target is unavailable.
        When a target is missing, `scrollToSection` leaves the scroll position
        unchanged and this surfaces an unobtrusive "section unavailable" notice
        that clears itself after a short timeout.
      */}
      <div role="status" aria-live="polite" className="mx-auto max-w-[1400px] px-4 sm:px-6">
        {unavailableId && (
          <div className="border-x-[2px] border-b-[2px] border-ink bg-signal-amber px-3 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.25em] text-ink">
            ⚠ Section unavailable
          </div>
        )}
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Section: hero
// ---------------------------------------------------------------------------

// Live "last published" indicator driven by the scheduled GitHub Actions run
// that refreshes the static artifacts ("Refresh static AutoOutlook artifacts"
// → free-hosting-refresh.yml). Fetches the latest *successful* run timestamp
// from the public GitHub API and returns an elapsed `HH:MM:SS` string that
// ticks every second. Returns `null` while loading or if the request fails
// (offline / API rate-limited), so callers can show a graceful placeholder.
const PUBLISH_RUNS_URL =
  'https://api.github.com/repos/ShianMike/AutoOutlook/actions/workflows/free-hosting-refresh.yml/runs?status=success&per_page=1';

function useLastPublished(): string | null {
  const [publishedAt, setPublishedAt] = useState<Date | null>(null);
  const [now, setNow] = useState<Date>(() => new Date());

  // Tick once a second so the elapsed readout stays live.
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const id = window.setInterval(() => setNow(new Date()), CLOCK_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, []);

  // Fetch the latest successful run on mount, then refresh every 5 minutes.
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    let active = true;
    const load = async () => {
      try {
        const res = await fetch(PUBLISH_RUNS_URL, { headers: { Accept: 'application/vnd.github+json' } });
        if (!res.ok) return;
        const data = await res.json();
        const run = data?.workflow_runs?.[0];
        const ts: string | undefined = run?.updated_at ?? run?.run_started_at ?? run?.created_at;
        if (active && ts) setPublishedAt(new Date(ts));
      } catch {
        /* offline or rate-limited — leave the placeholder in place */
      }
    };
    void load();
    const id = window.setInterval(load, 5 * 60 * 1000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, []);

  return useMemo(() => {
    if (!publishedAt) return null;
    let s = Math.max(0, Math.floor((now.getTime() - publishedAt.getTime()) / 1000));
    const h = Math.floor(s / 3600);
    s -= h * 3600;
    const m = Math.floor(s / 60);
    s -= m * 60;
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${pad(h)}:${pad(m)}:${pad(s)}`;
  }, [publishedAt, now]);
}

// Rotating "today's risk" scenarios for the hero telemetry panel. The panel
// cycles through these to read as a live, changing outlook (auto-advance is
// paused on hover and disabled under reduced motion).
interface HeroScenario {
  category: RiskCategory;
  region: string;
  hazard: HazardKey;
  conf: number;
  agree: number;
}

const HERO_SCENARIOS: HeroScenario[] = [
  { category: 'ENH', region: 'Central Plains', hazard: 'tornado', conf: 72, agree: 35 },
  { category: 'SLGT', region: 'Mid-Mississippi Valley', hazard: 'wind', conf: 58, agree: 49 },
  { category: 'MRGL', region: 'Southern Appalachians', hazard: 'hail', conf: 41, agree: 63 },
  { category: 'HIGH', region: 'Deep South', hazard: 'tornado', conf: 88, agree: 24 },
  { category: 'MOD', region: 'Ark-La-Tex', hazard: 'wind', conf: 79, agree: 31 },
  { category: 'TSTM', region: 'Ohio Valley', hazard: 'flood', conf: 33, agree: 70 },
];

function Hero({ reducedMotion = false }: { reducedMotion?: boolean }) {
  const clock = useUtcClock();
  const lastPublishAgo = useLastPublished();
  const [scenarioIdx, setScenarioIdx] = useState(0);
  const pausedRef = useRef(false);

  // Auto-cycle the telemetry readout so it reads as a live, updating outlook.
  // Paused on hover/focus and disabled entirely under reduced motion.
  useEffect(() => {
    if (reducedMotion || typeof window === 'undefined') return undefined;
    const id = window.setInterval(() => {
      if (pausedRef.current) return;
      setScenarioIdx((i) => (i + 1) % HERO_SCENARIOS.length);
    }, 3600);
    return () => window.clearInterval(id);
  }, [reducedMotion]);

  const scenario = HERO_SCENARIOS[scenarioIdx];
  const catMeta = RISK_META[scenario.category];
  const catColorClass = catMeta.tw.split(' ')[0];
  const catTextClass = catMeta.tw.split(' ')[1] ?? 'text-ink';
  const hazardMeta = HAZARD_META[scenario.hazard];
  const hazardLabel = scenario.hazard === 'wind' ? 'Wind' : hazardMeta.label;
  const hazardBar = HAZARD_ACCENT[scenario.hazard].bar;
  const hazardText = hazardBar.replace('bg-', 'text-');

  return (
    <section className="landing-atmosphere relative overflow-hidden border-b-[3px] border-ink bg-paper">
      <div className="pointer-events-none absolute inset-0 retro-grid-bg opacity-60" aria-hidden />
      {/*
        Continuously-looping decorative hosts (Req 4.1, 7.1). They are not
        mounted at all under reduced motion so suppression is immediate and the
        accessibility tree never sees them; when motion is allowed the CSS
        `prefers-reduced-motion` block still governs their animation.
      */}
      {!reducedMotion && (
        <>
          <div className="landing-drift-field" aria-hidden />
          <div className="landing-front-line landing-front-line-a" aria-hidden />
          <div className="landing-front-line landing-front-line-b" aria-hidden />
        </>
      )}

      {/*
        Desktop hero presentation (Req 2.7): the two-column, viewport-height
        layout is gated to the desktop breakpoint via `lg:` utilities. On
        mobile/tablet the section falls back to a single-column, naturally
        flowing layout. On desktop the grid is constrained to the initial
        viewport height (minus the sticky nav) and vertically centered so the
        headline, single-line summary, and primary CTA sit fully within the
        first viewport without scrolling (Req 2.1).
      */}
      <div className="relative mx-auto grid max-w-[1400px] grid-cols-1 gap-6 px-4 py-12 sm:px-6 lg:min-h-[calc(100vh-3.75rem)] lg:grid-cols-[1.4fr_1fr] lg:items-center lg:gap-10 lg:py-12">
        {/* Left: headline */}
        <div className="landing-hero-copy flex flex-col gap-5 lg:gap-4">
          <div className="landing-hero-item flex flex-wrap items-center gap-2" style={revealDelay(60)}>
            <RetroBadge tone="ink">[ SYSTEM 01 / OUTLOOK ]</RetroBadge>
            <RetroBadge tone="lime" pulse>OPERATIONAL</RetroBadge>
            <RetroBadge tone="paper">v1.2 · MODEL V5</RetroBadge>
          </div>

          <h1 className="landing-hero-item landing-title break-words font-display font-extrabold uppercase leading-[0.85] tracking-[-0.04em] text-ink"
              style={{ ...revealDelay(130), fontSize: 'clamp(3rem, 9vw, 7rem)' }}>
            Auto<span className="text-signal-amber">Outlook</span>
          </h1>

          {/*
            Single-line product summary (Req 2.1 / Property 2). The string is
            line-break free and capped at 140 chars; `lg:whitespace-nowrap`
            keeps it on one line at the desktop breakpoint while allowing it to
            wrap on narrower viewports without horizontal clipping (Req 6.3).
          */}
          <p className="landing-hero-item landing-hero-summary max-w-[640px] font-display text-xl font-bold uppercase leading-tight tracking-tight text-ink/80 sm:text-2xl lg:max-w-none lg:whitespace-nowrap lg:text-[1.4rem]"
             style={revealDelay(210)}>
            {HERO_PRODUCT_SUMMARY}
          </p>

          {/*
            Detailed product description. Shown on mobile/tablet where vertical
            space is scroll-friendly, but hidden at the desktop breakpoint so
            the condensed desktop hero (headline + summary + primary CTA) fits
            within the initial viewport height (Req 2.1, 2.7).
          */}
          <p className="landing-hero-item max-w-[640px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg lg:hidden"
             style={revealDelay(290)}>
            AutoOutlook ingests the latest extended-range model cycle, derives the severe-weather ingredient deck,
            runs trained tornado / hail / wind / thunder probability heads, and publishes
            SPC-style risk polygons + probability tiles for forecast hours <span className="font-mono font-bold text-ink">f00–f48</span>.
            v1.2 retrains four calibrated XGBoost models on 849,720 archive rows and expands the feature
            schema to 37 inputs, including location, temperature, reflectivity, 500-mb height, and time-season cycles.
            Trained thunder now drives TSTM, while cleaner CIG corridors and Merged Outlook improve the map workflow.
            The 2026 Risk Archive is regenerated with the same trained models across each full 12Z-to-12Z Day 1 window.
          </p>

          <div className="landing-hero-item flex flex-wrap items-center gap-3 pt-2" style={revealDelay(370)}>
            <a
              href="#dashboard"
              onClick={go('#dashboard')}
              className="retro-button retro-button-primary landing-action-button !px-5 !py-3 text-base"
            >
              Launch Dashboard ▸
            </a>
            <a
              href="#docs"
              onClick={go('#docs')}
              className="retro-button landing-action-button !px-5 !py-3 text-base"
            >
              Read the Docs
            </a>
          </div>

          <dl className="landing-hero-item mt-6 grid grid-cols-2 gap-px border-[3px] border-ink bg-ink sm:grid-cols-4" style={revealDelay(450)}>
            <Stat label="FORECAST HOURS" value="f00–f48" sub="hourly resolution" />
            <Stat label="PROVIDER CHAIN" value="3-tier" sub="live · fallback · mock" />
            <Stat label="HAZARD HEADS" value="4 trained" sub="tor · hail · wind · tstm" />
            <Stat label="SPC QC" value="3 modes" sub="auto · SPC · overlay" />
          </dl>
        </div>

        {/* Right: telemetry panel */}
        <div className="landing-hero-panel relative">
          <div
            className="qc-glow-border shadow-retro-lg"
            onMouseEnter={() => { pausedRef.current = true; }}
            onMouseLeave={() => { pausedRef.current = false; }}
          >
            <div className="relative z-[1] retro-scanline overflow-hidden bg-ink p-0 text-paper">
            {!reducedMotion && (
              <>
                <div className="landing-panel-glow" aria-hidden />
                <div className="landing-sweep-line" aria-hidden />
              </>
            )}
            {/* corner crosshairs */}
            <CornerMarks />

            <div className="flex items-center justify-between border-b-[3px] border-paper/15 px-4 py-2">
              <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
                ◢ TELEMETRY · LIVE
              </span>
              <div className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 animate-pulse-dot rounded-full bg-signal-lime" aria-hidden />
                <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/80">SYNC</span>
              </div>
            </div>

            {/* Primary readout — the focal point: Day 1 category + main hazard.
                Auto-cycles through HERO_SCENARIOS to read as a live outlook. */}
            <div className="grid grid-cols-1 gap-px border-b-[3px] border-paper/15 bg-paper/10 sm:grid-cols-2">
              <div className={`min-w-0 p-4 transition-colors duration-500 ${catColorClass} ${catTextClass}`}>
                <div className="font-mono text-[10px] uppercase tracking-[0.3em] opacity-70">Day 1 outlook</div>
                <div className="mt-1 font-display text-5xl font-extrabold uppercase leading-none tracking-tight">
                  {scenario.category}
                </div>
                <div className="mt-2 truncate font-mono text-[10px] uppercase tracking-[0.2em] opacity-80" title={scenario.region}>
                  {scenario.region}
                </div>
              </div>
              <div className="flex flex-col bg-ink p-4">
                <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/50">Main hazard</div>
                <div className="mt-1 flex items-center gap-2">
                  <HazardIcon name={scenario.hazard} className={`h-6 w-6 shrink-0 ${hazardText}`} />
                  <span className="min-w-0 break-words font-display text-xl font-extrabold uppercase leading-tight tracking-tight text-paper">
                    {hazardLabel}
                  </span>
                </div>
                <div className="mt-auto pt-3 flex items-baseline justify-between font-mono text-[10px] uppercase tracking-[0.25em] text-paper/50">
                  <span>Confidence</span>
                  <span className="text-paper/80">{scenario.conf}%</span>
                </div>
                <div className="mt-1 h-2 w-full border border-paper/20 bg-paper/5" aria-hidden>
                  <div className={`h-full transition-[width] duration-500 ${hazardBar}`} style={{ width: `${scenario.conf}%` }} />
                </div>
              </div>
            </div>

            {/* Supporting stats. */}
            <div className="grid grid-cols-2 gap-px bg-paper/10">
              <DarkStat label="UTC TIME" value={clock.timeFull} sub={clock.date} />
              <DarkStat label="CYCLE" value="12Z RUN" sub="auto-detected" />
              <DarkStat label="SPC AGREE" value={`${scenario.agree}%`} sub="QC sample" />
              <DarkStat label="SPC QC" value="LEDGER" valueClass="text-signal-lime" sub="risk counts" />
            </div>

            {/* probability heatmap simulation */}
            <div className="border-t-[3px] border-paper/15 p-3">
              <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-paper/60">
                  HAZARD PROBABILITY · F+12H
                </span>
                <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-paper/40">
                  STRIDE 1
                </span>
              </div>
              <ProbabilityTile />
              <div className="mt-2 flex items-center justify-between font-mono text-[9px] uppercase tracking-[0.25em] text-paper/50">
                <span>2%</span>
                <span>5%</span>
                <span>15%</span>
                <span>30%</span>
                <span>45%</span>
                <span>60%</span>
              </div>
            </div>

            <div className="border-t-[3px] border-paper/15 px-5 py-3 font-mono text-[10px] uppercase leading-relaxed tracking-[0.2em] text-paper/60">
              LAST PUBLISH {lastPublishAgo ? `${lastPublishAgo} AGO` : '—'}
            </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function CornerMarks() {
  const cls = 'absolute h-3 w-3 border-paper/70';
  return (
    <>
      <span aria-hidden className={`${cls} left-1.5 top-1.5 border-l-2 border-t-2`} />
      <span aria-hidden className={`${cls} right-1.5 top-1.5 border-r-2 border-t-2`} />
      <span aria-hidden className={`${cls} bottom-1.5 left-1.5 border-b-2 border-l-2`} />
      <span aria-hidden className={`${cls} bottom-1.5 right-1.5 border-b-2 border-r-2`} />
    </>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="landing-stat bg-paper p-3">
      <div className="font-mono text-[9px] uppercase tracking-[0.3em] text-ink/60">{label}</div>
      <div className="mt-1 font-display text-xl font-extrabold uppercase tracking-tight text-ink">{value}</div>
      {sub && <div className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.2em] text-ink/60">{sub}</div>}
    </div>
  );
}

function DarkStat({ label, value, sub, valueClass = '' }: { label: string; value: string; sub?: string; valueClass?: string }) {
  return (
    <div className="landing-dark-stat bg-ink p-3">
      <div className="font-mono text-[9px] uppercase tracking-[0.3em] text-paper/50">{label}</div>
      <div className={`mt-1 inline-block whitespace-nowrap font-display text-base font-extrabold uppercase leading-none tracking-tight text-paper ${valueClass}`}>
        {value}
      </div>
      {sub && <div className="mt-1 font-mono text-[9px] uppercase tracking-[0.2em] text-paper/50">{sub}</div>}
    </div>
  );
}

// CSS-only mock probability heatmap (looks like a hazard tile band).
function ProbabilityTile() {
  // Generate a deterministic 14x6 grid of probability values clustered in a blob.
  const cols = 14;
  const rows = 6;
  const cells: number[] = [];
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const cx = 8;
      const cy = 3;
      const dist = Math.sqrt((c - cx) * (c - cx) * 0.6 + (r - cy) * (r - cy));
      // Off-center blob fades out with distance.
      const base = Math.max(0, 1 - dist / 4.5);
      // Add a deterministic ripple so it looks scientific, not perfect.
      const ripple = ((c * 7 + r * 11) % 13) / 80;
      cells.push(Math.min(1, Math.max(0, base + ripple - 0.05)));
    }
  }
  const colorFor = (v: number) => {
    if (v < 0.05) return 'bg-paper/10';
    if (v < 0.12) return 'bg-risk-tstm/70';
    if (v < 0.22) return 'bg-risk-mrgl/80';
    if (v < 0.36) return 'bg-risk-slgt/85';
    if (v < 0.52) return 'bg-risk-enh/90';
    if (v < 0.7) return 'bg-risk-mod';
    return 'bg-risk-high';
  };
  return (
    <div
      className="landing-probability-grid grid gap-px border-[2px] border-paper/30 bg-paper/20 p-px"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
      aria-hidden
    >
      {cells.map((v, i) => (
        <div key={i} className={`landing-heat-cell aspect-square ${colorFor(v)}`} style={heatDelay(i, cols)} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: live ticker band
// ---------------------------------------------------------------------------

function LiveTickerBand() {
  const items = [
    '► 12Z CYCLE · 49 OUTLOOKS PUBLISHED',
    '► HAZARD PROBABILITY HEADS · ACTIVE',
    '► SPC QC · AGREEMENT + DISPLACEMENT + LEDGER',
    '► 2026 RISK ARCHIVE · ENH+ VERIFICATION MAPS',
    '► SPC HAZARD OUTLOOKS · TORN / HAIL / WIND',
    '► OVERLAY COMPARE · AUTO / SPC / QC HATCH',
    '► PROVIDER CHAIN: LIVE → FALLBACK → MOCK',
    '► MAIN HAZARD · TORNADO · CONF 72%',
    '► RUN-LOCK CLEAR · NEXT REFRESH 27 MIN',
    '► CYCLE COMPLETE · F00–F48 READY',
    '► GRID STRIDE 2 · TILE STRIDE 1',
  ];
  const span = (
    <div className="flex shrink-0">
      {items.map((t, i) => (
        <span key={i} className="px-6 py-2">
          {t}
        </span>
      ))}
    </div>
  );
  return (
    // Continuously-looping decorative marquee (Decorative_Animation per the
    // design glossary): the `animate-ticker` motion loops forever and the
    // content is duplicated solely for a seamless scroll. It carries no
    // controls and no unique information, so the host is marked decorative
    // (`aria-hidden`) to exclude it from the accessibility tree (Req 4.5 /
    // Property 8) and to avoid screen readers announcing the duplicated band.
    <div className="landing-ticker border-b-[3px] border-ink bg-ink text-paper/80" aria-hidden>
      <div className="overflow-hidden font-mono text-[11px] uppercase tracking-[0.3em]">
        <div className="flex animate-ticker whitespace-nowrap">
          {span}
          {span}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: risk ramp
// ---------------------------------------------------------------------------

// One-line ramp descriptors (concise companions to the verbose RISK_DESCRIPTORS
// used elsewhere) so the ascending ladder stays scannable at small type sizes.
const RISK_SHORT: Record<RiskCategory, string> = {
  TSTM: 'Non-severe storms; lightning, gusty winds.',
  MRGL: 'Isolated, short-lived severe storms.',
  SLGT: 'Scattered severe storms.',
  ENH: 'Numerous, more persistent severe storms.',
  MOD: 'Widespread, long-track or intense storms.',
  HIGH: 'Severe outbreak; long-track tornadoes.',
};

function RiskRamp() {
  return (
    <section className="border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <div className="landing-reveal" data-landing-reveal="true">
          <SectionHead tag="RAMP / 02" title="Six categories. One ladder." />
          <p className="mt-4 max-w-[680px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
            The SPC categorical ladder, climbing from general thunder to a severe outbreak.
            Each rung marks where <em>that</em> category is the highest applicable risk.
          </p>
        </div>

        {/*
          Ascending ladder (Req 2.5 fixed order): on desktop the categories
          render as a flex row aligned to a common baseline, with each rung's
          height growing by severity (via the `--rung` custom property consumed
          only at `lg`) so the colored bars literally climb against the ink
          "sky" above them. Below `lg` they fall back to an equal-height grid so
          the layout stays single/!two-column and unclipped on small screens.
        */}
        <div className="mt-10 flex flex-col gap-px border-[3px] border-ink bg-ink sm:grid sm:grid-cols-3 lg:flex lg:flex-row lg:items-end lg:gap-0 lg:border-0 lg:bg-transparent">
          {RISK_ORDER.map((code) => {
            const meta = RISK_META[code];
            return (
              <div
                key={code}
                className={`landing-risk-card landing-reveal relative flex flex-col p-4 transition-[filter] duration-200 hover:brightness-110 lg:min-h-[var(--rung)] lg:flex-1 lg:border-[3px] lg:border-ink lg:-ml-[3px] lg:first:ml-0 ${meta.tw}`}
                data-landing-reveal="true"
                style={{ ...revealDelay(90 + meta.ord * 55), ['--rung' as string]: `${188 + meta.ord * 34}px` }}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[10px] font-bold uppercase tracking-[0.3em] opacity-80">
                    {String(meta.ord + 1).padStart(2, '0')} / 06
                  </span>
                  <span className="font-mono text-[10px] font-bold uppercase tracking-[0.3em] opacity-80">
                    {meta.chipText}
                  </span>
                </div>
                <div className="mt-auto pt-8">
                  <div className="font-display text-2xl font-extrabold uppercase leading-none tracking-tight xl:text-3xl">
                    {meta.label}
                  </div>
                  <p className="mt-2 font-sans text-xs leading-snug opacity-90">
                    {RISK_SHORT[code]}
                  </p>
                </div>
              </div>
            );
          })}
        </div>

        {/* Slim baseline rail — the severity "ground" the ladder stands on. */}
        <div className="landing-reveal mt-px grid grid-cols-6 border-x-[3px] border-b-[3px] border-ink" data-landing-reveal="true" style={revealDelay(280)}>
          {RISK_ORDER.map((code) => (
            <div key={code} className={`landing-ramp-segment ${RISK_META[code].tw} h-2`} aria-hidden />
          ))}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: capabilities bento
// ---------------------------------------------------------------------------

function CapabilitiesBento() {
  const trackRef = useRef<HTMLUListElement | null>(null);
  const pausedRef = useRef(false);
  const reducedMotion = useReducedMotion();

  // Auto-advance the carousel one card at a time, looping back to the start at
  // the end. Paused on hover/focus-within (set via pausedRef) so reading isn't
  // interrupted, and disabled entirely under reduced motion (Req 4.1) — the
  // track stays fully swipe/keyboard scrollable in that case.
  useEffect(() => {
    if (reducedMotion) return undefined;
    const track = trackRef.current;
    if (!track || typeof window === 'undefined') return undefined;

    const id = window.setInterval(() => {
      if (pausedRef.current) return;
      const first = track.querySelector<HTMLElement>('[data-capability-card]');
      const amount = first ? first.offsetWidth + 20 : track.clientWidth * 0.8;
      const atEnd = track.scrollLeft + track.clientWidth >= track.scrollWidth - 4;
      // At the end, sweep smoothly back to the start — the scroll-driven effect
      // below makes the fallen cards rise back up as the track returns.
      track.scrollTo({ left: atEnd ? 0 : track.scrollLeft + amount, behavior: 'smooth' });
    }, 3200);

    return () => window.clearInterval(id);
  }, [reducedMotion]);

  // Scroll-driven "falling" effect: as a card scrolls past the left edge it
  // translates down + rotates slightly + fades, like it's dropping away. As the
  // track scrolls back (including the loop-to-start sweep) the same math runs in
  // reverse, so cards rise smoothly back up. Disabled under reduced motion,
  // where cards are left untransformed (Req 4.1/7.1 — transform/opacity only).
  useEffect(() => {
    const track = trackRef.current;
    if (!track || typeof window === 'undefined') return undefined;

    const cards = () => Array.from(track.querySelectorAll<HTMLElement>('[data-capability-card]'));

    if (reducedMotion) {
      cards().forEach((card) => {
        card.style.transform = '';
        card.style.opacity = '';
      });
      return undefined;
    }

    let raf = 0;
    const update = () => {
      raf = 0;
      const trackLeft = track.getBoundingClientRect().left;
      cards().forEach((card) => {
        const r = card.getBoundingClientRect();
        const x = r.left - trackLeft; // distance of card's left from track's left edge
        if (x < 0) {
          const p = Math.min(1, -x / r.width); // 0 → 1 as the card exits left
          card.style.transform = `translateY(${(p * 56).toFixed(1)}px) rotate(${(p * -3).toFixed(2)}deg)`;
          card.style.opacity = (1 - p).toFixed(3);
        } else {
          card.style.transform = '';
          card.style.opacity = '';
        }
      });
    };
    const onScroll = () => {
      if (!raf) raf = window.requestAnimationFrame(update);
    };

    update();
    track.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      track.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, [reducedMotion]);

  const pause = () => {
    pausedRef.current = true;
  };
  const resume = () => {
    pausedRef.current = false;
  };

  return (
    <section id="capabilities" className="scroll-mt-20 border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <div className="landing-reveal" data-landing-reveal="true">
          <SectionHead tag="CAPABILITIES / 03" title="An operations console, not a chart." />
          <p className="mt-4 max-w-[640px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
            Every panel answers one operational question. The console cycles automatically — hover to pause.
          </p>
        </div>

        {/*
          Auto-advancing scroll-snap carousel with a "peek + fall" motion: the
          next card peeks on the right and is softened by the right-edge mask,
          while cards exiting on the left drop down and fade via the scroll-
          driven effect above. Extra bottom padding gives the falling cards room
          so they aren't clipped. Overflow stays within this track, never the
          page body (Req 6.1).
        */}
        <ul
          ref={trackRef}
          onMouseEnter={pause}
          onMouseLeave={resume}
          onFocusCapture={pause}
          onBlurCapture={resume}
          className="landing-reveal mt-8 flex snap-x snap-mandatory gap-5 overflow-x-auto pb-16 pr-[18%] [scrollbar-width:none] [mask-image:linear-gradient(to_right,#000_0,#000_72%,transparent_98%)] [-webkit-mask-image:linear-gradient(to_right,#000_0,#000_72%,transparent_98%)] [&::-webkit-scrollbar]:hidden"
          data-landing-reveal="true"
          style={revealDelay(120)}
        >
          {CAPABILITIES.map((c) => (
            <li
              key={c.tag}
              data-capability-card
              className="retro-card landing-card-motion group relative flex w-[270px] shrink-0 snap-start flex-col p-5 transition-[transform,opacity] duration-300 ease-out will-change-[transform,opacity] sm:w-[300px]"
            >
              <div className="flex items-center justify-between">
                <span className={`inline-block h-3 w-3 border-[2px] border-ink ${c.accent}`} aria-hidden />
                <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/60">[ {c.tag} ]</span>
              </div>
              <h3 className="mt-5 font-display text-2xl font-extrabold uppercase leading-tight tracking-tight">
                {c.title}
              </h3>
              <p className="mt-3 font-sans text-sm leading-relaxed text-ink/70">{c.body}</p>
              <div className="mt-auto flex items-center justify-between border-t-[2px] border-ink/15 pt-3 font-mono text-[10px] uppercase tracking-[0.3em] text-ink/60">
                <span>► PANEL · LIVE</span>
                <span>{c.tag}</span>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: pipeline / how it works
// ---------------------------------------------------------------------------

function HowItWorks() {
  return (
    <section id="pipeline" className="scroll-mt-20 relative border-b-[3px] border-ink bg-ink text-paper retro-scanline">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <div className="landing-reveal" data-landing-reveal="true">
          <SectionHead tag="PIPELINE / 04" title="From raw model data to verified outlook." dark />
          <p className="mt-4 max-w-[760px] font-sans text-base leading-relaxed text-paper/70 sm:text-lg">
            Each fixed HRRR cycle becomes a finished f00–f48 artifact. Trained thunder feeds TSTM,
            severe hazards feed MRGL and up, and the same path backs the 21 archive cases.
          </p>
        </div>

        {/*
          Compact horizontal flow rail (Req 2.5/2.6 fixed order + concise copy):
          each step is a slim node — amber index, mono label, one-line title —
          connected left-to-right by arrow glyphs on desktop and stacked on
          mobile. Step bodies are dropped here in favor of a single title line
          to keep the section scannable.
        */}
        <ol
          className="landing-reveal mt-10 grid grid-cols-1 items-stretch gap-px border-[3px] border-ink bg-ink sm:grid-cols-2 lg:grid-cols-[repeat(5,1fr)]"
          data-landing-reveal="true"
          style={revealDelay(90)}
        >
          {PIPELINE_STEPS.map((step) => (
            <li
              key={step.code}
              className="landing-pipeline-step group relative flex items-start gap-3 bg-ink p-4"
            >
              <span className="font-display text-3xl font-extrabold leading-none tracking-tight text-signal-amber">
                {step.code}
              </span>
              <div className="flex min-w-0 flex-col">
                <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/50">
                  {step.label}
                </span>
                <h3 className="mt-1 font-display text-sm font-extrabold uppercase leading-tight tracking-tight text-paper">
                  {step.title}
                </h3>
              </div>
            </li>
          ))}
        </ol>

        {/* leakage guard — slim inline strip */}
        <div
          className="landing-reveal mt-6 flex flex-wrap items-center gap-x-3 gap-y-1 border-[3px] border-signal-red bg-ink px-4 py-2.5"
          data-landing-reveal="true"
          style={revealDelay(180)}
        >
          <span className="font-mono text-[11px] font-bold uppercase tracking-[0.25em] text-signal-red">
            ⚠ Leakage guard
          </span>
          <span className="font-sans text-sm leading-snug text-paper/80">
            Predictions publish <span className="font-bold text-signal-amber">first</span>; SPC Day 1 is fetched
            <span className="font-bold text-signal-amber"> after</span>, for verification only.
          </span>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: hazards
// ---------------------------------------------------------------------------

// Per-hazard accent + glyph styling so the four hazard heads read apart at a
// glance instead of as four identical dark blocks.
const HAZARD_ACCENT: Record<HazardKey, { bar: string; glyphBg: string; glyphText: string }> = {
  tornado: { bar: 'bg-signal-red', glyphBg: 'bg-signal-red', glyphText: 'text-paper' },
  hail: { bar: 'bg-signal-cyan', glyphBg: 'bg-signal-cyan', glyphText: 'text-ink' },
  wind: { bar: 'bg-signal-amber', glyphBg: 'bg-signal-amber', glyphText: 'text-ink' },
  flood: { bar: 'bg-signal-lime', glyphBg: 'bg-signal-lime', glyphText: 'text-ink' },
};

// Escalating risk-ramp colors used to render probability bands as a visual
// scale rather than plain "2 / 5 / 10 …" text.
const PROB_RAMP = ['bg-risk-tstm', 'bg-risk-mrgl', 'bg-risk-slgt', 'bg-risk-enh', 'bg-risk-mod', 'bg-risk-high'];

function probChip(i: number, n: number): { bg: string; text: string } {
  const idx = n <= 1 ? 0 : Math.min(PROB_RAMP.length - 1, Math.round((i / (n - 1)) * (PROB_RAMP.length - 1)));
  return { bg: PROB_RAMP[idx], text: idx >= 4 ? 'text-paper' : 'text-ink' };
}

// Short labels for the categorical flood band so they fit under the bar graph.
const FLOOD_ABBREV: Record<string, string> = {
  Marginal: 'MRGL',
  Slight: 'SLGT',
  Moderate: 'MOD',
  High: 'HIGH',
};

// Line-art hazard icons (stroke = currentColor, so they inherit the accent
// foreground color). Replaces the previous emoji glyphs.
function HazardIcon({ name, className = '' }: { name: HazardKey; className?: string }) {
  const common = {
    className,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  };
  switch (name) {
    case 'tornado':
      return (
        <svg {...common}>
          <path d="M3 5h18" />
          <path d="M5 9h13" />
          <path d="M8 13h8" />
          <path d="M10 17h4" />
          <path d="M13 17l-1 4" />
        </svg>
      );
    case 'hail':
      return (
        <svg {...common}>
          <path d="M6 12.5a3.5 3.5 0 0 1 .4-7 5 5 0 0 1 9.6-1.3A3.5 3.5 0 0 1 17.5 12.5" />
          <circle cx="8" cy="18" r="1" />
          <circle cx="12" cy="20" r="1" />
          <circle cx="16" cy="18" r="1" />
        </svg>
      );
    case 'wind':
      return (
        <svg {...common}>
          <path d="M3 8h10a2.5 2.5 0 1 0-2.5-2.5" />
          <path d="M3 12h14a2.5 2.5 0 1 1-2.5 2.5" />
          <path d="M3 16h8a2 2 0 1 1-2 2" />
        </svg>
      );
    case 'flood':
    default:
      return (
        <svg {...common}>
          <path d="M3 8c2-1.6 4-1.6 6 0s4 1.6 6 0 4-1.6 6 0" />
          <path d="M3 13c2-1.6 4-1.6 6 0s4 1.6 6 0 4-1.6 6 0" />
          <path d="M3 18c2-1.6 4-1.6 6 0s4 1.6 6 0 4-1.6 6 0" />
        </svg>
      );
  }
}

function HazardsSection() {
  return (
    <section id="landing-hazards" className="scroll-mt-20 border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <div className="landing-reveal" data-landing-reveal="true">
          <SectionHead tag="HAZARDS / 05" title="Tornado · Hail · Wind · Flood." />
          <p className="mt-4 max-w-[680px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
            Each head publishes its own probability surface, with a SIG (significant severe) overlay where it applies.
            Bands follow the SPC convention.
          </p>
        </div>

        <div className="mt-10 grid grid-cols-1 gap-5 sm:grid-cols-2">
          {HAZARDS.map((h, idx) => {
            const meta = HAZARD_META[h.key];
            const accent = HAZARD_ACCENT[h.key];
            const numeric = h.key !== 'flood';
            const raw = h.band.replace(/%/g, '').split(/[/·]/).map((s) => s.trim()).filter(Boolean);
            const bars = numeric
              ? raw.map((s) => ({ label: `${s}%`, value: Number(s) }))
              : raw.map((s, i) => ({ label: FLOOD_ABBREV[s] ?? s.toUpperCase(), value: i + 1 }));
            const maxValue = Math.max(...bars.map((b) => b.value), 1);
            return (
              <article
                key={h.key}
                className="retro-card landing-card-motion landing-reveal relative flex flex-col overflow-hidden p-0"
                data-landing-reveal="true"
                style={revealDelay(90 + idx * 65)}
              >
                {/* Accent stripe — distinguishes each hazard at a glance. */}
                <span className={`${accent.bar} h-1.5 w-full`} aria-hidden />

                <div className="flex items-center justify-between gap-3 border-b-[3px] border-ink px-4 py-3">
                  <div className="flex items-center gap-3">
                    <span className={`inline-flex h-11 w-11 items-center justify-center border-[2px] border-ink ${accent.glyphBg} ${accent.glyphText}`}>
                      <HazardIcon name={h.key} className="h-6 w-6" />
                    </span>
                    <div className="flex flex-col leading-none">
                      <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/60">Hazard head</span>
                      <span className="mt-1 font-display text-xl font-extrabold uppercase tracking-tight text-ink">
                        {meta.label}
                      </span>
                    </div>
                  </div>
                  <RetroBadge tone={numeric ? 'red' : 'cyan'}>{numeric ? 'ML' : 'RULE'}</RetroBadge>
                </div>

                <div className="flex flex-1 flex-col gap-4 p-4">
                  <div>
                    <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/60">Probability bands</div>
                    {/* Mini bar graph: bar height scales with the band value. */}
                    <div className="mt-3 flex items-end gap-1.5">
                      {bars.map((b, i) => {
                        const c = probChip(i, bars.length);
                        const heightPct = Math.max(12, Math.round((b.value / maxValue) * 100));
                        return (
                          <div key={`${b.label}-${i}`} className="flex flex-1 flex-col items-center">
                            <div className="flex h-16 w-full items-end">
                              <div
                                className={`w-full border-[2px] border-ink ${c.bg}`}
                                style={{ height: `${heightPct}%` }}
                              />
                            </div>
                            <span className="mt-1 font-mono text-[9px] font-bold leading-none text-ink/70">
                              {b.label}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  <p className="mt-auto border-t-[2px] border-ink/15 pt-3 font-sans text-sm leading-snug text-ink/75">
                    {h.copy}
                  </p>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: SPC verification / QC feature
// ---------------------------------------------------------------------------

// QC breakdown of how the outlook compares to the official SPC Day 1 (mock,
// illustrative values). Agreement + under + over sum to 100%.
const QC_BREAKDOWN: { label: string; pct: number; bar: string }[] = [
  { label: 'Agreement', pct: 35, bar: 'bg-signal-lime' },
  { label: 'Underforecast', pct: 40, bar: 'bg-signal-amber' },
  { label: 'Overforecast', pct: 25, bar: 'bg-signal-red' },
];

// Per-category ledger: AutoOutlook count vs official SPC count (mock).
const QC_LEDGER: { code: RiskCategory; ao: number; spc: number }[] = [
  { code: 'TSTM', ao: 5, spc: 5 },
  { code: 'MRGL', ao: 4, spc: 4 },
  { code: 'SLGT', ao: 3, spc: 2 },
  { code: 'ENH', ao: 2, spc: 3 },
  { code: 'MOD', ao: 1, spc: 1 },
  { code: 'HIGH', ao: 0, spc: 0 },
];

const VERIFY_POINTS: { k: string; v: string }[] = [
  { k: 'Agreement readout', v: 'How much of the outlook overlaps the official SPC Day 1.' },
  { k: 'Displacement ratio', v: 'How far off spatially, normalized by area.' },
  { k: 'Leakage guard', v: 'SPC is fetched only after publish — it never feeds the model.' },
  { k: 'Category ledger', v: 'Per-category over/under counts at a glance.' },
];

function SpcVerificationFeature() {
  return (
    <section id="verification-feature" className="scroll-mt-20 border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <div className="grid grid-cols-1 items-center gap-10 lg:grid-cols-2">
          {/* Copy + highlights */}
          <div className="landing-reveal" data-landing-reveal="true">
            <SectionHead tag="VERIFICATION / 06" title="Graded against the official SPC." />
            <p className="mt-4 max-w-[560px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
              Every forecast is cross-checked against the official SPC Day 1 outlook — purely for verification.
              Predictions publish first; SPC is fetched after and never feeds back into the model.
            </p>
            <ul className="mt-6 flex flex-col gap-px border-[3px] border-ink bg-ink">
              {VERIFY_POINTS.map((p) => (
                <li key={p.k} className="flex flex-col gap-1 bg-paper p-4 sm:flex-row sm:items-baseline sm:gap-4">
                  <span className="font-display text-sm font-extrabold uppercase tracking-tight text-ink sm:w-44 sm:shrink-0">
                    {p.k}
                  </span>
                  <span className="font-sans text-sm leading-snug text-ink/70">{p.v}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* QC console mock */}
          <div className="landing-reveal" data-landing-reveal="true" style={revealDelay(120)}>
            <div className="qc-glow-border shadow-retro-lg">
              <div className="relative z-[1] overflow-hidden bg-ink p-5 text-paper">
                <div className="flex items-center justify-between border-b-[3px] border-paper/15 pb-3">
                  <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">SPC QC · DAY 1</span>
                  <RetroBadge tone="lime">SAMPLE n=18</RetroBadge>
                </div>

                <div className="mt-4 flex items-end gap-3">
                  <span className="font-display text-6xl font-extrabold leading-none tracking-tight text-signal-lime">35%</span>
                  <span className="pb-1 font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">mean agreement</span>
                </div>

                <div className="mt-5 flex flex-col gap-2">
                  {QC_BREAKDOWN.map((b) => (
                    <div key={b.label}>
                      <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.25em] text-paper/60">
                        <span>{b.label}</span>
                        <span>{b.pct}%</span>
                      </div>
                      <div className="mt-1 h-3 w-full border-[2px] border-paper/20 bg-paper/5">
                        <div className={`h-full ${b.bar}`} style={{ width: `${b.pct}%` }} />
                      </div>
                    </div>
                  ))}
                </div>

                <div className="mt-5 border-t-[3px] border-paper/15 pt-3">
                  <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">Category ledger · AO / SPC</div>
                  <div className="mt-2 grid grid-cols-3 gap-x-4 gap-y-1.5 sm:grid-cols-6">
                    {QC_LEDGER.map((r) => (
                      <div key={r.code} className="flex items-center gap-1.5">
                        <span className={`inline-block h-2.5 w-2.5 border border-paper/30 ${RISK_META[r.code].tw.split(' ')[0]}`} aria-hidden />
                        <span className="font-mono text-[10px] font-bold text-paper/80">{r.code}</span>
                        <span className="ml-auto font-mono text-[10px] text-paper/60">{r.ao}/{r.spc}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: tech stack
// ---------------------------------------------------------------------------

function TechStack() {
  return (
    <section id="stack" className="scroll-mt-20 border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <div className="landing-reveal" data-landing-reveal="true">
          <SectionHead tag="STACK / 07" title="Boring tools. Loud results." />
          <p className="mt-4 max-w-[760px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
            AutoOutlook is built on widely-deployed primitives so the operations posture stays simple.
            Vite + React + TypeScript power the interactive console. Every outlook ships as a pre-built bundle
            — risk polygons, probability tiles, QC metadata, and archive records land together as versioned artifacts.
          </p>
        </div>

        {/* Grouped tech layers — labeled rows of pills. */}
        <div className="mt-10 flex flex-col gap-px border-[3px] border-ink bg-ink">
          {TECH_GROUPS.map((g) => (
            <div key={g.label} className="flex flex-col gap-3 bg-paper p-4 sm:flex-row sm:items-center sm:gap-5">
              <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50 sm:w-32 sm:shrink-0">
                {g.label}
              </span>
              <div className="flex flex-wrap gap-2">
                {g.items.map((t, idx) => (
                  <span
                    key={t}
                    className="landing-tech-pill landing-reveal border-[2px] border-ink bg-paper px-3 py-1.5 font-mono text-[11px] font-bold uppercase tracking-[0.25em] shadow-retro-sm transition-transform hover:-translate-y-0.5"
                    data-landing-reveal="true"
                    style={revealDelay(40 + idx * 24)}
                  >
                    {t}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-10 grid grid-cols-1 gap-5 sm:grid-cols-2 md:grid-cols-3">
          <FactCard
            k="49"
            label="Forecast hours"
            sub="f00–f48 hourly outlooks"
            visual={
              <div className="flex h-7 w-full items-end gap-[3px]" aria-hidden>
                {Array.from({ length: 13 }).map((_, i) => (
                  <span
                    key={i}
                    className={`w-full ${i % 4 === 0 ? 'bg-signal-amber' : 'bg-ink/25'}`}
                    style={{ height: `${35 + (i / 12) * 65}%` }}
                  />
                ))}
              </div>
            }
          />
          <FactCard
            k="6"
            label="Risk categories"
            sub="TSTM → HIGH ladder"
            visual={
              <div className="flex w-full gap-1" aria-hidden>
                {RISK_ORDER.map((code) => (
                  <span key={code} className={`h-3 flex-1 border border-ink ${RISK_META[code].tw.split(' ')[0]}`} />
                ))}
              </div>
            }
          />
          <FactCard
            k="3"
            label="SPC compare modes"
            sub="Auto · SPC · overlay QC"
            visual={
              <div className="flex flex-wrap gap-1.5" aria-hidden>
                {[
                  { m: 'AUTO', bg: 'bg-signal-lime' },
                  { m: 'SPC', bg: 'bg-signal-cyan' },
                  { m: 'OVERLAY', bg: 'bg-signal-amber' },
                ].map((c) => (
                  <span
                    key={c.m}
                    className={`border-[2px] border-ink px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.2em] text-ink ${c.bg}`}
                  >
                    {c.m}
                  </span>
                ))}
              </div>
            }
          />
        </div>
      </div>
    </section>
  );
}

function FactCard({ k, label, sub, visual }: { k: string; label: string; sub: string; visual?: ReactNode }) {
  return (
    <div className="retro-card landing-card-motion landing-reveal flex h-full flex-col p-5" data-landing-reveal="true">
      <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/60">{label}</div>
      <div className="mt-2 font-display font-extrabold uppercase leading-none tracking-[-0.03em]" style={{ fontSize: 'clamp(2.5rem, 6vw, 4.5rem)' }}>
        {k}
      </div>
      {visual && <div className="mt-4 flex h-8 items-end">{visual}</div>}
      <div className="mt-auto pt-4 font-mono text-[11px] uppercase tracking-[0.25em] text-ink/60">{sub}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: final CTA
// ---------------------------------------------------------------------------

function FinalCTA({ reducedMotion = false }: { reducedMotion?: boolean }) {
  return (
    <section className="landing-atmosphere relative overflow-hidden border-b-[3px] border-ink bg-paper">
      <div className="pointer-events-none absolute inset-0 retro-grid-bg opacity-60" aria-hidden />
      {!reducedMotion && <div className="landing-drift-field" aria-hidden />}
      <div className="relative mx-auto max-w-[1400px] px-4 py-16 sm:px-6 lg:py-24">
        <div className="retro-card-lg retro-scanline landing-final-card landing-reveal relative overflow-hidden bg-ink p-8 text-paper sm:p-12" data-landing-reveal="true">
          {!reducedMotion && (
            <>
              <div className="landing-panel-glow" aria-hidden />
              <div className="landing-sweep-line" aria-hidden />
            </>
          )}
          <CornerMarks />
          <div className="flex flex-wrap items-center gap-2">
            <RetroBadge tone="lime" pulse>READY</RetroBadge>
            <RetroBadge tone="paper">CONUS · F00–F48</RetroBadge>
            <RetroBadge tone="amber">v1.2</RetroBadge>
          </div>

          <h2
            className="mt-6 font-display font-extrabold uppercase leading-[0.85] tracking-[-0.04em]"
            style={{ fontSize: 'clamp(2.5rem, 8vw, 6.5rem)' }}
          >
            Launch the<br />
            <span className="text-signal-amber">outlook console.</span>
          </h2>

          <p className="mt-6 max-w-[640px] font-sans text-base leading-relaxed text-paper/75 sm:text-lg">
            No sign-up. No tour. The dashboard auto-loads the latest cycle, renders the outlook, and gives the
            SPC agreement panel enough detail to see where AutoOutlook matched, missed, or overcalled the Day 1.
            The 2026 archive keeps 21 retrained full-day ENH+ verification maps available from the same map controls.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <a
              href="#dashboard"
              onClick={go('#dashboard')}
              className="retro-button !border-paper !bg-signal-amber !text-ink !px-6 !py-3 text-base"
            >
              Launch Dashboard ▸
            </a>
            <a
              href="#docs"
              onClick={go('#docs')}
              className="retro-button !border-paper !bg-transparent !text-paper !px-6 !py-3 text-base hover:!bg-paper hover:!text-ink"
            >
              Read the Docs
            </a>
            <a
              href="#docs-enh-verification"
              onClick={go('#docs-enh-verification')}
              className="retro-button !border-paper !bg-transparent !text-paper !px-6 !py-3 text-base hover:!bg-paper hover:!text-ink"
            >
              Open 2026 Risk Archive
            </a>
            <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/50">
              ► EDUCATIONAL · NOT AN OFFICIAL FORECAST
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: sponsor
// ---------------------------------------------------------------------------

function OpenFetchSponsor() {
  return (
    <section className="border-b-[3px] border-ink bg-signal-amber">
      <div className="landing-reveal mx-auto grid max-w-[1400px] grid-cols-1 gap-px border-x-[3px] border-ink bg-ink sm:grid-cols-[1fr_auto]" data-landing-reveal="true">
        <div className="bg-signal-amber px-4 py-8 sm:px-6 lg:px-8">
          <div className="flex flex-wrap items-center gap-2">
            <RetroBadge tone="ink">Sponsor Repository</RetroBadge>
            <RetroBadge tone="paper">OpenFetch</RetroBadge>
          </div>
          <h2 className="mt-4 font-display text-3xl font-extrabold uppercase leading-none tracking-tight text-ink sm:text-5xl">
            Support OpenFetch.
          </h2>
          <p className="mt-4 max-w-[720px] font-sans text-base leading-relaxed text-ink/75 sm:text-lg">
            OpenFetch is our companion repository for fast, practical fetch tooling. Sponsor, star, or inspect the source from the AutoOutlook landing page footer.
          </p>
        </div>
        <div className="flex min-h-44 flex-col justify-between bg-paper p-5 sm:min-w-[360px]">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/60">Repository</div>
            <div className="mt-2 break-all font-mono text-sm font-bold uppercase leading-relaxed text-ink">
              github.com/ShianMike/OpenFetch
            </div>
          </div>
          <a
            href="https://github.com/ShianMike/OpenFetch"
            target="_blank"
            rel="noreferrer"
            className="retro-button retro-button-primary mt-5 w-fit !px-5 !py-3 text-sm"
          >
            Open Repository
          </a>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: footer
// ---------------------------------------------------------------------------

function LandingFooter() {
  // In-page anchors route through the same guarded scroll helper as the nav so
  // a missing target retains scroll position and surfaces an accessible
  // "section unavailable" status (Req 2.3, 2.4); cross-view links use `go`.
  const { unavailableId, onSectionLink } = useGuardedSectionNav();
  return (
    // Footer (contentinfo) landmark with a non-empty accessible name (Req 5.3).
    <footer aria-label="Site footer" className="border-t-[3px] border-ink bg-ink text-paper">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
          AutoOutlook · Automated Convective Risk Intelligence · v1.2.3
        </span>
        <nav aria-label="Footer" className="flex flex-wrap items-center gap-4 font-mono text-[10px] uppercase tracking-[0.3em] text-paper/40">
          <a href="#dashboard" onClick={go('#dashboard')} className="hover:text-paper">Dashboard</a>
          <a href="#docs-enh-verification" onClick={go('#docs-enh-verification')} className="hover:text-paper">2026 Risk Archive</a>
          <a href="#docs" onClick={go('#docs')} className="hover:text-paper">Docs</a>
          <a href="#changelog" onClick={go('#changelog')} className="hover:text-paper">Changelog</a>
          <a href="https://github.com/ShianMike/OpenFetch" target="_blank" rel="noreferrer" className="hover:text-paper">OpenFetch</a>
          <a href="#capabilities" onClick={onSectionLink('#capabilities')} className="hover:text-paper">Capabilities</a>
          <span>LIVE → FALLBACK → MOCK</span>
        </nav>
      </div>
      {/* Transient in-page-navigation status mirroring the nav (Req 2.4). */}
      <div role="status" aria-live="polite" className="mx-auto max-w-[1400px] px-4 sm:px-6">
        {unavailableId && (
          <div className="mb-3 inline-block border-[2px] border-paper bg-signal-amber px-3 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.25em] text-ink">
            ⚠ Section unavailable
          </div>
        )}
      </div>
    </footer>
  );
}

// ---------------------------------------------------------------------------
// Shared: section heading
// ---------------------------------------------------------------------------

// Default labels used when a caller supplies an empty/whitespace-only tag or
// title. Requirement 2.6 / Property 3 require every section heading to render
// *exactly one* non-empty tag label and *exactly one* non-empty title, so the
// component never emits an empty heading — it falls back to these constants.
const SECTION_HEAD_DEFAULT_TAG = 'SECTION';
const SECTION_HEAD_DEFAULT_TITLE = 'AutoOutlook';

// Shared section heading (Req 2.6 / Property 3).
//
// Renders exactly one non-empty tag label (the `[ … ]` mono eyebrow) and
// exactly one non-empty title (a single `<h2>`). Both inputs are trimmed and
// fall back to a non-empty default so the rendered heading always satisfies the
// "one tag + one title, both non-empty" invariant regardless of caller input.
//
// The title is always an `<h2>`: it sits one level below the single page `<h1>`
// (the hero headline) so collecting heading levels in document order never skips
// a level (Req 5.5 / Property 11).
function SectionHead({ tag, title, dark = false }: { tag: string; title: string; dark?: boolean }) {
  const tagLabel = tag.trim() || SECTION_HEAD_DEFAULT_TAG;
  const titleLabel = title.trim() || SECTION_HEAD_DEFAULT_TITLE;
  return (
    <div className="flex flex-col gap-3">
      <div className={`flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.35em] ${dark ? 'text-paper/60' : 'text-ink/60'}`}>
        <span className={`inline-block h-2 w-2 ${dark ? 'bg-signal-amber' : 'bg-ink'}`} aria-hidden />
        <span>[ {tagLabel} ]</span>
        <span className={`h-px flex-1 ${dark ? 'bg-paper/20' : 'bg-ink/15'}`} />
      </div>
      <h2
        className={`font-display font-extrabold uppercase leading-[0.95] tracking-[-0.03em] ${dark ? 'text-paper' : 'text-ink'}`}
        style={{ fontSize: 'clamp(2rem, 5vw, 4rem)' }}
      >
        {titleLabel}
      </h2>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top-level export
// ---------------------------------------------------------------------------

export default function LandingPage() {
  // Live reduced-motion preference (Req 4.1, 4.3, 4.6). Drives the reveal
  // system and gates the looping decorative-animation hosts below so motion is
  // suppressed at the source under reduced motion, complementing the
  // `prefers-reduced-motion` CSS block (which still handles any host that does
  // remain mounted).
  const reducedMotion = useReducedMotion();
  useLandingReveal(reducedMotion);

  // Reset the document vertical scroll offset to 0 on mount so the landing page
  // always opens at the hero (Req 7.2). SSR-safe (guards on `window`) and uses
  // `behavior: 'auto'` to reset instantly — well within the 100ms budget — even
  // if smooth scrolling is enabled globally later.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
  }, []);

  return (
    <div className="landing-page min-h-screen bg-paper text-ink">
      <LandingNav />
      {/* Main landmark with a non-empty accessible name (Req 5.3). */}
      <main aria-label="AutoOutlook landing">
        {/*
          Fixed section ordering (Req 2.5): the sections render in one document
          order — hero → ticker → risk ramp → capabilities → pipeline → hazards
          → provider chain → tech stack → final CTA → sponsor → footer — and that
          order is identical across every Viewport_Class. There are no
          per-breakpoint reordering utilities (no `order-*`, `flex-*-reverse`)
          anywhere in this subtree, so the reading order never changes with the
          viewport.

          Non-skipping heading levels (Req 5.5 / Property 11): the hero owns the
          single page `<h1>`; every section title is an `<h2>` (via `SectionHead`
          or a direct `<h2>` in the CTA/sponsor sections); card/step titles nest
          one level deeper as `<h3>`. Collecting heading levels in document order
          therefore yields 1 → 2 → 3 with no descending step jumping more than one
          level.
        */}
        <Hero reducedMotion={reducedMotion} />
        <LiveTickerBand />
        <RiskRamp />
        <CapabilitiesBento />
        <HowItWorks />
        <HazardsSection />
        <SpcVerificationFeature />
        <TechStack />
        <FinalCTA reducedMotion={reducedMotion} />
        <OpenFetchSponsor />
      </main>
      <LandingFooter />
    </div>
  );
}
