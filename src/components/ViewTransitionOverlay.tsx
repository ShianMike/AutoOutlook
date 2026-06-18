import { useEffect, useMemo, useState } from 'react';

import { OVERLAY_DISPLAY_MS } from './landing/landingMotionConfig';
import { useReducedMotion } from './landing/useReducedMotion';

// Brutalist boot-style overlay shown during view changes (landing / dashboard / docs).
// Mounted from `App.tsx` keyed on the active view, so the overlay remounts and
// re-runs its enter/hold/exit cycle on every navigation.

export type TransitionView = 'landing' | 'dashboard' | 'docs' | 'changelog';

type AccentTone = 'amber' | 'cyan' | 'lime';

interface ViewMeta {
  code: string;
  brand: string;
  brandAccent: string;
  title: string;
  subtitle: string;
  badge: string;
  tone: AccentTone;
  lines: string[];
}

const VIEW_META: Record<TransitionView, ViewMeta> = {
  landing: {
    code: 'AO/01',
    brand: 'Auto',
    brandAccent: 'Outlook',
    title: 'Returning home',
    subtitle: 'Convective Risk Intelligence',
    badge: '◢ HOME',
    tone: 'amber',
    lines: [
      '> RESUME PROVIDER CHAIN',
      '> RENDER LANDING DECK',
      '> BIND NAV CONTROLS',
      '> READY · v1.2',
    ],
  },
  dashboard: {
    code: 'AO/01',
    brand: 'Outlook',
    brandAccent: 'Console',
    title: 'Booting console',
    subtitle: 'Outlook · Hazards · SPC verify',
    badge: '◢ DASHBOARD',
    tone: 'amber',
    lines: [
      '> RESOLVE LATEST CYCLE',
      '> FETCH OUTLOOK BUNDLE',
      '> BIND HAZARD HEADS',
      '> MOUNT OUTLOOK MAP',
    ],
  },
  docs: {
    code: 'DOC/00',
    brand: 'Documen',
    brandAccent: 'tation',
    title: 'Loading reference',
    subtitle: 'Definitions · Skill · Notes',
    badge: '◢ DOCS',
    tone: 'cyan',
    lines: [
      '> INDEX SECTIONS',
      '> RENDER REFERENCE PAGES',
      '> BIND ANCHOR LOCKS',
      '> READY · STATIC DOC',
    ],
  },
  changelog: {
    code: 'LOG/02',
    brand: 'Patch',
    brandAccent: 'Notes',
    title: 'Loading patch notes',
    subtitle: 'v1.1 → v1.2 · release ladder',
    badge: '◢ CHANGELOG',
    tone: 'lime',
    lines: [
      '> INDEX RELEASES',
      '> DIFF v1.1 → v1.2',
      '> RESOLVE CHANGE KINDS',
      '> READY · v1.2 CURRENT',
    ],
  },
};

const TONE_CLASSES: Record<AccentTone, { fg: string; bg: string; ring: string; bar: string }> = {
  amber: {
    fg: 'text-signal-amber',
    bg: 'bg-signal-amber',
    ring: 'border-signal-amber',
    bar: 'bg-signal-amber',
  },
  cyan: {
    fg: 'text-signal-cyan',
    bg: 'bg-signal-cyan',
    ring: 'border-signal-cyan',
    bar: 'bg-signal-cyan',
  },
  lime: {
    fg: 'text-signal-lime',
    bg: 'bg-signal-lime',
    ring: 'border-signal-lime',
    bar: 'bg-signal-lime',
  },
};

// Total visible duration of the overlay before unmount. `OVERLAY_DISPLAY_MS`
// (600ms) is the single source of truth shared with the rest of the landing
// motion system; the exit fade starts early enough that the `overlay-out`
// keyframe finishes right as the overlay unmounts.
const EXIT_ANIM_MS = 280; // matches the `overlay-out` keyframe duration in tailwind.config.ts
const EXIT_AT_MS = Math.max(0, OVERLAY_DISPLAY_MS - EXIT_ANIM_MS);

interface ViewTransitionOverlayProps {
  view: TransitionView;
  // Bumped by the parent on each view change. We key the overlay on this so it
  // re-mounts every time and the enter animations replay.
  cycle: number;
}

export default function ViewTransitionOverlay({ view, cycle }: ViewTransitionOverlayProps) {
  const [active, setActive] = useState(true);
  const [exiting, setExiting] = useState(false);
  const [pct, setPct] = useState(0);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    setActive(true);
    setExiting(false);
    const exitTimer = window.setTimeout(() => setExiting(true), EXIT_AT_MS);
    const doneTimer = window.setTimeout(() => setActive(false), OVERLAY_DISPLAY_MS);
    return () => {
      window.clearTimeout(exitTimer);
      window.clearTimeout(doneTimer);
    };
  }, [cycle]);

  // Drive the load bar + percentage with requestAnimationFrame so the fill and
  // the counter stay perfectly in sync and reliably complete within the visible
  // window (before the exit fade at EXIT_AT_MS). Eased with easeOutCubic for a
  // fast-then-settle feel. Under reduced motion it snaps to 100% immediately.
  useEffect(() => {
    if (reducedMotion) {
      setPct(100);
      return undefined;
    }
    setPct(0);
    const fillMs = Math.max(300, EXIT_AT_MS - 120);
    let raf = 0;
    let start = 0;
    const step = (t: number) => {
      if (!start) start = t;
      const p = Math.min(1, (t - start) / fillMs);
      const eased = 1 - Math.pow(1 - p, 3);
      setPct(eased * 100);
      if (p < 1) raf = window.requestAnimationFrame(step);
    };
    raf = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(raf);
  }, [cycle, reducedMotion]);

  const meta = VIEW_META[view];
  const tone = TONE_CLASSES[meta.tone];
  const utc = useUtcStamp();

  const lines = useMemo(() => meta.lines, [meta.lines]);

  // Under reduced motion the overlay still presents its destination state, but
  // any continuously-looping decorative motion (radar sweep, pulsing dots,
  // blinking caret, the animated load fill) is suppressed. `loop` drops a
  // looping animation class when motion is reduced. (Req 9.3)
  const loop = (className: string) => (reducedMotion ? '' : className);

  if (!active) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={`Loading ${view}`}
      className={[
        'fixed inset-0 z-[200] flex items-center justify-center',
        'bg-ink text-paper retro-scanline',
        exiting ? 'animate-overlay-out pointer-events-none' : 'animate-overlay-in',
      ].join(' ')}
    >
      {/* Background radar sweep — pure CSS conic gradient that rotates. */}
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
        <div className="relative h-[140vmax] w-[140vmax] opacity-[0.07]">
          <div
            className={`absolute inset-0 ${loop('animate-radar-sweep')}`}
            style={{
              background:
                'conic-gradient(from 0deg, rgba(245,184,0,0) 0deg, rgba(245,184,0,0.55) 18deg, rgba(245,184,0,0) 24deg)',
            }}
            aria-hidden
          />
        </div>
      </div>

      {/* Faint grid backdrop. */}
      <div className="pointer-events-none absolute inset-0 retro-grid-bg opacity-[0.05]" aria-hidden />

      {/* Halftone dot field. */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.08]"
        style={{
          backgroundImage:
            'radial-gradient(rgba(245,241,232,0.7) 1px, transparent 1px)',
          backgroundSize: '14px 14px',
        }}
        aria-hidden
      />

      {/* Center panel */}
      <div
        className={[
          'relative w-[min(560px,92vw)] border-[4px] border-paper bg-ink p-0 shadow-[10px_10px_0_0_#f5f1e8]',
          'animate-panel-in',
        ].join(' ')}
      >
        <CornerMarks />

        {/* Top strip */}
        <div className="flex items-center justify-between border-b-[3px] border-paper/30 bg-ink px-4 py-2">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block h-2 w-2 ${loop('animate-pulse-dot')} rounded-full ${tone.bg}`}
              aria-hidden
            />
            <span className="font-mono text-[10px] uppercase tracking-[0.35em] text-paper/70">
              ◢ NAVIGATE · {meta.badge.replace('◢ ', '')}
            </span>
          </div>
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/40">
            UTC {utc}
          </span>
        </div>

        {/* Brand row */}
        <div className="flex items-center gap-3 border-b-[3px] border-paper/15 bg-ink px-4 py-3">
          <div className="border-[3px] border-paper bg-paper px-2 py-1 font-mono text-[11px] font-bold tracking-[0.3em] text-ink">
            {meta.code}
          </div>
          <div className="flex flex-col leading-none">
            <span className="font-display text-2xl font-extrabold uppercase tracking-tight">
              {meta.brand}
              <span className={tone.fg}>{meta.brandAccent}</span>
            </span>
            <span className="mt-1 font-mono text-[10px] uppercase tracking-[0.3em] text-paper/50">
              {meta.subtitle}
            </span>
          </div>
        </div>

        {/* Title + boot lines */}
        <div className="flex flex-col gap-3 border-b-[3px] border-paper/15 px-4 py-4">
          <h2 className="font-display text-3xl font-extrabold uppercase leading-[0.9] tracking-tight">
            {meta.title}
            <span className={`ml-1 inline-block w-2 ${tone.bg} ${loop('animate-blink')}`} style={{ height: '0.85em', verticalAlign: '-0.05em' }} aria-hidden />
          </h2>

          <ul className="mt-1 flex flex-col gap-1.5 font-mono text-[11px] uppercase tracking-[0.18em] text-paper/80">
            {lines.map((line, idx) => (
              <li
                key={`${cycle}-${idx}`}
                className={reducedMotion ? 'opacity-100' : 'animate-boot-line opacity-0'}
                style={reducedMotion ? undefined : { animationDelay: `${100 + idx * 200}ms` }}
              >
                {line}
              </li>
            ))}
          </ul>
        </div>

        {/* Progress bar — rAF-driven fill + live percentage. */}
        <div className="border-b-[3px] border-paper/15 px-4 py-3">
          <div className="mb-1.5 flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.3em] text-paper/50">
            <span>► LOAD STREAM</span>
            <span className={`${tone.fg} tabular-nums`}>{String(Math.round(pct)).padStart(3, '0')}%</span>
          </div>
          <div className="relative h-3 w-full border-[2px] border-paper bg-paper/10">
            <div
              className={`absolute inset-y-0 left-0 ${tone.bar}`}
              style={{ width: `${pct}%` }}
            >
              <div
                className="absolute inset-0 opacity-50"
                style={{
                  backgroundImage:
                    'repeating-linear-gradient(90deg, rgba(17,17,17,0) 0 6px, rgba(17,17,17,0.35) 6px 7px)',
                }}
                aria-hidden
              />
              {/* Bright leading edge that rides the fill. */}
              {!reducedMotion && pct > 0 && pct < 100 && (
                <span className="absolute inset-y-0 right-0 w-[3px] bg-paper" aria-hidden />
              )}
            </div>
            {/* Tick marks above the bar */}
            <div className="absolute -top-1 left-0 right-0 flex justify-between" aria-hidden>
              {Array.from({ length: 11 }).map((_, i) => (
                <span
                  key={i}
                  className={`block w-px ${i % 5 === 0 ? 'h-2 bg-paper' : 'h-1 bg-paper/50'}`}
                />
              ))}
            </div>
          </div>
        </div>

        {/* Bottom strip */}
        <div className="flex items-center justify-between bg-ink px-4 py-2 font-mono text-[10px] uppercase tracking-[0.3em] text-paper/55">
          <span className="inline-flex items-center gap-2">
            <span className={`inline-block h-1.5 w-1.5 ${loop('animate-pulse-dot')} ${tone.bg}`} aria-hidden />
            STAND BY
          </span>
          <span>v1.2 · STREAM OK</span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function CornerMarks() {
  const cls = 'absolute h-3 w-3 border-paper';
  return (
    <>
      <span aria-hidden className={`${cls} left-1.5 top-1.5 border-l-2 border-t-2`} />
      <span aria-hidden className={`${cls} right-1.5 top-1.5 border-r-2 border-t-2`} />
      <span aria-hidden className={`${cls} bottom-1.5 left-1.5 border-b-2 border-l-2`} />
      <span aria-hidden className={`${cls} bottom-1.5 right-1.5 border-b-2 border-r-2`} />
    </>
  );
}

function useUtcStamp() {
  const [stamp, setStamp] = useState(() => formatUtc(new Date()));
  useEffect(() => {
    const id = window.setInterval(() => setStamp(formatUtc(new Date())), 250);
    return () => window.clearInterval(id);
  }, []);
  return stamp;
}

function formatUtc(d: Date) {
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  const ss = String(d.getUTCSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}Z`;
}
