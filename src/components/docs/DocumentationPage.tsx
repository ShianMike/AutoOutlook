import type { ReactNode } from 'react';
import RetroPanel from '../retro/RetroPanel';
import RetroBadge from '../retro/RetroBadge';
import ForecastDisclaimer from '../ForecastDisclaimer';
import { RISK_META } from '../../types/forecast';
import { HAZARD_CONFIGS, type OutlookHazardKey } from '../../utils/hazardProbabilityBands';

interface LevelRow {
  code: keyof typeof RISK_META;
  blurb: string;
  tornado: string;
  hail: string;
  wind: string;
}

const LEVELS: LevelRow[] = [
  { code: 'TSTM', blurb: 'Non-severe thunderstorms expected. Lightning, brief gusts, locally heavy rain. No organized severe threat.',                tornado: 'none',  hail: 'none',  wind: 'none'  },
  { code: 'MRGL', blurb: 'Isolated severe storms possible. Low coverage, short-lived, marginal intensity.',                                            tornado: '≥ 2%',  hail: '≥ 5%',  wind: '≥ 5%'  },
  { code: 'SLGT', blurb: 'Scattered severe storms. A few organized cells with brief tornadoes, large hail, or damaging gusts.',                        tornado: '≥ 5%',  hail: '≥ 15%', wind: '≥ 15%' },
  { code: 'ENH',  blurb: 'Numerous severe storms. Several intense, longer-lived storms; significant severe possible.',                                 tornado: '≥ 10%', hail: '≥ 30%', wind: '≥ 30%' },
  { code: 'MOD',  blurb: 'Widespread severe weather. Strong/significant tornadoes, very large hail, or widespread damaging winds expected.',           tornado: '≥ 15%', hail: '≥ 45%', wind: '≥ 45%' },
  { code: 'HIGH', blurb: 'Outbreak-level threat. Long-track strong tornadoes or extensive derecho-scale wind events likely.',                          tornado: '≥ 30%', hail: '≥ 60%', wind: '≥ 60%' },
];

interface ConfidenceFloor {
  code: keyof typeof RISK_META | 'NONE';
  floor: string;
}

const CONFIDENCE_FLOORS: ConfidenceFloor[] = [
  { code: 'NONE', floor: '0.45' },
  { code: 'TSTM', floor: '0.50' },
  { code: 'MRGL', floor: '0.58' },
  { code: 'SLGT', floor: '0.66' },
  { code: 'ENH',  floor: '0.73' },
  { code: 'MOD',  floor: '0.80' },
  { code: 'HIGH', floor: '0.88' },
];

interface HorizonBand {
  range: string;
  label: string;
  description: string;
  tone: 'lime' | 'amber' | 'orange';
}

const HORIZONS: HorizonBand[] = [
  { range: '0 – 6 h',  label: 'High Confidence',  description: 'Anchored to the latest HRRR analysis. Mesoscale placement of storm mode and initiation is most reliable here.', tone: 'lime' },
  { range: '6 – 18 h', label: 'Moderate Confidence', description: 'Deterministic-only guidance. Timing of capping erosion, frontal passage, and convective initiation introduces spread.', tone: 'amber' },
  { range: '18 – 48 h', label: 'Pattern Guidance', description: 'Use for trend and synoptic pattern recognition rather than exact placement. Position errors of 100–200 km are expected.', tone: 'orange' },
];

interface HazardBand {
  key: OutlookHazardKey;
  hazard: string;
  note: string;
}

// Labels + colors are pulled from HAZARD_CONFIGS at render time so the
// documentation chips always match what the live hazard map paints.
const HAZARD_BANDS: HazardBand[] = [
  {
    key: 'tornado',
    hazard: 'Tornado',
    note: 'Probability of a tornado within 25 mi of any point in the band, valid over the forecast hour.',
  },
  {
    key: 'hail',
    hazard: 'Hail (severe)',
    note: 'Probability of ≥ 1 in. diameter hail within 25 mi of any point. Significant-severe hail flagged separately when ≥ 2 in.',
  },
  {
    key: 'wind',
    hazard: 'Wind (severe)',
    note: 'Probability of ≥ 58 mph thunderstorm wind gust within 25 mi of any point. Significant-severe when ≥ 74 mph.',
  },
  {
    key: 'thunder',
    hazard: 'General Thunder',
    note: 'Probability of at least one lightning strike within 12 mi of any point in the band.',
  },
];

// YIQ luminance contrast picker: returns ink for light backgrounds and
// paper for dark ones, so the probability label stays legible on every
// chip in the SPC color ramp (green/tan/red/magenta/purple/cyan/yellow).
function pickChipTextColor(hex: string): string {
  const clean = hex.replace('#', '');
  if (clean.length !== 6) return '#111111';
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  const yiq = (r * 299 + g * 587 + b * 114) / 1000;
  return yiq >= 160 ? '#111111' : '#f5f1e8';
}

type GlossaryAccent = 'red' | 'cyan' | 'amber' | 'orange' | 'lime';

interface GlossaryEntry {
  term: string;
  unit: string;
  definition: string;
  favorable?: string;
}

interface GlossaryGroup {
  title: string;
  code: string;
  accent: GlossaryAccent;
  blurb: string;
  entries: GlossaryEntry[];
}

const GLOSSARY: GlossaryGroup[] = [
  {
    title: 'Instability',
    code: 'IN',
    accent: 'red',
    blurb: 'Available buoyancy and the cap that resists its release.',
    entries: [
      { term: 'MLCAPE', unit: 'J/kg', favorable: '≥ 3000',  definition: 'Mixed-Layer CAPE. Buoyant energy of a parcel averaged over the lowest 100 mb. Best for mid-day surface-based convection.' },
      { term: 'MUCAPE', unit: 'J/kg', favorable: '≥ 3500',  definition: 'Most-Unstable CAPE. Buoyancy of the most unstable parcel in the lowest 300 mb. Captures elevated convection.' },
      { term: 'SBCAPE', unit: 'J/kg', favorable: '≥ 3000',  definition: 'Surface-Based CAPE. Buoyancy of a parcel lifted from the surface. Sensitive to inversions and capping.' },
      { term: 'CIN',    unit: 'J/kg', favorable: '≥ −50',   definition: 'Convective Inhibition. Negative energy a parcel must overcome before free ascent. Weak CIN (closer to zero) favors initiation; strongly negative values suppress storms.' },
    ],
  },
  {
    title: 'Moisture',
    code: 'MO',
    accent: 'cyan',
    blurb: 'Low-level humidity and the depth of the moist layer feeding storm inflow.',
    entries: [
      { term: 'Sfc Td',     unit: '°F',    favorable: '≥ 70',   definition: 'Surface dewpoint. Low-level moisture available to feed storm inflow.' },
      { term: 'PWAT',       unit: 'in',    favorable: '≥ 1.7',  definition: 'Precipitable Water. Total column water vapor expressed as a depth of rainfall.' },
      { term: 'LCL',        unit: 'm AGL', favorable: '≤ 800',  definition: 'Lifted Condensation Level. Cloud base height; lower LCLs favor tornadoes.' },
      { term: 'PWAT depth', unit: 'm',     favorable: '≥ 3500', definition: 'Depth proxy for the moist layer feeding the storm. Used as a quick surrogate when sounding-based depth is unavailable.' },
    ],
  },
  {
    title: 'Kinematics',
    code: 'KN',
    accent: 'amber',
    blurb: 'Vertical wind structure that organizes updrafts and supports rotation.',
    entries: [
      { term: '0–1 km SRH',    unit: 'm²/s²', favorable: '≥ 250', definition: 'Storm-Relative Helicity in the lowest 1 km. High values favor near-surface rotation and tornado potential.' },
      { term: '0–3 km SRH',    unit: 'm²/s²', favorable: '≥ 400', definition: 'Storm-Relative Helicity in the lowest 3 km. Mid-level rotation that supports supercell maintenance.' },
      { term: 'Sfc–500 Shear', unit: 'kt',    favorable: '≥ 50',  definition: 'Bulk shear between the surface and the 500 mb level. ≥ 35–40 kt favors organized convection.' },
      { term: 'SR wind proxy', unit: 'kt',    favorable: '≥ 40',  definition: 'Storm-relative inflow proxy derived from available wind fields. Approximates inflow into a moving updraft.' },
    ],
  },
  {
    title: 'Forcing & Mode',
    code: 'FM',
    accent: 'orange',
    blurb: 'Surface boundaries, initiation confidence, and the expected storm character.',
    entries: [
      { term: 'Front signal',     unit: '',  definition: 'Strength of a surface boundary (cold front, dryline, outflow). Strong boundaries focus initiation. Reported as weak / moderate / strong.' },
      { term: 'Initiation Conf.', unit: '%', favorable: '≥ 60', definition: 'Confidence that storms initiate over the focus region during the forecast hour.' },
      { term: 'Storm Mode',       unit: '',  definition: 'Expected dominant mode: discrete supercells, multicell clusters, linear (QLCS), or mixed. Discrete favors tornadoes/hail; linear favors damaging wind.' },
      { term: 'Capping',          unit: '',  definition: 'Strength of an inhibiting warm layer aloft. Strong cap suppresses storms; weak cap allows initiation. Reported as weak / moderate / strong.' },
    ],
  },
  {
    title: 'Composite Signals',
    code: 'CS',
    accent: 'lime',
    blurb: 'Single-number indices that summarize severe potential.',
    entries: [
      { term: 'STP',     unit: '', favorable: '≥ 4', definition: 'Significant Tornado Parameter. Composite of CAPE, shear, SRH, and LCL. Values ≥ 1 favor significant tornadoes.' },
      { term: 'SCP',     unit: '', favorable: '≥ 8', definition: 'Supercell Composite Parameter. Combines MUCAPE, effective SRH, and effective shear.' },
      { term: 'EHI',     unit: '', favorable: '≥ 4', definition: 'Energy Helicity Index. CAPE × 0–1 km SRH normalized. Tornado favorability index.' },
      { term: 'SHIP',    unit: '', favorable: '≥ 3', definition: 'Significant Hail Parameter. Composite signaling large/very large hail potential.' },
      { term: 'TorComp', unit: '', favorable: '≥ 3', definition: 'Internal tornado composite blending the ML tornado probability with the forecast hour and severity ordinal.' },
    ],
  },
];

const GLOSSARY_ACCENT_BG: Record<GlossaryAccent, string> = {
  red:    'bg-signal-red text-paper',
  cyan:   'bg-signal-cyan text-ink',
  amber:  'bg-signal-amber text-ink',
  orange: 'bg-signal-orange text-ink',
  lime:   'bg-signal-lime text-ink',
};

export default function DocumentationPage() {
  return (
    <div className="flex flex-col">
      <DocsHero />
      <main className="w-full min-w-0 flex-1 px-3 py-2 sm:px-4 xl:px-5 flex flex-col gap-3 xl:gap-4">
        <DocsOverview />
        <DocsLevels />
        <DocsPerformance />
        <DocsPredictability />
        <DocsHazards />
        <DocsSources />
        <DocsGlossary />
        <DocsDisclaimerSection />
      </main>
    </div>
  );
}

function DocsHero() {
  return (
    <header className="border-b-[3px] border-ink bg-ink text-paper">
      <div className="w-full px-4 py-3 xl:px-5 flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <span className="inline-flex border-[2px] border-paper bg-ink px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.3em] text-paper/80">
            AO/DOC
          </span>
          <h2 className="font-display text-base font-extrabold uppercase tracking-[0.16em] truncate">
            AutoOutlook · Reference Manual
          </h2>
        </div>
        <div className="flex items-center gap-2">
          <RetroBadge tone="cyan">Static</RetroBadge>
          <RetroBadge tone="ink">v0.1</RetroBadge>
        </div>
      </div>
      <div className="border-t-[2px] border-paper/20 bg-ink px-4 py-1.5 xl:px-5">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/55">
          Eight sections · Architecture · Levels · Skill · Predictability · Bands · Providers · Glossary · Disclaimer
        </span>
      </div>
    </header>
  );
}

function DocSection({
  id,
  eyebrow,
  title,
  badge,
  children,
}: {
  id: string;
  eyebrow: string;
  title: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-4">
      <RetroPanel title={title} eyebrow={eyebrow} badge={badge}>
        <div className="space-y-4 font-sans text-sm leading-relaxed text-ink">
          {children}
        </div>
      </RetroPanel>
    </section>
  );
}

function Lead({ children }: { children: ReactNode }) {
  return (
    <p className="font-display text-base font-bold uppercase leading-snug tracking-wide text-ink">
      {children}
    </p>
  );
}

function Body({ children }: { children: ReactNode }) {
  return <p className="text-[13.5px] leading-relaxed text-ink/85">{children}</p>;
}

function DocList({ children }: { children: ReactNode }) {
  return (
    <ul className="flex flex-col gap-2 text-[12.5px] text-ink/85" role="list">
      {children}
    </ul>
  );
}

function DocListItem({ children }: { children: ReactNode }) {
  return (
    <li className="flex items-start gap-3">
      <span
        className="mt-[8px] inline-block h-[3px] w-[10px] shrink-0 bg-ink"
        aria-hidden
      />
      <span className="leading-snug">{children}</span>
    </li>
  );
}

function Mono({ children }: { children: ReactNode }) {
  return (
    <span className="border-[1.5px] border-ink bg-paper px-1 py-px font-mono text-[11px] uppercase tracking-wider text-ink">
      {children}
    </span>
  );
}

function StatGrid({ items }: { items: { label: string; value: string }[] }) {
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      {items.map((item) => (
        <div
          key={item.label}
          className="border-[2px] border-ink bg-paper p-2 shadow-retro-sm"
        >
          <div className="font-mono text-[9px] uppercase tracking-widest text-ink/55">
            {item.label}
          </div>
          <div className="mt-1 font-display text-lg font-extrabold leading-none text-ink">
            {item.value}
          </div>
        </div>
      ))}
    </div>
  );
}

function DocsOverview() {
  return (
    <DocSection
      id="docs-overview"
      eyebrow="DOC / 01 · WHAT IS ACRI"
      title="System Overview"
    >
      <Lead>
        AutoOutlook is an Automated Convective Risk Intelligence platform: a fully
        automated severe-weather outlook that runs end-to-end without a human in the loop.
      </Lead>
      <Body>
        ACRI mirrors the categorical and probabilistic structure of an SPC convective outlook
        using HRRR model fields, MetPy-style derived ingredients, and XGBoost hazard
        probabilities. It produces a categorical risk surface, per-hazard probability bands,
        a parameter dashboard, an auto-generated forecast discussion, and a system-status
        readout, all refreshed every 15 minutes.
      </Body>

      <StatGrid
        items={[
          { label: 'Forecast Horizon', value: '0 – 48 h' },
          { label: 'Slider Stops',     value: '7' },
          { label: 'Auto Refresh',     value: '15 min' },
          { label: 'Hazards Modeled',  value: '3 + 1' },
        ]}
      />

      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <PipelineStep
          step="01"
          name="HRRR Fields"
          detail="GRIB2 byte-range subset of CAPE, CIN, dewpoint, winds, heights."
        />
        <PipelineStep
          step="02"
          name="Derived Ingredients"
          detail="MetPy-style shear, SRH proxies, STP/SCP/EHI/SHIP composites."
        />
        <PipelineStep
          step="03"
          name="XGBoost Hazards"
          detail="Tornado / hail / wind probabilities → SPC-style category + bands."
        />
      </div>

      <Body>
        ACRI runs as a deployable artifact pipeline: the latest extended HRRR cycle is
        detected automatically, forecast hours <Mono>f00 … f48</Mono> are processed, and the
        outputs are written to disk as GeoJSON polygons, probability tiles, metadata, and
        preview PNGs. The frontend reads those artifacts; it does not invoke the model on
        request.
      </Body>
    </DocSection>
  );
}

function PipelineStep({
  step,
  name,
  detail,
}: {
  step: string;
  name: string;
  detail: string;
}) {
  return (
    <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
      <div className="flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center border-[2px] border-ink bg-ink font-mono text-[10px] font-bold text-paper">
          {step}
        </span>
        <span className="font-display text-sm font-extrabold uppercase tracking-wider text-ink">
          {name}
        </span>
      </div>
      <p className="mt-2 text-[12.5px] leading-snug text-ink/80">{detail}</p>
    </div>
  );
}

function DocsLevels() {
  return (
    <DocSection
      id="docs-levels"
      eyebrow="DOC / 02 · TSTM → HIGH"
      title="Risk Level Codex"
    >
      <Lead>
        Six categorical risk levels, each anchored to a per-hazard probability threshold.
      </Lead>
      <Body>
        The category in any cell is the highest level supported by its hazard probabilities.
        Thresholds below match the SPC convention used by the verification harness in{' '}
        <Mono>backend.ml.validate_models</Mono>. "MOD" and "MDT" are equivalent.
      </Body>

      <div className="overflow-hidden border-[2px] border-ink">
        <div className="grid grid-cols-[112px_minmax(0,1fr)_repeat(3,72px)] border-b-[2px] border-ink bg-ink text-paper font-mono text-[10px] uppercase tracking-[0.18em]">
          <div className="px-3 py-2">Level</div>
          <div className="border-l-[1.5px] border-paper/30 px-3 py-2">Meaning</div>
          <div className="border-l-[1.5px] border-paper/30 px-2 py-2 text-center">Torn.</div>
          <div className="border-l-[1.5px] border-paper/30 px-2 py-2 text-center">Hail</div>
          <div className="border-l-[1.5px] border-paper/30 px-2 py-2 text-center">Wind</div>
        </div>
        {LEVELS.map((row) => {
          const meta = RISK_META[row.code];
          return (
            <div
              key={row.code}
              className="grid grid-cols-[112px_minmax(0,1fr)_repeat(3,72px)] border-b-[1.5px] border-ink last:border-b-0"
            >
              <div className={`flex items-center justify-center px-2 py-3 ${meta.tw}`}>
                <span className="font-display text-base font-extrabold tracking-widest">
                  {row.code}
                </span>
              </div>
              <div className="border-l-[1.5px] border-ink bg-paper px-3 py-3">
                <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink/55">
                  {meta.label}
                </div>
                <p className="mt-0.5 text-[12.5px] leading-snug text-ink/85">{row.blurb}</p>
              </div>
              <div className="border-l-[1.5px] border-ink bg-paper px-2 py-3 text-center font-mono text-[11px] font-bold tracking-wider text-ink">
                {row.tornado}
              </div>
              <div className="border-l-[1.5px] border-ink bg-paper px-2 py-3 text-center font-mono text-[11px] font-bold tracking-wider text-ink">
                {row.hail}
              </div>
              <div className="border-l-[1.5px] border-ink bg-paper px-2 py-3 text-center font-mono text-[11px] font-bold tracking-wider text-ink">
                {row.wind}
              </div>
            </div>
          );
        })}
      </div>

      <Body>
        Thresholds are <em>cumulative</em>: a SLGT polygon includes every cell that meets at
        least the SLGT threshold for any hazard, so larger bands always contain smaller
        ones. The map renders each band as an annulus showing where that level is the highest
        applicable risk.
      </Body>
    </DocSection>
  );
}

function DocsPerformance() {
  return (
    <DocSection
      id="docs-performance"
      eyebrow="DOC / 03 · TRAINING & VERIFICATION"
      title="Model Skill"
      badge={<RetroBadge tone="amber">Experimental</RetroBadge>}
    >
      <Lead>
        ACRI uses three XGBoost gradient-boosted classifiers (one per severe hazard)
        trained on a historical HRRR archive matched to SPC severe reports.
      </Lead>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.22em] text-ink/55">
            Training Set
          </div>
          <DocList>
            <DocListItem>HRRR archive, byte-range subset of severe-relevant fields.</DocListItem>
            <DocListItem>SPC severe reports matched to the precise HRRR grid valid time.</DocListItem>
            <DocListItem>Configurable positive/negative point density per hour.</DocListItem>
            <DocListItem>De-duplicated by feature+label hash to suppress leakage.</DocListItem>
          </DocList>
        </div>
        <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.22em] text-ink/55">
            Activation Guardrails
          </div>
          <DocList>
            <DocListItem>Minimum training rows: <Mono>5,000</Mono>.</DocListItem>
            <DocListItem>Feature schema hash must match the runtime feature list.</DocListItem>
            <DocListItem>Artifacts tagged <Mono>experimentalOnly</Mono> stay inactive unless opted in.</DocListItem>
            <DocListItem>When inactive, the rule-based engine takes over.</DocListItem>
          </DocList>
        </div>
      </div>

      <div className="border-[3px] border-ink bg-paper">
        <div className="flex items-center justify-between border-b-[2px] border-ink bg-paper px-3 py-1.5">
          <span className="font-display font-extrabold uppercase text-[12px] tracking-wider">
            Confidence Floor by Category
          </span>
          <span className="font-mono text-[10px] tracking-widest text-ink/50">
            server.py · _artifact_confidence
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2 p-2.5 md:grid-cols-7">
          {CONFIDENCE_FLOORS.map((row) => {
            const meta = row.code === 'NONE' ? null : RISK_META[row.code];
            return (
              <div
                key={row.code}
                className="border-[2px] border-ink bg-paper p-2 shadow-retro-sm flex flex-col gap-1"
              >
                <div
                  className={`inline-block self-start border-[2px] border-ink px-1.5 py-0.5 font-display text-[11px] font-extrabold tracking-widest ${meta?.tw ?? 'bg-paper text-ink'}`}
                >
                  {row.code}
                </div>
                <div className="font-mono text-[14px] font-bold leading-none text-ink">
                  {row.floor}
                </div>
                <div className="font-mono text-[8px] uppercase tracking-widest text-ink/50">
                  Floor
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <Body>
        Verification of probability calibration lives in <Mono>backend.ml.validate_models</Mono>
        {' '}and <Mono>backend.ml.spc_verification</Mono>: Brier scores, reliability bins at{' '}
        <Mono>(0, 2, 5, 10, 15, 30, 45, 60, 100)%</Mono>, and overlap against the official
        SPC Day 1 outlook. Verification is computed after artifacts are written; the
        official outlook is never fed back into the model.
      </Body>

      <Body>
        Confidence floors above are the lower bound the dashboard reports for each category.
        The published value adds a small contribution from the peak hazard probability, so a
        HIGH cell with strong probabilities will read close to <Mono>0.95</Mono>.
      </Body>
    </DocSection>
  );
}

function DocsPredictability() {
  return (
    <DocSection
      id="docs-predictability"
      eyebrow="DOC / 04 · WHAT 0–48 H ACTUALLY MEANS"
      title="Predictability Window"
    >
      <Lead>
        The forecast envelope spans <Mono>0–48 h</Mono> from the most recent extended HRRR
        cycle. Confidence is not uniform across that window.
      </Lead>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {HORIZONS.map((band) => (
          <div key={band.range} className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
            <div className="flex items-center justify-between">
              <span className="font-display text-[13px] font-extrabold uppercase tracking-widest text-ink">
                {band.range}
              </span>
              <RetroBadge tone={band.tone}>{band.label}</RetroBadge>
            </div>
            <p className="mt-2 text-[12.5px] leading-snug text-ink/80">{band.description}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.22em] text-ink/55">
            Where Skill Comes From
          </div>
          <DocList>
            <DocListItem>HRRR assimilates radar and surface obs hourly; the first 6 h carry the most situational truth.</DocListItem>
            <DocListItem>Beyond ~18 h, deterministic placement errors grow faster than mesoscale skill.</DocListItem>
            <DocListItem>XGBoost calibration was trained at hour-of-forecast granularity, so the model "knows" later hours are noisier.</DocListItem>
          </DocList>
        </div>
        <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.22em] text-ink/55">
            How To Read The Slider
          </div>
          <DocList>
            <DocListItem>The 7 stops sample the full window: <Mono>0 · +3h · +6h · +9h · +12h · +18h · +24h</Mono>.</DocListItem>
            <DocListItem>Hazard bands can shift one level between adjacent stops. That is expected behavior, not a bug.</DocListItem>
            <DocListItem>If <Mono>mlHazardHours</Mono> is zero, the ML model is inactive for the current cycle and the rule-based engine is driving the outlook.</DocListItem>
          </DocList>
        </div>
      </div>
    </DocSection>
  );
}

function DocsHazards() {
  return (
    <DocSection
      id="docs-hazards"
      eyebrow="DOC / 05 · PROBABILITY CONTOURS"
      title="Hazard Probability Bands"
    >
      <Lead>
        Each hazard is contoured at a fixed ladder of probabilities. Larger bands always
        contain smaller ones.
      </Lead>
      <Body>
        Polygons are generated by <Mono>marching_squares_cumulative_contours</Mono> with
        cartographic generalization: small components are pruned, holes below a threshold
        are filled, and ladders are buffered so the visual hierarchy is preserved even when
        the underlying probability field is noisy.
      </Body>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {HAZARD_BANDS.map((band) => {
          const cfg = HAZARD_CONFIGS[band.key];
          return (
            <div key={band.key} className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
              <div className="flex items-center justify-between">
                <span className="font-display text-[13px] font-extrabold uppercase tracking-widest text-ink">
                  {band.hazard}
                </span>
                <span className="font-mono text-[10px] uppercase tracking-widest text-ink/50">
                  {cfg.labels.length} bands
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {cfg.labels.map((label, idx) => {
                  const bg = cfg.colors[idx];
                  const fg = pickChipTextColor(bg);
                  return (
                    <span
                      key={label}
                      className="border-[2px] border-ink px-2 py-0.5 font-mono text-[11px] font-bold tracking-wider shadow-retro-sm"
                      style={{ background: bg, color: fg }}
                    >
                      {label}
                    </span>
                  );
                })}
              </div>
              <p className="mt-3 text-[12.5px] leading-snug text-ink/75">{band.note}</p>
            </div>
          );
        })}
      </div>

      <Body>
        Significant-severe (the SPC SIG hatch) activates when the peak hazard
        probability for the active forecast hour clears a per-hazard threshold:{' '}
        <Mono>≥ 10% tornado</Mono> for EF2+, <Mono>≥ 30% hail</Mono> for stones ≥ 2 in,
        or <Mono>≥ 30% wind</Mono> for gusts ≥ 74 mph. General thunder and flood do
        not carry a SIG layer.
      </Body>

      <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
        <div className="flex items-center justify-between">
          <span className="font-display text-[13px] font-extrabold uppercase tracking-widest text-ink">
            SIG Layer
          </span>
          <span className="font-mono text-[10px] uppercase tracking-widest text-ink/50">
            Offset · Morphing
          </span>
        </div>

        <p className="mt-2 text-[12.5px] leading-snug text-ink/75">
          Unlike SPC's per-cell hatching, ACRI renders SIG as a{' '}
          <span className="font-bold text-ink">single smooth polygon</span> that is{' '}
          <span className="font-bold text-ink">offset along a per-hazard axis</span>{' '}
          from the primary high-probability band, so the SIG core has its own location
          instead of sitting directly on top of the ENH+ region.
        </p>

        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <SigOffsetCard
            hazard="Tornado"
            offset="+0.55 / −0.45"
            rationale="Toward the warm-sector / triple-point where STP and 0–1 km SRH peak."
          />
          <SigOffsetCard
            hazard="Hail"
            offset="−0.65 / +0.55"
            rationale="Back-left along the dry-line / mid-level lapse-rate axis where 2″+ stones cluster."
          />
          <SigOffsetCard
            hazard="Wind"
            offset="+0.95 / +0.40"
            rationale="Downshear along the QLCS / cold-pool axis where 74+ mph gusts cluster."
          />
        </div>

        <p className="mt-3 text-[12.5px] leading-snug text-ink/75">
          The SIG polygon also{' '}
          <span className="font-bold text-ink">morphs through the forecast cycle</span>{' '}
          — it rotates (about ±14°), stretches (aspect ±20%), wobbles its centroid
          around the peak cell, and scales with how far the peak exceeds the SIG
          threshold (roughly <Mono>0.78×</Mono> at threshold to <Mono>1.43×</Mono> when
          the peak is well above it). Different hazards morph out of phase with each
          other so the four panels read as distinct objects through the loop instead
          of pulsing in lock-step.
        </p>
      </div>
    </DocSection>
  );
}

function SigOffsetCard({
  hazard,
  offset,
  rationale,
}: {
  hazard: string;
  offset: string;
  rationale: string;
}) {
  return (
    <div className="border-[1.5px] border-ink bg-paper p-2 shadow-retro-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-display text-[11px] font-extrabold uppercase tracking-widest text-ink">
          {hazard}
        </span>
        <span className="font-mono text-[10px] font-bold tracking-wider text-ink/80">
          {offset}
        </span>
      </div>
      <p className="mt-1 text-[11.5px] leading-snug text-ink/70">{rationale}</p>
    </div>
  );
}

function DocsSources() {
  return (
    <DocSection
      id="docs-sources"
      eyebrow="DOC / 06 · WHERE THE DATA COMES FROM"
      title="Data Provider Chain"
    >
      <Lead>
        A three-tier provider chain. The dashboard always renders something useful, even if
        the highest-priority tier is offline.
      </Lead>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <ProviderTier
          tier="01"
          name="Python Backend"
          source="NOMADS HRRR · MetPy diagnostics · XGBoost"
          when="Preferred. Used whenever /api/forecast is reachable."
          tone="amber"
        />
        <ProviderTier
          tier="02"
          name="Open-Meteo"
          source="GFS-Seamless JSON · browser-side"
          when="Automatic fallback if the Python backend is offline."
          tone="cyan"
        />
        <ProviderTier
          tier="03"
          name="Mock Bundle"
          source="Deterministic Plains severe-weather day"
          when="Final fallback when both live providers fail."
          tone="paper"
        />
      </div>

      <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
        <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-ink/55">
          Source Badge
        </div>
        <p className="mt-1.5 text-[12.5px] leading-snug text-ink/85">
          The <Mono>SOURCE</Mono> badge in the command header indicates the winning tier:{' '}
          <Mono>LIVE</Mono> for backend or Open-Meteo, <Mono>FALLBACK</Mono> for mock. The
          System Status panel shows the full provider chain attempt log, including which
          providers were skipped and why.
        </p>
      </div>

      <Body>
        Production deployments can pin the public service to artifact-only mode
        (<Mono>AUTOOUTLOOK_FORECAST_SOURCE=artifact</Mono> +{' '}
        <Mono>AUTOOUTLOOK_ENABLE_LIVE_BUILD=false</Mono>). The public endpoint then serves
        only pre-generated artifacts; expensive HRRR/XGBoost generation runs in a separate
        scheduled job.
      </Body>
    </DocSection>
  );
}

function ProviderTier({
  tier,
  name,
  source,
  when,
  tone,
}: {
  tier: string;
  name: string;
  source: string;
  when: string;
  tone: 'amber' | 'cyan' | 'paper';
}) {
  return (
    <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-ink/55">
          Tier {tier}
        </span>
        <RetroBadge tone={tone}>{name}</RetroBadge>
      </div>
      <div className="font-mono text-[11px] uppercase tracking-wider text-ink">{source}</div>
      <p className="mt-2 text-[12.5px] leading-snug text-ink/75">{when}</p>
    </div>
  );
}

function DocsGlossary() {
  const totalTerms = GLOSSARY.reduce((sum, group) => sum + group.entries.length, 0);
  return (
    <DocSection
      id="docs-glossary"
      eyebrow="DOC / 07 · PARAMETER DICTIONARY"
      title="Ingredients Glossary"
      badge={<RetroBadge tone="cyan">{totalTerms} terms</RetroBadge>}
    >
      <Lead>
        Every parameter on the Environmental Ingredients board, grouped by meteorological
        role and annotated with the value the dashboard considers strongly favorable for
        organized severe convection.
      </Lead>

      <GlossaryLegend />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {GLOSSARY.map((group) => (
          <GlossaryGroupCard key={group.title} group={group} />
        ))}
      </div>
    </DocSection>
  );
}

function GlossaryLegend() {
  return (
    <div className="flex flex-col gap-2 border-[2px] border-ink bg-paper px-3 py-2 shadow-retro-sm md:flex-row md:items-center md:justify-between">
      <div className="flex items-center gap-2">
        <span className="bg-ink px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.24em] text-paper">
          Legend
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink/65">
          How to read each entry
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-4">
        <LegendChip
          sample={
            <span className="font-mono text-[10px] uppercase tracking-widest text-ink/55">
              J/kg
            </span>
          }
          label="measurement unit"
        />
        <LegendChip
          sample={
            <span className="inline-flex items-center border-[1.5px] border-ink bg-signal-lime/55 px-1.5 py-[1px] font-mono text-[10px] font-bold uppercase tracking-wider text-ink">
              ≥ 3000
            </span>
          }
          label="strongly favorable"
        />
      </div>
    </div>
  );
}

function LegendChip({ sample, label }: { sample: ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      {sample}
      <span className="font-mono text-[9px] uppercase tracking-widest text-ink/55">
        {label}
      </span>
    </div>
  );
}

function GlossaryGroupCard({ group }: { group: GlossaryGroup }) {
  return (
    <div className="border-[3px] border-ink bg-paper shadow-retro overflow-hidden">
      <header
        className={`flex items-stretch border-b-[3px] border-ink ${GLOSSARY_ACCENT_BG[group.accent]}`}
      >
        <div className="grid w-14 shrink-0 place-items-center border-r-[3px] border-ink bg-ink text-paper">
          <span className="font-mono text-[12px] font-bold tracking-[0.2em]">
            {group.code}
          </span>
        </div>
        <div className="flex-1 px-3 py-2.5">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="font-display text-lg font-extrabold uppercase leading-none tracking-wide">
              {group.title}
            </h3>
            <span className="font-mono text-[10px] uppercase tracking-widest opacity-70">
              {group.entries.length} {group.entries.length === 1 ? 'term' : 'terms'}
            </span>
          </div>
          <p className="mt-1.5 font-mono text-[10px] uppercase leading-snug tracking-[0.14em] opacity-80">
            {group.blurb}
          </p>
        </div>
      </header>

      <ul className="flex flex-col">
        {group.entries.map((entry, idx) => (
          <li
            key={entry.term}
            className={`grid grid-cols-1 gap-2 px-4 py-3.5 md:grid-cols-[170px_minmax(0,1fr)] md:gap-5 ${idx > 0 ? 'border-t-[1.5px] border-ink/15' : ''}`}
          >
            <div className="flex flex-col gap-1.5">
              <span className="font-display text-[15px] font-extrabold uppercase leading-none tracking-wider text-ink">
                {entry.term}
              </span>
              <div className="flex flex-wrap items-center gap-1.5">
                {entry.unit && (
                  <span className="font-mono text-[10px] uppercase tracking-widest text-ink/55">
                    {entry.unit}
                  </span>
                )}
                {entry.favorable && (
                  <span className="inline-flex items-center border-[1.5px] border-ink bg-signal-lime/55 px-1.5 py-[1px] font-mono text-[10px] font-bold uppercase tracking-wider text-ink">
                    {entry.favorable}
                  </span>
                )}
              </div>
            </div>
            <p className="text-[13px] leading-relaxed text-ink/85">
              {entry.definition}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DocsDisclaimerSection() {
  return (
    <DocSection
      id="docs-disclaimer"
      eyebrow="DOC / 08 · USE & VERIFICATION"
      title="Verification & Disclaimer"
      badge={<RetroBadge tone="red">Experimental</RetroBadge>}
    >
      <Lead>
        AutoOutlook is an experimental, automated forecast. No human meteorologist is in the
        loop.
      </Lead>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.22em] text-ink/55">
            Leakage Guard
          </div>
          <p className="text-[12.5px] leading-snug text-ink/85">
            The pipeline writes prediction artifacts <strong>before</strong> downloading the
            official SPC Day 1 outlook. The official outlook is used only for post-hoc
            verification; it never enters the feature matrix.
          </p>
        </div>
        <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.22em] text-ink/55">
            Decision Use
          </div>
          <p className="text-[12.5px] leading-snug text-ink/85">
            Do not use ACRI as the sole basis for protective action. Defer to your national
            hydrometeorological service. In the United States, the operational convective
            outlook is issued by the NOAA Storm Prediction Center.
          </p>
        </div>
      </div>

      <div className="border-[3px] border-ink bg-paper p-3 shadow-retro-sm">
        <ForecastDisclaimer />
      </div>
    </DocSection>
  );
}
