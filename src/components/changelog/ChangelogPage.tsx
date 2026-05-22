import { useEffect, useMemo, useState } from 'react';

import RetroBadge from '../retro/RetroBadge';
import { viewLinkHandler } from '../../utils/navigateView';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ChangeKind = 'NEW' | 'FIX' | 'IMPROVE' | 'REMOVE' | 'DOCS';
type ReleaseStatus = 'CURRENT' | 'STABLE' | 'INITIAL';

interface ChangeEntry {
  kind: ChangeKind;
  title: string;
  body: string;
}

interface VersionRelease {
  version: string;       // 'v0.2'
  codename: string;      // short title
  date: string;          // ISO date
  status: ReleaseStatus;
  summary: string;
  highlights: string[];
  changes: ChangeEntry[];
}

// ---------------------------------------------------------------------------
// Style maps
// ---------------------------------------------------------------------------

type ToneName = 'lime' | 'amber' | 'cyan' | 'red' | 'paper';

const KIND_TONE: Record<ChangeKind, ToneName> = {
  NEW:     'lime',
  FIX:     'amber',
  IMPROVE: 'cyan',
  REMOVE:  'red',
  DOCS:    'paper',
};

const KIND_GLYPH: Record<ChangeKind, string> = {
  NEW:     '+',
  FIX:     '✕',
  IMPROVE: '↑',
  REMOVE:  '−',
  DOCS:    '✎',
};

const STATUS_TONE: Record<ReleaseStatus, 'lime' | 'amber' | 'cyan'> = {
  CURRENT: 'lime',
  STABLE:  'cyan',
  INITIAL: 'amber',
};

// Tailwind JIT cannot interpolate class names at runtime, so we route every
// tone-dependent class through explicit string maps it can statically see.
const TONE_BG: Record<ToneName, string> = {
  lime:  'bg-signal-lime',
  amber: 'bg-signal-amber',
  cyan:  'bg-signal-cyan',
  red:   'bg-signal-red',
  paper: 'bg-paper',
};
const TONE_BORDER: Record<ToneName, string> = {
  lime:  'border-signal-lime',
  amber: 'border-signal-amber',
  cyan:  'border-signal-cyan',
  red:   'border-signal-red',
  paper: 'border-paper',
};
const TONE_TEXT: Record<ToneName, string> = {
  lime:  'text-ink',
  amber: 'text-ink',
  cyan:  'text-ink',
  red:   'text-paper',
  paper: 'text-ink',
};

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

const RELEASES: VersionRelease[] = [
  {
    version: 'v0.2',
    codename: 'Cleaner band rendering',
    date: '2026-05-22',
    status: 'CURRENT',
    summary:
      'Risk-band rendering pass and a product-first landing rewrite. Closes the inter-tier gap with a colored separator, kills the SLGT glow, and stops tiny high-tier features from rendering as halo artifacts.',
    highlights: [
      '5 km separator boundary now visibly colored',
      'No more green glow inside SLGT',
      'Tiny ENH/MDT specks demote one tier down',
      'Landing page reads like a product, not a stack trace',
    ],
    changes: [
      {
        kind: 'FIX',
        title: 'Removed inner separator stroke that tinted SLGT green',
        body: 'The boundary stroke between bands was rendered half-inside, half-outside each polygon. The inside half showed through the 0.48-opacity fill and tinted SLGT yellow toward green. The stroke is gone; bands now read in their true color.',
      },
      {
        kind: 'NEW',
        title: 'Per-band separator stroke to color the 5 km boundary',
        body: 'Each higher band gets a separate separator stroke rendered in the lower band\'s color. The 5 km lower-owned boundary now reads as an intentional band edge instead of a paper-white seam.',
      },
      {
        kind: 'FIX',
        title: 'Auto-demote tiny higher-tier specks',
        body: 'ENH, MDT, MOD, and HIGH features below 0.04 deg² are walked one tier down per pass until they pass threshold (or hit TSTM). Stops a stray single-cell ENH dot from rendering as a meaningless speck inside a wider SLGT region.',
      },
      {
        kind: 'FIX',
        title: 'No more concentric halo around small features',
        body: 'Tiny higher-tier features below 0.04 deg² are demoted before rendering so the separator pass does not create concentric halo artifacts around single-cell specks.',
      },
      {
        kind: 'IMPROVE',
        title: 'Polygon ring orientation normalized for hole rendering',
        body: 'Annulus geometry is built as a donut (expanded outer + original outer as inner hole). Ring orientation is normalized after construction so d3-geo paints the gap, not the inverse of the gap.',
      },
      {
        kind: 'REMOVE',
        title: 'Landing page no longer mentions backend processes or cloud providers',
        body: 'Scrubbed every reference to HRRR, XGBoost, NOMADS, MetPy, cfgrib, GRIB, Flask, Python, Cloud Run, Cloud Scheduler, and GCS. Pipeline section now reads as ingest → derive → infer → publish → verify in product-level language only.',
      },
      {
        kind: 'DOCS',
        title: 'Patch notes page',
        body: 'This page. Versioned changelog with kind-tagged entries (NEW / FIX / IMPROVE / REMOVE / DOCS), reverse-chronological, with filter chips so you can scope to just the fixes or just the new features.',
      },
    ],
  },
  {
    version: 'v0.1',
    codename: 'Initial release',
    date: '2026-05-01',
    status: 'INITIAL',
    summary:
      'First public AutoOutlook cut. Hands-off pipeline, SPC-style outlook, and an opinionated retro console UI for forecast hours f00–f48.',
    highlights: [
      'Categorical outlook map (TSTM → HIGH)',
      'Hazard probability boards for tor / hail / wind / flood',
      '3-tier provider chain with mock guard rail',
      'SPC Day 1 cross-check on a 40 km grid',
    ],
    changes: [
      {
        kind: 'NEW',
        title: 'Categorical outlook map',
        body: 'Stepped risk polygons rendered in the SPC convention. TSTM through HIGH bands as concentric annuli — never solid disks. Auto-zoomed to the region of greatest convective interest.',
      },
      {
        kind: 'NEW',
        title: 'Hazard probability boards',
        body: 'Tornado, hail, damaging wind, and excessive rainfall probability surfaces resolved per forecast hour. SIG-severe overlays activate once probabilities clear the 10% EF2+ / 2"+ / 74 mph thresholds.',
      },
      {
        kind: 'NEW',
        title: 'Forecast time scrubber · f00 – f48',
        body: 'Hourly resolution across the full extended cycle window. Play / pause animation, keyboard nav, and a verified-bundle status indicator on every hour.',
      },
      {
        kind: 'NEW',
        title: '3-tier provider chain',
        body: 'Live forecast feed → public-model fallback → deterministic mock. The chain fails downward never upward, and the source badge tells you which tier won.',
      },
      {
        kind: 'NEW',
        title: 'SPC verification',
        body: 'Forecast bundles cross-checked against the official SPC Day 1 outlook on a 40 km grid. Agreement %, underforecast cells, and overforecast cells exposed as first-class telemetry.',
      },
      {
        kind: 'NEW',
        title: 'Auto-generated forecast discussion',
        body: 'Narrative paragraph blending ingredients, composites, and storm-mode signals into operator-readable forecast prose. No LLM, no hallucinations — pure rules.',
      },
      {
        kind: 'NEW',
        title: 'Operator panels',
        body: 'Watch readiness, system status, environmental ingredients grid, risk timeline, and model audit panel. Everything you need to trust or distrust the run on sight.',
      },
      {
        kind: 'DOCS',
        title: 'Documentation set',
        body: 'SPC outlook conventions, hazard probability bands, and retro UI design language documented under the docs view.',
      },
    ],
  },
];

const CHANGE_KINDS: ChangeKind[] = ['NEW', 'FIX', 'IMPROVE', 'REMOVE', 'DOCS'];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const go = viewLinkHandler;

function useUtcClock() {
  const [now, setNow] = useState<Date>(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return useMemo(() => {
    const hh = String(now.getUTCHours()).padStart(2, '0');
    const mm = String(now.getUTCMinutes()).padStart(2, '0');
    const ss = String(now.getUTCSeconds()).padStart(2, '0');
    return `${hh}:${mm}:${ss}Z`;
  }, [now]);
}

function formatDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return iso;
  const month = date.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' }).toUpperCase();
  const day = String(date.getUTCDate()).padStart(2, '0');
  const year = date.getUTCFullYear();
  return `${month} ${day} · ${year}`;
}

function countByKind(release: VersionRelease, kind: ChangeKind): number {
  return release.changes.filter((c) => c.kind === kind).length;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ChangelogPage() {
  useEffect(() => {
    if (typeof window !== 'undefined') {
      window.scrollTo({ top: 0 });
    }
  }, []);

  const [activeFilters, setActiveFilters] = useState<Set<ChangeKind>>(new Set(CHANGE_KINDS));
  const toggleFilter = (kind: ChangeKind) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      // Never let everything be filtered out.
      if (next.size === 0) return new Set(CHANGE_KINDS);
      return next;
    });
  };

  return (
    <div className="min-h-screen bg-paper text-ink">
      <ChangelogNav />
      <main>
        <ChangelogHero />
        <FilterStrip activeFilters={activeFilters} onToggle={toggleFilter} />
        <Timeline activeFilters={activeFilters} />
        <BackLinks />
      </main>
      <ChangelogFooter />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top nav
// ---------------------------------------------------------------------------

function ChangelogNav() {
  const time = useUtcClock();
  return (
    <header className="sticky top-0 z-40 border-b-[3px] border-ink bg-paper">
      <div className="mx-auto flex max-w-[1400px] items-center gap-4 px-4 py-2.5 sm:px-6">
        <a href="#" onClick={go('')} className="flex items-center gap-3">
          <div className="border-[3px] border-ink bg-ink px-2 py-1 font-mono text-[10px] font-bold tracking-[0.3em] text-paper">
            AO/01
          </div>
          <div className="hidden flex-col leading-none sm:flex">
            <span className="font-display text-lg font-extrabold uppercase tracking-tight">
              Auto<span className="text-signal-amber">Outlook</span>
            </span>
            <span className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.25em] text-ink/60">
              Patch Notes · v0.2
            </span>
          </div>
        </a>

        <div className="hidden flex-1 items-center justify-center gap-6 md:flex">
          <a href="#" onClick={go('')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Home
          </a>
          <a href="#docs" onClick={go('#docs')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Docs
          </a>
          <a href="#dashboard" onClick={go('#dashboard')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Dashboard
          </a>
          <span className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink">
            Changelog
          </span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <div className="hidden items-center gap-2 border-[2px] border-ink bg-paper px-2 py-1 font-mono text-[10px] uppercase tracking-[0.25em] text-ink shadow-retro-sm sm:flex">
            <span className="inline-block h-2 w-2 animate-pulse-dot rounded-full bg-signal-lime" aria-hidden />
            <span>UTC {time}</span>
          </div>
          <a
            href="#dashboard"
            onClick={go('#dashboard')}
            className="retro-button retro-button-primary text-[11px]"
          >
            Launch Dashboard ▸
          </a>
        </div>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Hero
// ---------------------------------------------------------------------------

function ChangelogHero() {
  const current = RELEASES[0];
  const previous = RELEASES[1];
  return (
    <section className="relative border-b-[3px] border-ink bg-paper">
      <div className="pointer-events-none absolute inset-0 retro-grid-bg opacity-60" aria-hidden />

      <div className="relative mx-auto grid max-w-[1400px] grid-cols-1 gap-8 px-4 py-12 sm:px-6 lg:grid-cols-[1.3fr_1fr] lg:gap-10 lg:py-20">
        <div className="flex flex-col gap-6">
          <div className="flex flex-wrap items-center gap-2">
            <RetroBadge tone="ink">[ PATCH NOTES / 00 ]</RetroBadge>
            <RetroBadge tone="lime" pulse>CURRENT · {current.version}</RetroBadge>
            <RetroBadge tone="paper">Updated {formatDate(current.date)}</RetroBadge>
          </div>

          <h1
            className="font-display font-extrabold uppercase leading-[0.85] tracking-[-0.04em] text-ink"
            style={{ fontSize: 'clamp(3rem, 9vw, 7.5rem)' }}
          >
            Patch<span className="text-signal-amber">Notes</span>
          </h1>

          <p className="max-w-[640px] font-display text-xl font-bold uppercase leading-tight tracking-tight text-ink/80 sm:text-2xl lg:text-3xl">
            What shipped, what broke, what got fixed.
            <br />
            <span className="text-ink/55">{current.version} · {current.codename}.</span>
          </p>

          <p className="max-w-[640px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
            {current.summary}
          </p>

          <div className="flex flex-wrap items-center gap-3 pt-2">
            <a
              href="#release-v0-2"
              className="retro-button retro-button-primary !px-5 !py-3 text-base"
            >
              ▾ Read v0.2 in full
            </a>
            <a
              href="#release-v0-1"
              className="retro-button !px-5 !py-3 text-base"
            >
              See v0.1
            </a>
          </div>

          <dl className="mt-6 grid grid-cols-2 gap-px border-[3px] border-ink bg-ink sm:grid-cols-4">
            <HeroStat label="CURRENT" value={current.version} sub={current.codename} />
            <HeroStat label="PREVIOUS" value={previous.version} sub={previous.codename} />
            <HeroStat label="RELEASES" value={String(RELEASES.length)} sub="versions shipped" />
            <HeroStat label="CHANGES" value={String(RELEASES.reduce((sum, r) => sum + r.changes.length, 0))} sub="across all versions" />
          </dl>
        </div>

        {/* Right: version diff panel */}
        <div className="relative">
          <div className="retro-card-lg retro-scanline bg-ink p-0 text-paper">
            <CornerMarks />
            <div className="flex items-center justify-between border-b-[3px] border-paper/15 px-4 py-2">
              <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
                ◢ DIFF · {previous.version} → {current.version}
              </span>
              <div className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 animate-pulse-dot rounded-full bg-signal-lime" aria-hidden />
                <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/80">LIVE</span>
              </div>
            </div>

            <div className="px-4 py-3">
              <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/50">
                v0.2 highlights
              </span>
              <ul className="mt-3 flex flex-col gap-2">
                {current.highlights.map((h) => (
                  <li key={h} className="flex items-start gap-3">
                    <span className="mt-1 inline-block h-2 w-2 shrink-0 bg-signal-amber" aria-hidden />
                    <span className="font-sans text-sm leading-snug text-paper/90">{h}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="border-t-[3px] border-paper/15 px-4 py-3">
              <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/50">
                change breakdown · v0.2
              </span>
              <div className="mt-3 grid grid-cols-5 gap-px border-[2px] border-paper/30 bg-paper/20">
                {CHANGE_KINDS.map((kind) => {
                  const count = countByKind(current, kind);
                  return (
                    <div key={kind} className="bg-ink p-2 text-center">
                      <div className="font-mono text-[9px] uppercase tracking-[0.25em] text-paper/55">
                        {kind}
                      </div>
                      <div className="mt-1 font-display text-xl font-extrabold leading-none tracking-tight text-paper">
                        {count}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="border-t-[3px] border-paper/15 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
              ▸ READING ORDER · NEWEST FIRST
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

function HeroStat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-paper p-3">
      <div className="font-mono text-[9px] uppercase tracking-[0.3em] text-ink/50">{label}</div>
      <div className="mt-1 font-display text-xl font-extrabold uppercase tracking-tight text-ink">{value}</div>
      {sub && <div className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.2em] text-ink/50">{sub}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter strip
// ---------------------------------------------------------------------------

function FilterStrip({
  activeFilters,
  onToggle,
}: {
  activeFilters: Set<ChangeKind>;
  onToggle: (kind: ChangeKind) => void;
}) {
  return (
    <section className="border-b-[3px] border-ink bg-ink text-paper">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/55">
          ▸ FILTER ·
        </span>
        {CHANGE_KINDS.map((kind) => {
          const active = activeFilters.has(kind);
          const tone = KIND_TONE[kind];
          const baseClass =
            'inline-flex items-center gap-2 border-[2px] px-3 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.25em] transition-all';
          const onClass = `${TONE_BORDER[tone]} ${TONE_BG[tone]} ${TONE_TEXT[tone]} shadow-retro-sm`;
          const offClass = 'border-paper/30 bg-transparent text-paper/55 hover:border-paper/60 hover:text-paper';
          return (
            <button
              key={kind}
              type="button"
              onClick={() => onToggle(kind)}
              aria-pressed={active}
              className={`${baseClass} ${active ? onClass : offClass}`}
            >
              <span className="text-[12px] leading-none">{KIND_GLYPH[kind]}</span>
              <span>{kind}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

function Timeline({ activeFilters }: { activeFilters: Set<ChangeKind> }) {
  return (
    <section className="border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <SectionHead tag="RELEASES / 01" title="Versions, newest first." />
        <p className="mt-4 max-w-[760px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
          Every release ships as a single bundle — the dashboard, the docs, and the static
          outlook artifacts all move together. Major themes get codenames; everything else lands
          in NEW / FIX / IMPROVE / REMOVE / DOCS.
        </p>

        <ol className="mt-12 flex flex-col gap-12">
          {RELEASES.map((release, idx) => (
            <ReleaseCard key={release.version} release={release} index={idx} activeFilters={activeFilters} />
          ))}
        </ol>
      </div>
    </section>
  );
}

function ReleaseCard({
  release,
  index,
  activeFilters,
}: {
  release: VersionRelease;
  index: number;
  activeFilters: Set<ChangeKind>;
}) {
  const visibleChanges = release.changes.filter((c) => activeFilters.has(c.kind));
  const anchor = `release-${release.version.replace(/\./g, '-')}`;
  const statusTone = STATUS_TONE[release.status];

  return (
    <li id={anchor} className="scroll-mt-24 relative grid grid-cols-1 gap-px border-[3px] border-ink bg-ink lg:grid-cols-[280px_1fr]">
      {/* Left rail */}
      <div className="flex flex-col gap-4 bg-paper p-5">
        <div className="flex items-center gap-2">
          <RetroBadge tone={statusTone}>{release.status}</RetroBadge>
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/55">
            REL / {String(RELEASES.length - index).padStart(2, '0')}
          </span>
        </div>

        <div
          className="font-display font-extrabold uppercase leading-none tracking-[-0.03em] text-ink"
          style={{ fontSize: 'clamp(2.75rem, 5vw, 4rem)' }}
        >
          {release.version}
        </div>

        <div className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">CODENAME</span>
          <span className="font-display text-lg font-extrabold uppercase leading-tight tracking-tight text-ink">
            {release.codename}
          </span>
        </div>

        <div className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">SHIPPED</span>
          <span className="font-mono text-sm font-bold uppercase tracking-[0.1em] text-ink">
            {formatDate(release.date)}
          </span>
        </div>

        <div className="mt-1 flex flex-wrap gap-1.5">
          {CHANGE_KINDS.map((kind) => {
            const count = countByKind(release, kind);
            if (count === 0) return null;
            const tone = KIND_TONE[kind];
            return (
              <span
                key={kind}
                className={`inline-flex items-center gap-1 border-[2px] border-ink ${TONE_BG[tone]} ${TONE_TEXT[tone]} px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.2em] shadow-retro-sm`}
              >
                <span className="text-[11px] leading-none">{KIND_GLYPH[kind]}</span>
                <span>{kind}</span>
                <span className="ml-0.5">×{count}</span>
              </span>
            );
          })}
        </div>
      </div>

      {/* Right column: summary + change entries */}
      <div className="flex flex-col gap-5 bg-paper p-5 sm:p-6">
        <p className="font-sans text-base leading-relaxed text-ink/75 sm:text-lg">
          {release.summary}
        </p>

        {visibleChanges.length === 0 ? (
          <div className="border-[2px] border-dashed border-ink/30 bg-ink/[0.02] p-4 text-center font-mono text-[10px] uppercase tracking-[0.3em] text-ink/45">
            No entries match the current filter for {release.version}.
          </div>
        ) : (
          <ul className="flex flex-col gap-px border-[3px] border-ink bg-ink">
            {visibleChanges.map((change, i) => (
              <li key={`${change.kind}-${i}`} className="grid grid-cols-1 gap-px bg-ink sm:grid-cols-[96px_1fr]">
                <div className={`flex items-start justify-center ${TONE_BG[KIND_TONE[change.kind]]} px-3 py-3 sm:py-4`}>
                  <span className={`inline-flex items-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.25em] ${TONE_TEXT[KIND_TONE[change.kind]]}`}>
                    <span className="text-[12px] leading-none">{KIND_GLYPH[change.kind]}</span>
                    <span>{change.kind}</span>
                  </span>
                </div>
                <div className="bg-paper px-4 py-3 sm:px-5 sm:py-4">
                  <h3 className="font-display text-base font-extrabold uppercase leading-tight tracking-tight text-ink sm:text-lg">
                    {change.title}
                  </h3>
                  <p className="mt-2 font-sans text-sm leading-relaxed text-ink/70 sm:text-base">
                    {change.body}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Back links
// ---------------------------------------------------------------------------

function BackLinks() {
  return (
    <section className="border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-12 sm:px-6 lg:py-16">
        <div className="grid grid-cols-1 gap-px border-[3px] border-ink bg-ink md:grid-cols-3">
          <BackTile
            href="#"
            onClick={go('')}
            kicker="HOME · 00"
            title="Back to overview"
            body="Capabilities, pipeline, hazards, and stack on the landing page."
          />
          <BackTile
            href="#dashboard"
            onClick={go('#dashboard')}
            kicker="CONSOLE · 01"
            title="Launch dashboard"
            body="The actual outlook map, timelines, and hazard boards for the latest cycle."
          />
          <BackTile
            href="#docs"
            onClick={go('#docs')}
            kicker="DOCS · 02"
            title="Read the documentation"
            body="SPC outlook conventions, hazard probability formulas, and UI language."
          />
        </div>
      </div>
    </section>
  );
}

function BackTile({
  href,
  onClick,
  kicker,
  title,
  body,
}: {
  href: string;
  onClick: (e: { preventDefault: () => void }) => void;
  kicker: string;
  title: string;
  body: string;
}) {
  return (
    <a
      href={href}
      onClick={onClick}
      className="group relative flex flex-col gap-3 bg-paper p-5 transition-all hover:bg-ink hover:text-paper"
    >
      <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50 group-hover:text-paper/55">
        ◢ {kicker}
      </span>
      <span className="font-display text-2xl font-extrabold uppercase leading-tight tracking-tight">
        {title}
      </span>
      <span className="font-sans text-sm leading-relaxed text-ink/70 group-hover:text-paper/75">
        {body}
      </span>
      <span className="mt-auto font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50 group-hover:text-signal-amber">
        ▸ OPEN
      </span>
    </a>
  );
}

// ---------------------------------------------------------------------------
// Footer
// ---------------------------------------------------------------------------

function ChangelogFooter() {
  return (
    <footer className="border-t-[3px] border-ink bg-ink text-paper">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
          AutoOutlook · Automated Convective Risk Intelligence · v0.2
        </span>
        <div className="flex flex-wrap items-center gap-4 font-mono text-[10px] uppercase tracking-[0.3em] text-paper/40">
          <a href="#" onClick={go('')} className="hover:text-paper">Home</a>
          <a href="#dashboard" onClick={go('#dashboard')} className="hover:text-paper">Dashboard</a>
          <a href="#docs" onClick={go('#docs')} className="hover:text-paper">Docs</a>
          <span>LIVE → FALLBACK → MOCK</span>
        </div>
      </div>
    </footer>
  );
}

// ---------------------------------------------------------------------------
// Shared: section heading
// ---------------------------------------------------------------------------

function SectionHead({ tag, title }: { tag: string; title: string }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.35em] text-ink/55">
        <span className="inline-block h-2 w-2 bg-ink" aria-hidden />
        <span>[ {tag} ]</span>
        <span className="h-px flex-1 bg-ink/15" />
      </div>
      <h2
        className="font-display font-extrabold uppercase leading-[0.95] tracking-[-0.03em] text-ink"
        style={{ fontSize: 'clamp(2rem, 5vw, 4rem)' }}
      >
        {title}
      </h2>
    </div>
  );
}
