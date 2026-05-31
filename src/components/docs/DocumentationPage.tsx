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
  { code: 'MOD',  blurb: 'Widespread severe weather. Strong/significant tornadoes, very large hail, or widespread damaging winds expected.',           tornado: '≥ 30%', hail: '≥ 60%', wind: '≥ 60%' },
  { code: 'HIGH', blurb: 'Outbreak-level threat. Long-track strong tornadoes or extensive derecho-scale wind events likely.',                          tornado: '≥ 45%', hail: 'not used', wind: '60% + sig' },
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

interface ResearchSource {
  title: string;
  category: string;
  source: string;
  href: string;
  usedFor: string;
}

const RESEARCH_SOURCES: ResearchSource[] = [
  {
    title: 'SPC Severe Weather Parameters',
    category: 'SPC / Operations',
    source: 'NOAA Storm Prediction Center',
    href: 'https://origin-west-www-spc.woc.noaa.gov/exper/mesoanalysis/help/begin.html',
    usedFor: 'Base ingredient definitions for instability, shear, storm-relative winds, SCP, STP, and SPC-style parameter interpretation.',
  },
  {
    title: 'Significant Tornado Parameter',
    category: 'Tornado Composite',
    source: 'NOAA Storm Prediction Center',
    href: 'https://origin-west-www-spc.woc.noaa.gov/exper/soundings/help/stp.html',
    usedFor: 'STP term weights, shear handling, CIN handling, and the idea that significant tornado potential requires overlapping ingredients.',
  },
  {
    title: 'Close-Proximity Supercell Soundings',
    category: 'Supercell Soundings',
    source: 'Thompson et al., Weather and Forecasting, 2003',
    href: 'https://training.weather.gov/wdtd/courses/woc/severe/storm-structures-hazards/storm-modes/hodograph-srh/story_content/external_files/ruc_waf.pdf',
    usedFor: 'Supercell environment calibration for CAPE, shear, SRH, LCL, and tornadic versus nontornadic parameter overlap.',
  },
  {
    title: 'SCP / STP Parameter Update',
    category: 'Composite Update',
    source: 'Thompson, Edwards, and Mead, SPC, 2004',
    href: 'https://ams.confex.com/ams/pdfpapers/82100.pdf',
    usedFor: 'Updated SCP/STP formulation logic, including effective shear, effective SRH, and the CIN term used to reduce broad false-alarm areas.',
  },
  {
    title: 'MetPy Significant Tornado',
    category: 'Calculation Check',
    source: 'Unidata MetPy',
    href: 'https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.significant_tornado.html',
    usedFor: 'Cross-checking fixed-layer STP units and threshold behavior against a maintained meteorological calculation library.',
  },
  {
    title: 'MetPy Supercell Composite',
    category: 'Calculation Check',
    source: 'Unidata MetPy',
    href: 'https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.supercell_composite.html',
    usedFor: 'Cross-checking SCP ingredient normalization for instability, storm-relative helicity, and deep-layer shear.',
  },
  {
    title: 'MetPy Storm-Relative Helicity',
    category: 'Calculation Check',
    source: 'Unidata MetPy',
    href: 'https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.storm_relative_helicity.html',
    usedFor: 'Reference calculation for SRH, which supports low-level rotation, mesocyclone, and tornado-favorability wording.',
  },
  {
    title: 'MetPy Bulk Shear',
    category: 'Calculation Check',
    source: 'Unidata MetPy',
    href: 'https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.bulk_shear.html',
    usedFor: 'Reference calculation for deep-layer bulk shear used in organized storm, supercell, and storm-mode interpretation.',
  },
  {
    title: 'MetPy LCL',
    category: 'Calculation Check',
    source: 'Unidata MetPy',
    href: 'https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.lcl.html',
    usedFor: 'Reference calculation for lifted condensation level, used in cloud-base and tornado-environment interpretation.',
  },
  {
    title: 'Baseline Supercell Parameter Climatology',
    category: 'Parameter Climatology',
    source: 'Rasmussen and Blanchard, Weather and Forecasting, 1998',
    href: 'https://training.weather.gov/wdtd/courses/rac/severe/parameters/rasmussenAndBlanchard1998.pdf',
    usedFor: 'Context for sounding-derived supercell and tornado parameters, including CAPE, SRH, shear, EHI, VGP, and LCL interpretation.',
  },
  {
    title: 'Baseline Severe Parameter Climatology',
    category: 'Parameter Climatology',
    source: 'Craven and Brooks, National Weather Digest, 2004',
    href: 'https://www.nssl.noaa.gov/users/brooks/public_html/papers/cravenbrooksnwa.pdf',
    usedFor: 'Broad severe-weather parameter ranges for thunderstorms, severe storms, significant hail/wind, and significant tornado environments.',
  },
  {
    title: 'Storm-Relative Helicity',
    category: 'SRH / Rotation',
    source: 'Davies-Jones, Burgess, and Foster, NOAA/NWS, 1990',
    href: 'https://repository.library.noaa.gov/view/noaa/7309',
    usedFor: 'Low-level rotation and storm-relative helicity concepts used in tornado and supercell-favorability wording.',
  },
  {
    title: 'NWS Helicity Glossary',
    category: 'SRH / Rotation',
    source: 'NOAA National Weather Service',
    href: 'https://forecast.weather.gov/glossary.php?word=helicity',
    usedFor: 'Plain-language SRH definition and operational threshold context for mid-level rotation and mesocyclone potential.',
  },
  {
    title: 'Supercell Motion Hodograph Technique',
    category: 'Storm Motion',
    source: 'Bunkers et al., Weather and Forecasting, 2000',
    href: 'https://www.weather.gov/media/unr/soo/scm/BKZTW00.pdf',
    usedFor: 'Supercell motion and storm-relative inflow context behind SRH and right-moving supercell assumptions.',
  },
  {
    title: 'Storm Shear and Buoyancy',
    category: 'Shear / Instability',
    source: 'Weisman and Klemp, Monthly Weather Review, 1982',
    href: 'https://doi.org/10.1175/1520-0493(1982)110%3C0504:TDONSC%3E2.0.CO;2',
    usedFor: 'Deep-layer shear and buoyancy relationships used to distinguish weak convection, multicells, and organized severe storms.',
  },
  {
    title: 'Strong Long-Lived Squall Lines',
    category: 'Linear Mode',
    source: 'Rotunno, Klemp, and Weisman, Journal of the Atmospheric Sciences, 1988',
    href: 'https://doi.org/10.1175/1520-0469(1988)045%3C0463:ATFSLL%3E2.0.CO;2',
    usedFor: 'QLCS and squall-line context for linear storm-mode wording and shear/cold-pool organization.',
  },
  {
    title: 'Convective Inhibition',
    category: 'Capping',
    source: 'NOAA / NWS Fort Worth',
    href: 'https://www.weather.gov/fwd/convectiveparameterscin',
    usedFor: 'Capping language and CIN interpretation: CIN as the strength of the cap, with small CIN more easily breakable.',
  },
  {
    title: 'NWS Weather Glossary',
    category: 'Moisture / Thermodynamics',
    source: 'NOAA National Weather Service',
    href: 'https://www.weather.gov/otx/Full_Weather_Glossary',
    usedFor: 'Reference wording for dewpoint, precipitable water, lifted index, LCL, and moisture-related forecast terms.',
  },
  {
    title: 'NWS Precipitable Water',
    category: 'Moisture / PWAT',
    source: 'NOAA National Weather Service',
    href: 'https://forecast.weather.gov/glossary.php?word=pw',
    usedFor: 'PWAT definition used for deep moisture and heavy-rain-favorability language.',
  },
  {
    title: 'NWS Thunderstorm Ingredients',
    category: 'Ingredients Framework',
    source: 'NOAA National Weather Service',
    href: 'https://www.weather.gov/source/zhu/ZHU_Training_Page/thunderstorm_stuff/Thunderstorms/thunderstorms.htm',
    usedFor: 'Operational framing for moisture, instability, lift, and storm-relative wind shear as severe-thunderstorm ingredients.',
  },
  {
    title: 'NWS Dryline Glossary',
    category: 'Forcing / Boundaries',
    source: 'NOAA National Weather Service',
    href: 'https://www.weather.gov/ggw/GlossaryD',
    usedFor: 'Dryline definition and severe-weather relevance for boundary forcing and initiation wording.',
  },
  {
    title: 'NWS Outflow Boundary Reference',
    category: 'Forcing / Boundaries',
    source: 'NOAA National Weather Service',
    href: 'https://www.weather.gov/gid/29139',
    usedFor: 'Outflow-boundary definition and boundary-interaction context for initiation and local storm enhancement.',
  },
  {
    title: 'NSHARP Hail and Tornado Reference',
    category: 'Hail / Tornado Tools',
    source: 'NOAA Virtual Lab',
    href: 'https://vlab.noaa.gov/web/oclo/nsharp-hail-and-tornado-reference',
    usedFor: 'Operational sounding-tool context for significant hail and tornado parameters, including SHIP/STP display conventions.',
  },
  {
    title: 'Large Hail Environments and SHIP',
    category: 'Hail Composite',
    source: 'Tang et al., npj Climate and Atmospheric Science, 2019',
    href: 'https://www.nature.com/articles/s41612-019-0103-7',
    usedFor: 'Context for large-hail environments and SHIP/LHP as hail-discrimination parameters.',
  },
  {
    title: 'Conditional Severe Hail and Wind Intensity',
    category: 'Hail / Wind Intensity',
    source: 'Jirak et al., NOAA/SPC',
    href: 'https://origin-west-www-spc.woc.noaa.gov/publications/jirak/cond-int.pdf',
    usedFor: 'Significant hail and wind intensity context behind higher-end severe hazard wording.',
  },
  {
    title: 'Short-Term Convective Mode Evolution',
    category: 'Storm Mode',
    source: 'Dial, Racy, and Thompson, Weather and Forecasting, 2010',
    href: 'https://training.weather.gov/wdtd/courses/woc/severe/storm-structures-hazards/storm-modes/understanding-sm/story_content/external_files/Dialetal2010.pdf',
    usedFor: 'Storm-mode wording: boundary forcing matters, but linear, discrete, and mixed modes depend on shear, wind orientation, forcing, and residence time near the boundary.',
  },
  {
    title: 'SPC Convective Outlooks',
    category: 'Outlook Conventions',
    source: 'NOAA Storm Prediction Center',
    href: 'https://www.spc.noaa.gov/misc/SPC_probotlk_info.html',
    usedFor: 'SPC-style categorical outlook framing, hazard probabilities, and significant-severe hatch terminology.',
  },
  {
    title: 'SPC Severe Weather Reports',
    category: 'Reports / Verification',
    source: 'NOAA Storm Prediction Center',
    href: 'https://origin-west-www-spc.woc.noaa.gov/wcm/index.html',
    usedFor: 'Historical tornado, hail, and wind report context used for verification, climatology, and severe-weather outcome definitions.',
  },
  {
    title: 'High-Resolution Rapid Refresh',
    category: 'Model Data',
    source: 'NOAA Global Systems Laboratory',
    href: 'https://rapidrefresh.noaa.gov/hrrr/',
    usedFor: 'HRRR model context for hourly, convection-allowing fields that feed the environmental ingredient dashboard.',
  },
  {
    title: 'Rapid Refresh / HRRR Archive',
    category: 'Model Data',
    source: 'NOAA National Centers for Environmental Information',
    href: 'https://www.ncei.noaa.gov/products/weather-climate-models/rapid-refresh-update',
    usedFor: 'Archive and model-family context for RAP/HRRR fields used in historical gathering and forecast artifacts.',
  },
  {
    title: 'HRRR System Description',
    category: 'Model Data',
    source: 'Dowell et al., Weather and Forecasting / NOAA Repository',
    href: 'https://repository.library.noaa.gov/view/noaa/53029',
    usedFor: 'Technical background for HRRR as an hourly updated, convection-allowing forecast model.',
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
  note?: string;
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
    blurb: 'Surface boundaries, initiation confidence, capping, and the expected storm character.',
    entries: [
      { term: 'Front signal',     unit: '',  definition: 'Strength of a surface boundary such as a cold front, dryline, outflow boundary, or triple point. Stronger boundaries focus lift and initiation.' },
      { term: 'Initiation Conf.', unit: '%', favorable: '≥ 60', definition: 'Confidence that storms initiate over the focus region during the forecast hour. It weighs boundary support, cap relief, moisture, and instability together.' },
      { term: 'Storm Mode',       unit: '',  definition: 'Expected dominant mode: discrete supercells, multicell clusters, linear QLCS, or mixed. Mode helps frame the likely tornado, hail, and wind threat.' },
      { term: 'Capping',          unit: '',  definition: 'Strength of the inhibiting warm layer aloft, based on CIN magnitude. Weak or moderate caps can still break; strong caps may suppress storms even in unstable air.' },
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
    <div className="flex min-w-0 flex-col">
      <DocsHero />
      <main className="flex w-full min-w-0 flex-1 flex-col gap-3 px-3 py-2 sm:px-4 xl:gap-4 xl:px-5">
        <DocsOverview />
        <DocsLevels />
        <DocsPerformance />
        <DocsSpcQc />
        <DocsPredictability />
        <DocsHazards />
        <DocsSources />
        <DocsGlossary />
        <DocsDisclaimerSection />
        <DocsResearchSources />
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
          <RetroBadge tone="ink">v0.6</RetroBadge>
        </div>
      </div>
      <div className="border-t-[2px] border-paper/20 bg-ink px-4 py-1.5 xl:px-5">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/55">
          Ten sections · Architecture · Levels · Skill · SPC QC · Predictability · Bands · Providers · Glossary · Disclaimer · Research
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
        a parameter dashboard, an auto-generated forecast discussion, an SPC QC console,
        and a system-status readout, all refreshed every 15 minutes.
      </Body>

      <StatGrid
        items={[
          { label: 'Forecast Horizon', value: '0 – 48 h' },
          { label: 'SPC Compare',      value: '3 modes' },
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

function DocsSpcQc() {
  return (
    <DocSection
      id="docs-spc-qc"
      eyebrow="DOC / 04 · SPC DAY 1 COMPARISON"
      title="SPC QC Console"
      badge={<RetroBadge tone="lime">v0.6</RetroBadge>}
    >
      <Lead>
        v0.6 turns the SPC Day 1 verification artifact into an operator-facing QC panel
        and a direct map comparison mode.
      </Lead>

      <Body>
        The backend emits <Mono>verification_summary.json</Mono> after AutoOutlook artifacts
        are generated. The frontend reads that summary and renders the agreement percentage,
        underforecast and overforecast cell counts, category ledgers, SPC forecaster metadata,
        valid/expiration timestamps, and leakage-guard status.
      </Body>

      <StatGrid
        items={[
          { label: 'Map Modes', value: '3' },
          { label: 'QC Cards', value: '3' },
          { label: 'Risk Rows', value: '7' },
          { label: 'Leakage Guard', value: 'Post' },
        ]}
      />

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <SpcQcCard
          title="SPC Agreement"
          badge="QC"
          body="A compact calibration card shows the agreement percentage, aligned cells, evaluated cells, and whether the official SPC outlook was fetched only after prediction artifacts were ready."
        />
        <SpcQcCard
          title="Displacement Ratio"
          badge="UNF / OVF"
          body="Underforecast means SPC risk exceeds AutoOutlook. Overforecast means AutoOutlook exceeds SPC. Hover or focus the cards to read those definitions in-app."
        />
        <SpcQcCard
          title="Category Ledger"
          badge="All Risks"
          body="Every category from NONE through HIGH stays visible, even when the count is zero, so users can quickly compare AutoOutlook and SPC distribution by tier."
        />
      </div>

      <div className="border-[3px] border-ink bg-paper p-3 shadow-retro-sm">
        <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.22em] text-ink/55">
          Overlay Comparison Modes
        </div>
        <DocList>
          <DocListItem><Mono>AutoOutlook only</Mono> shows the generated categorical risk contours.</DocListItem>
          <DocListItem><Mono>SPC Day 1 only</Mono> shows the official SPC categorical boundaries for the current Day 1 product.</DocListItem>
          <DocListItem><Mono>Overlay compare</Mono> combines both layers and uses bounded QC hatches for agreement, underforecast, and overforecast regions.</DocListItem>
        </DocList>
      </div>

      <Body>
        The overlay hatches are diagnostic, not replacement outlooks. The official SPC
        product remains the authoritative operational forecast; AutoOutlook uses it only
        to explain calibration after the automated run is already complete.
      </Body>
    </DocSection>
  );
}

function SpcQcCard({
  title,
  badge,
  body,
}: {
  title: string;
  badge: string;
  body: string;
}) {
  return (
    <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="font-display text-[13px] font-extrabold uppercase tracking-widest text-ink">
          {title}
        </span>
        <span className="shrink-0 border-[2px] border-ink bg-signal-lime px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-widest text-ink">
          {badge}
        </span>
      </div>
      <p className="text-[12.5px] leading-snug text-ink/75">{body}</p>
    </div>
  );
}

function DocsPredictability() {
  return (
    <DocSection
      id="docs-predictability"
      eyebrow="DOC / 05 · WHAT 0–48 H ACTUALLY MEANS"
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
      eyebrow="DOC / 06 · PROBABILITY CONTOURS"
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
        Significant-severe (the SPC SIG hatch) is a separate signal, not the
        same thing as the ordinary hazard probability. It activates at{' '}
        <Mono>≥ 10%</Mono> probability of EF2+ tornadoes, hail at least 2 in,
        or convective wind at least 65 kt. General thunder and flood do not
        carry a SIG layer.
      </Body>

      <div className="border-[2px] border-ink bg-paper p-3 shadow-retro-sm">
        <div className="flex items-center justify-between">
          <span className="font-display text-[13px] font-extrabold uppercase tracking-widest text-ink">
            SIG Layer
          </span>
          <span className="font-mono text-[10px] uppercase tracking-widest text-ink/50">
            Significant Severe
          </span>
        </div>

        <p className="mt-2 text-[12.5px] leading-snug text-ink/75">
          SIG is not a separate hazard category. It marks the favored corridor where the
          active tornado, hail, or wind threat may reach significant-severe criteria:
          EF2+ tornadoes, hail at least 2 inches in diameter, or thunderstorm gusts of at
          least 74 mph.
        </p>

        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <SigOffsetCard
            hazard="Tornado"
            focus="Warm-sector / triple-point"
            rationale="Favored where low-level rotation, moisture, and supercell support overlap."
          />
          <SigOffsetCard
            hazard="Hail"
            focus="Dryline / lapse-rate axis"
            rationale="Favored where strong instability, steep lapse rates, and organized updrafts overlap."
          />
          <SigOffsetCard
            hazard="Wind"
            focus="Downshear / QLCS corridor"
            rationale="Favored where organized storm lines and strong flow support higher-end gusts."
          />
        </div>

        <p className="mt-3 text-[12.5px] leading-snug text-ink/75">
          The hatch should be read as a favored significant-severe corridor, not a precise
          warning boundary. Its shape follows the strongest part of the threat and can shift
          or stretch from hour to hour as the environment changes.
        </p>
      </div>
    </DocSection>
  );
}

function SigOffsetCard({
  hazard,
  focus,
  rationale,
}: {
  hazard: string;
  focus: string;
  rationale: string;
}) {
  return (
    <div className="border-[1.5px] border-ink bg-paper p-2 shadow-retro-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-display text-[11px] font-extrabold uppercase tracking-widest text-ink">
          {hazard}
        </span>
        <span className="max-w-[70%] text-right font-mono text-[10px] font-bold leading-tight tracking-wider text-ink/80">
          {focus}
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
      eyebrow="DOC / 07 · WHERE THE DATA COMES FROM"
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
      eyebrow="DOC / 08 · PARAMETER DICTIONARY"
      title="Ingredients Glossary"
      badge={<RetroBadge tone="cyan">{totalTerms} terms</RetroBadge>}
    >
      <Lead>
        Every parameter on the Environmental Ingredients board, grouped by meteorological
        role and annotated with the value the dashboard considers strongly favorable for
        organized severe convection.
      </Lead>

      <GlossaryLegend />

      <div className="grid grid-cols-1 gap-3 xl:hidden">
        {GLOSSARY.map((group) => (
          <GlossaryGroupCard key={group.title} group={group} />
        ))}
      </div>
      <div className="hidden grid-cols-2 items-start gap-3 xl:grid">
        <div className="flex flex-col gap-3">
          {[GLOSSARY[0], GLOSSARY[2], GLOSSARY[4]].map((group) => (
            <GlossaryGroupCard key={group.title} group={group} />
          ))}
        </div>
        <div className="flex flex-col gap-3">
          {[GLOSSARY[1], GLOSSARY[3]].map((group) => (
            <GlossaryGroupCard key={group.title} group={group} />
          ))}
        </div>
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
    <div className="self-start overflow-hidden border-[3px] border-ink bg-paper shadow-retro">
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

      {group.note && (
        <div className="border-b-[1.5px] border-ink/20 bg-signal-orange/20 px-4 py-3">
          <p className="font-mono text-[10.5px] uppercase leading-relaxed tracking-[0.12em] text-ink/75">
            {group.note}
          </p>
        </div>
      )}

      <ul className="flex flex-col">
        {group.entries.map((entry, idx) => (
          <li
            key={entry.term}
            className={`grid grid-cols-1 gap-2 px-3 py-3 md:grid-cols-[160px_minmax(0,1fr)] md:gap-4 ${idx > 0 ? 'border-t-[1.5px] border-ink/15' : ''}`}
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
            <p className="min-w-0 text-[13px] leading-relaxed text-ink/85">
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
      eyebrow="DOC / 09 · USE & VERIFICATION"
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

function DocsResearchSources() {
  return (
    <DocSection
      id="docs-research"
      eyebrow="DOC / 10 · FORMULATION REFERENCES"
      title="Research Sources"
      badge={<RetroBadge tone="lime">{RESEARCH_SOURCES.length} refs</RetroBadge>}
    >
      <Lead>
        The Environmental Ingredients board is backed by operational SPC guidance,
        maintained MetPy calculations, NWS meteorological references, HRRR model
        documentation, and peer-reviewed severe-storm research.
      </Lead>

      <Body>
        This catalog covers the displayed instability, moisture, kinematic, forcing,
        storm-mode, composite, hazard-threshold, and model-data assumptions. The
        dashboard uses these sources for formulation alignment and plain-language
        documentation; official forecasts and warnings still come from NOAA/NWS and
        local hydrometeorological services.
      </Body>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {RESEARCH_SOURCES.map((item) => (
          <ResearchSourceCard key={item.href} item={item} />
        ))}
      </div>
    </DocSection>
  );
}

function ResearchSourceCard({ item }: { item: ResearchSource }) {
  return (
    <a
      href={item.href}
      target="_blank"
      rel="noreferrer"
      className="group flex min-h-[178px] flex-col justify-between border-[2px] border-ink bg-paper p-3 shadow-retro-sm transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:bg-signal-lime/35 hover:shadow-retro"
    >
      <div>
        <div className="mb-2 flex items-start justify-between gap-2">
          <div>
            <span className="mb-1.5 inline-block max-w-full border-[1.5px] border-ink bg-signal-lime/45 px-1.5 py-[1px] font-mono text-[8px] font-bold uppercase leading-tight tracking-[0.18em] text-ink">
              {item.category}
            </span>
            <h3 className="font-display text-[13px] font-extrabold uppercase leading-tight tracking-widest text-ink">
              {item.title}
            </h3>
            <p className="mt-1 font-mono text-[9px] uppercase tracking-[0.2em] text-ink/55">
              {item.source}
            </p>
          </div>
          <span className="shrink-0 border-[2px] border-ink bg-ink px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-widest text-paper group-hover:bg-paper group-hover:text-ink">
            Open
          </span>
        </div>
        <p className="text-[12.5px] leading-snug text-ink/80">{item.usedFor}</p>
      </div>
    </a>
  );
}
