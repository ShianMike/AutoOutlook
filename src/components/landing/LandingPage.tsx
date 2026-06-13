import { Fragment, useEffect, useMemo, useState, type CSSProperties } from 'react';

import RetroBadge from '../retro/RetroBadge';
import { HAZARD_META, RISK_META, type HazardKey, type RiskCategory } from '../../types/forecast';
import { viewLinkHandler } from '../../utils/navigateView';

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
    body: 'Each cycle pulls the severe-weather fields used by model schema v5: CAPE, CIN, temperature, moisture, shear, reflectivity, pressure, and 500-mb height.',
  },
  {
    code: '02',
    label: 'DERIVE',
    title: 'Ingredient diagnostics',
    body: 'Bulk shear, storm-relative helicity, STP, SCP, EHI, and SHIP composites are computed across the grid. The CONUS focus region is auto-detected for downstream rendering.',
  },
  {
    code: '03',
    label: 'INFER',
    title: 'Hazard probability',
    body: 'Four calibrated XGBoost heads produce tornado, hail, damaging-wind, and general-thunder probabilities from 37 spatial, environmental, and time-season inputs.',
  },
  {
    code: '04',
    label: 'PUBLISH',
    title: 'Outlook bundle',
    body: 'SPC-style risk polygons, probability tiles, one-label CIG overlays, preview images, and run metadata are assembled into a complete bundle for forecast hours f00–f48.',
  },
  {
    code: '05',
    label: 'VERIFY',
    title: 'SPC QC cross-check',
    body: 'After publish, the official SPC Day 1 outlook is fetched purely for verification. The QC bundle exposes agreement, underforecast, overforecast, category counts, and forecaster metadata.',
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

const TECH_PILLS = [
  'VITE',
  'REACT 18',
  'TYPESCRIPT 5',
  'TAILWIND 3',
  'react-simple-maps',
  'D3-GEO',
  'GEOJSON',
  'TOPOJSON',
  'WEBP TILES',
  'SPC VERIFICATION',
  'SPC OVERLAY QC',
  '2026 RISK ARCHIVE',
  'SPC HAZARD OUTLOOKS',
  'STORM REPORTS',
  'CATEGORY LEDGER',
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function useLandingReveal() {
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;

    const targets = Array.from(document.querySelectorAll<HTMLElement>('[data-landing-reveal]'));
    if (!targets.length) return undefined;

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (prefersReducedMotion || !('IntersectionObserver' in window)) {
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
        threshold: 0.14,
      },
    );

    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, []);
}

function revealDelay(ms: number): CSSProperties {
  return { '--landing-reveal-delay': `${ms}ms` } as CSSProperties;
}

function heatDelay(index: number, cols: number): CSSProperties {
  const row = Math.floor(index / cols);
  const col = index % cols;
  return { animationDelay: `${col * 24 + row * 70}ms` };
}

// Use the shared `viewLinkHandler` so internal links/buttons all route through
// the same logic that App.tsx listens to (hashchange).
const go = viewLinkHandler;

// ---------------------------------------------------------------------------
// Section: top navigation
// ---------------------------------------------------------------------------

function LandingNav() {
  const clock = useUtcClock();
  return (
    <header className="landing-nav sticky top-0 z-40 border-b-[3px] border-ink bg-paper">
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
              Convective Risk Intelligence
            </span>
          </div>
        </a>

        <div className="hidden flex-1 items-center justify-center gap-6 md:flex">
          <a href="#capabilities" className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Capabilities
          </a>
          <a href="#pipeline" className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Pipeline
          </a>
          <a href="#landing-hazards" className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Hazards
          </a>
          <a href="#stack" className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
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
        </div>

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
    </header>
  );
}

// ---------------------------------------------------------------------------
// Section: hero
// ---------------------------------------------------------------------------

function Hero() {
  const clock = useUtcClock();
  return (
    <section className="landing-atmosphere relative overflow-hidden border-b-[3px] border-ink bg-paper">
      <div className="pointer-events-none absolute inset-0 retro-grid-bg opacity-60" aria-hidden />
      <div className="landing-drift-field" aria-hidden />
      <div className="landing-front-line landing-front-line-a" aria-hidden />
      <div className="landing-front-line landing-front-line-b" aria-hidden />

      <div className="relative mx-auto grid max-w-[1400px] grid-cols-1 gap-6 px-4 py-12 sm:px-6 lg:grid-cols-[1.4fr_1fr] lg:gap-10 lg:py-20">
        {/* Left: headline */}
        <div className="landing-hero-copy flex flex-col gap-6">
          <div className="landing-hero-item flex flex-wrap items-center gap-2" style={revealDelay(60)}>
            <RetroBadge tone="ink">[ SYSTEM 01 / OUTLOOK ]</RetroBadge>
            <RetroBadge tone="lime" pulse>OPERATIONAL</RetroBadge>
            <RetroBadge tone="paper">v1.2 · MODEL V5</RetroBadge>
          </div>

          <h1 className="landing-hero-item landing-title font-display font-extrabold uppercase leading-[0.85] tracking-[-0.04em] text-ink"
              style={{ ...revealDelay(130), fontSize: 'clamp(3.5rem, 11vw, 9rem)' }}>
            Auto<span className="text-signal-amber">Outlook</span>
          </h1>

          <p className="landing-hero-item max-w-[640px] font-display text-xl font-bold uppercase leading-tight tracking-tight text-ink/80 sm:text-2xl lg:text-3xl"
             style={revealDelay(210)}>
            Automated convective risk intelligence.
            <br />
            <span className="text-ink/55">From raw model data to verified outlook — without a human in the loop.</span>
          </p>

          <p className="landing-hero-item max-w-[640px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg"
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
            <a
              href="#docs-enh-verification"
              onClick={go('#docs-enh-verification')}
              className="retro-button landing-action-button !px-5 !py-3 text-base"
            >
              2026 Risk Archive
            </a>
            <a
              href="#pipeline"
              className="font-mono text-[11px] uppercase tracking-[0.25em] text-ink/60 underline-offset-4 transition-colors hover:text-ink hover:underline"
            >
              ▾ How it works
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
          <div className="retro-card-lg retro-scanline landing-telemetry-card relative overflow-hidden bg-ink p-0 text-paper">
            <div className="landing-panel-glow" aria-hidden />
            <div className="landing-sweep-line" aria-hidden />
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

            <div className="grid grid-cols-2 gap-px bg-paper/10">
              <DarkStat label="UTC TIME" value={clock.timeFull} sub={clock.date} />
              <DarkStat label="CYCLE" value="12Z RUN" sub="auto-detected" />
              <DarkStat label="OUTLOOK" value="ENH" valueClass="bg-risk-enh text-paper px-2" sub="central plains" />
              <DarkStat label="MAIN HAZARD" value="TORNADO" sub="conf 72%" />
              <DarkStat label="SPC QC" value="LEDGER" valueClass="text-signal-lime" sub="risk counts" />
              <DarkStat label="SPC AGREE" value="35%" sub="QC sample" />
            </div>

            {/* probability heatmap simulation */}
            <div className="border-t-[3px] border-paper/15 p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
                  HAZARD PROBABILITY · F+12H
                </span>
                <span className="font-mono text-[9px] uppercase tracking-[0.25em] text-paper/40">
                  TILE STRIDE 1
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

            <div className="border-t-[3px] border-paper/15 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
              ▸ READY · 49 OUTLOOKS · LAST PUBLISH 00:08:42 AGO
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
      <div className="font-mono text-[9px] uppercase tracking-[0.3em] text-ink/50">{label}</div>
      <div className="mt-1 font-display text-xl font-extrabold uppercase tracking-tight text-ink">{value}</div>
      {sub && <div className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.2em] text-ink/50">{sub}</div>}
    </div>
  );
}

function DarkStat({ label, value, sub, valueClass = '' }: { label: string; value: string; sub?: string; valueClass?: string }) {
  return (
    <div className="landing-dark-stat bg-ink p-3">
      <div className="font-mono text-[9px] uppercase tracking-[0.3em] text-paper/50">{label}</div>
      <div className={`mt-1 inline-block font-display text-base font-extrabold uppercase leading-none tracking-tight text-paper ${valueClass}`}>
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
    <div className="landing-ticker border-b-[3px] border-ink bg-ink text-paper/80">
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

function RiskRamp() {
  return (
    <section className="border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <div className="landing-reveal" data-landing-reveal="true">
          <SectionHead tag="RAMP / 02" title="Six categories. One ladder." />
          <p className="mt-4 max-w-[760px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
            AutoOutlook honors the SPC categorical convention. Each step on the ramp narrows where, when, and how
            confident the system is about severe convection. Risk polygons render as concentric annuli — never solid disks —
            so each color band marks where <em>that</em> category is the highest applicable risk.
          </p>
        </div>

        <div className="mt-10 grid grid-cols-1 gap-px border-[3px] border-ink bg-ink md:grid-cols-3 lg:grid-cols-6">
          {RISK_ORDER.map((code) => {
            const meta = RISK_META[code];
            return (
              <div
                key={code}
                className={`landing-risk-card landing-reveal relative flex flex-col p-4 ${meta.tw}`}
                data-landing-reveal="true"
                style={revealDelay(90 + meta.ord * 55)}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[10px] uppercase tracking-[0.3em] opacity-70">
                    {String(meta.ord + 1).padStart(2, '0')} / 06
                  </span>
                  <span className="font-mono text-[10px] uppercase tracking-[0.3em] opacity-70">
                    {meta.chipText}
                  </span>
                </div>
                <div className="mt-6 font-display text-3xl font-extrabold uppercase leading-none tracking-tight">
                  {meta.label}
                </div>
                <p className="mt-3 font-sans text-xs leading-relaxed opacity-80">
                  {RISK_DESCRIPTORS[code]}
                </p>
              </div>
            );
          })}
        </div>

        {/* gradient bar */}
        <div className="landing-reveal mt-6 grid grid-cols-6 gap-0 border-[3px] border-ink shadow-retro" data-landing-reveal="true" style={revealDelay(260)}>
          {RISK_ORDER.map((code) => (
            <div key={code} className={`landing-ramp-segment ${RISK_META[code].tw} h-4`} aria-hidden />
          ))}
        </div>
        <div className="landing-reveal mt-1 grid grid-cols-6 font-mono text-[10px] uppercase tracking-[0.25em] text-ink/60" data-landing-reveal="true" style={revealDelay(310)}>
          {RISK_ORDER.map((code) => (
            <span key={code} className="text-center">
              {RISK_META[code].chipText}
            </span>
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
  return (
    <section id="capabilities" className="scroll-mt-20 border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <div className="landing-reveal" data-landing-reveal="true">
          <SectionHead tag="CAPABILITIES / 03" title="An operations console, not a chart." />
          <p className="mt-4 max-w-[760px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
            Every panel exists to answer one operational question. The dashboard refuses generic SaaS chrome —
            controls are compact, labels are explicit, and the sidebar now stays focused on the panels that matter
            during forecast review.
          </p>
        </div>

        <div className="mt-10 grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
          {CAPABILITIES.map((c, idx) => (
            <article
              key={c.tag}
              className="retro-card landing-card-motion landing-reveal group relative flex flex-col p-5"
              data-landing-reveal="true"
              style={revealDelay(80 + idx * 50)}
            >
              <div className="flex items-center justify-between">
                <span className={`inline-block h-3 w-3 border-[2px] border-ink ${c.accent}`} aria-hidden />
                <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">[ {c.tag} ]</span>
              </div>
              <h3 className="mt-5 font-display text-2xl font-extrabold uppercase leading-tight tracking-tight">
                {c.title}
              </h3>
              <p className="mt-3 font-sans text-sm leading-relaxed text-ink/70">{c.body}</p>
              <div className="mt-5 flex items-center justify-between border-t-[2px] border-ink/15 pt-3 font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">
                <span>► PANEL · LIVE</span>
                <span>{c.tag}</span>
              </div>
            </article>
          ))}
        </div>
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
            AutoOutlook converts each fixed HRRR cycle into a finished f00–f48 artifact. Model schema v5
            combines 37 environmental, spatial, and time-season inputs across four trained hazard heads;
            trained thunder probability feeds TSTM, severe hazards feed MRGL and higher categories, and
            CIG is published as one clean labeled overlay on the hazard maps. The same artifact path now backs the 21 curated
            12Z-to-12Z historical archive cases.
          </p>
        </div>

        <ol className="mt-10 grid grid-cols-1 gap-px border-[3px] border-paper/20 bg-paper/15 md:grid-cols-2 lg:grid-cols-5">
          {PIPELINE_STEPS.map((step, idx) => (
            <li
              key={step.code}
              className="landing-pipeline-step landing-reveal relative flex flex-col gap-3 bg-ink p-5"
              data-landing-reveal="true"
              style={revealDelay(90 + idx * 70)}
            >
              <div className="flex items-center justify-between">
                <span className="font-display text-4xl font-extrabold leading-none tracking-tight text-signal-amber">
                  {step.code}
                </span>
                <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/50">
                  STEP / {step.code}
                </span>
              </div>
              <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-signal-amber">
                ▸ {step.label}
              </div>
              <h3 className="font-display text-xl font-extrabold uppercase leading-tight tracking-tight">
                {step.title}
              </h3>
              <p className="font-sans text-sm leading-relaxed text-paper/70">{step.body}</p>
            </li>
          ))}
        </ol>

        {/* leakage guard callout */}
        <div className="landing-leakage-card landing-reveal mt-8 grid grid-cols-1 gap-px border-[3px] border-signal-red bg-signal-red md:grid-cols-[auto_1fr]" data-landing-reveal="true" style={revealDelay(180)}>
          <div className="flex items-center justify-center bg-signal-red px-4 py-3">
            <span className="font-display text-2xl font-extrabold uppercase tracking-tight text-paper">
              ⚠ LEAKAGE GUARD
            </span>
          </div>
          <div className="bg-ink p-4 text-paper">
            <p className="font-sans text-sm leading-relaxed">
              Predictions are published <span className="font-bold text-signal-amber">first</span>.
              The official SPC Day 1 outlook is fetched <span className="font-bold text-signal-amber">after</span>,
              and only for verification — it never feeds back into the forecast pipeline. The UI surfaces that guard
              directly in the SPC QC panel alongside agreement, displacement, and category-ledger diagnostics.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section: hazards
// ---------------------------------------------------------------------------

function HazardsSection() {
  return (
    <section id="landing-hazards" className="scroll-mt-20 border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <div className="landing-reveal" data-landing-reveal="true">
          <SectionHead tag="HAZARDS / 05" title="Tornado · Hail · Wind · Flood." />
          <p className="mt-4 max-w-[760px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
            Each hazard head publishes its own probability surface plus a SIG (significant severe) overlay where
            appropriate. Probability bands honor the SPC convention and the offset / morphing SIG layer matches the
            rendering documented in <span className="font-mono font-bold">docs/hazard-outlooks.md</span>.
          </p>
        </div>

        <div className="mt-10 grid grid-cols-1 gap-5 md:grid-cols-2">
          {HAZARDS.map((h, idx) => {
            const meta = HAZARD_META[h.key];
            return (
              <article
                key={h.key}
                className="retro-card landing-card-motion landing-reveal relative flex flex-col p-0"
                data-landing-reveal="true"
                style={revealDelay(90 + idx * 65)}
              >
                <div className="flex items-center justify-between border-b-[3px] border-ink bg-ink px-4 py-2 text-paper">
                  <div className="flex items-center gap-3">
                    <span className="inline-flex h-9 w-9 items-center justify-center border-[2px] border-paper/40 bg-paper/5 text-xl">
                      {meta.glyph}
                    </span>
                    <div className="flex flex-col leading-none">
                      <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
                        HAZARD HEAD
                      </span>
                      <span className="mt-1 font-display text-lg font-extrabold uppercase tracking-tight">
                        {meta.label}
                      </span>
                    </div>
                  </div>
                  <RetroBadge tone={h.key === 'flood' ? 'cyan' : 'red'}>{h.key === 'flood' ? 'RULE' : 'ML'}</RetroBadge>
                </div>

                <div className="grid grid-cols-1 gap-px bg-ink/10 sm:grid-cols-[1fr_auto]">
                  <div className="bg-paper p-4">
                    <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">
                      Probability bands
                    </div>
                    <div className="mt-2 font-mono text-sm font-bold text-ink">{h.band}</div>
                    {h.sigBand !== '— ' && (
                      <div className="mt-3 inline-flex items-center gap-2 border-[2px] border-ink bg-signal-red/15 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.25em] text-ink">
                        <span className="inline-block h-2 w-2 bg-signal-red" /> {h.sigBand}
                      </div>
                    )}
                  </div>
                  <div className="bg-paper p-4 sm:max-w-[260px]">
                    <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">Notes</div>
                    <p className="mt-2 font-sans text-sm leading-snug text-ink/75">{h.copy}</p>
                  </div>
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
// Section: provider chain
// ---------------------------------------------------------------------------

type TierTone = 'lime' | 'amber' | 'cyan';

interface TierData {
  tier: string;
  tone: TierTone;
  label: string;
  sub: string;
  copy: string;
}

const PROVIDER_TIERS: TierData[] = [
  {
    tier: 'TIER 1',
    tone: 'lime',
    label: 'Live forecast feed',
    sub: 'gated probability heads',
    copy: 'The full operational outlook: latest extended-range cycle, derived severe-weather ingredient deck, and gated tornado / hail / wind probability heads.',
  },
  {
    tier: 'TIER 2',
    tone: 'amber',
    label: 'Open-Meteo',
    sub: 'GFS-Seamless · public model',
    copy: 'Free public model endpoints. Browser-side fallback when the primary feed is unavailable.',
  },
  {
    tier: 'TIER 3',
    tone: 'cyan',
    label: 'Mock provider',
    sub: 'deterministic · plains',
    copy: 'Canned 7-stop Plains severe day. Final guard rail so the dashboard always renders.',
  },
];

function ProviderChain() {
  return (
    <section className="border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <div className="landing-reveal" data-landing-reveal="true">
          <SectionHead tag="RESILIENCE / 06" title="Three tiers. Always renders." />
          <p className="mt-4 max-w-[760px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
            The provider chain fails downward, never upward. Tier 1 is preferred. If it times out, the chain falls to
            Tier 2. If both live tiers fail, the deterministic mock loads so the dashboard never goes dark — and the
            source badge tells you exactly which tier won.
          </p>
        </div>

        <div className="mt-10 grid grid-cols-1 items-stretch gap-px border-[3px] border-ink bg-ink md:grid-cols-[1fr_auto_1fr_auto_1fr]">
          {PROVIDER_TIERS.map((t, idx) => (
            <Fragment key={t.tier}>
              <Tier tier={t.tier} tone={t.tone} label={t.label} sub={t.sub} copy={t.copy} idx={idx} total={PROVIDER_TIERS.length} />
              {idx < PROVIDER_TIERS.length - 1 && (
                <div className="hidden items-center justify-center bg-ink px-4 font-mono text-2xl text-signal-amber md:flex">
                  ▸
                </div>
              )}
            </Fragment>
          ))}
        </div>
      </div>
    </section>
  );
}

function Tier({ tier, tone, label, sub, copy, idx, total }: { tier: string; tone: TierTone; label: string; sub: string; copy: string; idx: number; total: number }) {
  return (
    <div className="landing-tier-card landing-reveal flex flex-col gap-3 bg-paper p-5" data-landing-reveal="true" style={revealDelay(90 + idx * 90)}>
      <div className="flex items-center justify-between">
        <RetroBadge tone={tone}>{tier}</RetroBadge>
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">
          {String(idx + 1).padStart(2, '0')} / {String(total).padStart(2, '0')}
        </span>
      </div>
      <div className="font-display text-2xl font-extrabold uppercase leading-none tracking-tight">
        {label}
      </div>
      <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">{sub}</div>
      <p className="font-sans text-sm leading-relaxed text-ink/70">{copy}</p>
    </div>
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

        <div className="mt-10 flex flex-wrap gap-2">
          {TECH_PILLS.map((t, idx) => (
            <span
              key={t}
              className="landing-tech-pill landing-reveal border-[2px] border-ink bg-paper px-3 py-1.5 font-mono text-[11px] font-bold uppercase tracking-[0.25em] shadow-retro-sm"
              data-landing-reveal="true"
              style={revealDelay(40 + idx * 24)}
            >
              {t}
            </span>
          ))}
        </div>

        <div className="mt-10 grid grid-cols-1 gap-5 md:grid-cols-3">
          <FactCard k="49" label="Forecast hours" sub="f00–f48 hourly outlooks" />
          <FactCard k="6" label="Risk categories" sub="TSTM → HIGH ladder" />
          <FactCard k="3" label="SPC compare modes" sub="Auto · SPC · overlay QC" />
        </div>
      </div>
    </section>
  );
}

function FactCard({ k, label, sub }: { k: string; label: string; sub: string }) {
  return (
    <div className="retro-card landing-card-motion landing-reveal flex flex-col p-5" data-landing-reveal="true">
      <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">{label}</div>
      <div className="mt-2 font-display font-extrabold uppercase leading-none tracking-[-0.03em]" style={{ fontSize: 'clamp(2.5rem, 6vw, 4.5rem)' }}>
        {k}
      </div>
      <div className="mt-3 font-mono text-[11px] uppercase tracking-[0.25em] text-ink/55">{sub}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: final CTA
// ---------------------------------------------------------------------------

function FinalCTA() {
  return (
    <section className="landing-atmosphere relative overflow-hidden border-b-[3px] border-ink bg-paper">
      <div className="pointer-events-none absolute inset-0 retro-grid-bg opacity-60" aria-hidden />
      <div className="landing-drift-field" aria-hidden />
      <div className="relative mx-auto max-w-[1400px] px-4 py-16 sm:px-6 lg:py-24">
        <div className="retro-card-lg retro-scanline landing-final-card landing-reveal relative overflow-hidden bg-ink p-8 text-paper sm:p-12" data-landing-reveal="true">
          <div className="landing-panel-glow" aria-hidden />
          <div className="landing-sweep-line" aria-hidden />
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
            <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">Repository</div>
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
  return (
    <footer className="border-t-[3px] border-ink bg-ink text-paper">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
          AutoOutlook · Automated Convective Risk Intelligence · v1.2.2
        </span>
        <div className="flex flex-wrap items-center gap-4 font-mono text-[10px] uppercase tracking-[0.3em] text-paper/40">
          <a href="#dashboard" onClick={go('#dashboard')} className="hover:text-paper">Dashboard</a>
          <a href="#docs-enh-verification" onClick={go('#docs-enh-verification')} className="hover:text-paper">2026 Risk Archive</a>
          <a href="#docs" onClick={go('#docs')} className="hover:text-paper">Docs</a>
          <a href="#changelog" onClick={go('#changelog')} className="hover:text-paper">Changelog</a>
          <a href="https://github.com/ShianMike/OpenFetch" target="_blank" rel="noreferrer" className="hover:text-paper">OpenFetch</a>
          <a href="#capabilities" className="hover:text-paper">Capabilities</a>
          <span>LIVE → FALLBACK → MOCK</span>
        </div>
      </div>
    </footer>
  );
}

// ---------------------------------------------------------------------------
// Shared: section heading
// ---------------------------------------------------------------------------

function SectionHead({ tag, title, dark = false }: { tag: string; title: string; dark?: boolean }) {
  return (
    <div className="flex flex-col gap-3">
      <div className={`flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.35em] ${dark ? 'text-paper/60' : 'text-ink/55'}`}>
        <span className={`inline-block h-2 w-2 ${dark ? 'bg-signal-amber' : 'bg-ink'}`} aria-hidden />
        <span>[ {tag} ]</span>
        <span className={`h-px flex-1 ${dark ? 'bg-paper/20' : 'bg-ink/15'}`} />
      </div>
      <h2
        className={`font-display font-extrabold uppercase leading-[0.95] tracking-[-0.03em] ${dark ? 'text-paper' : 'text-ink'}`}
        style={{ fontSize: 'clamp(2rem, 5vw, 4rem)' }}
      >
        {title}
      </h2>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top-level export
// ---------------------------------------------------------------------------

export default function LandingPage() {
  useLandingReveal();

  // Scroll to top on initial mount of the landing page so anchors don't trap.
  useEffect(() => {
    if (typeof window !== 'undefined') {
      window.scrollTo({ top: 0 });
    }
  }, []);

  return (
    <div className="landing-page min-h-screen bg-paper text-ink">
      <LandingNav />
      <main>
        <Hero />
        <LiveTickerBand />
        <RiskRamp />
        <CapabilitiesBento />
        <HowItWorks />
        <HazardsSection />
        <ProviderChain />
        <TechStack />
        <FinalCTA />
        <OpenFetchSponsor />
      </main>
      <LandingFooter />
    </div>
  );
}
