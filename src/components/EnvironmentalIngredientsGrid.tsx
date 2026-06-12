import type { HourSnapshot, Ingredients } from '../types/forecast';
import { focusLocationFromSnapshot } from '../utils/focusLocation';
import FocusLocationBadge from './FocusLocationBadge';
import RetroPanel from './retro/RetroPanel';

interface EnvironmentalIngredientsGridProps {
  snapshot: HourSnapshot | null;
}

interface MetricSpec {
  key: keyof Ingredients;
  label: string;
  unit: string;
  fmt?: (v: number) => string;
  // a "good for severe" range used to color the bar
  greenAt: number;       // value where bar is fully colored
  invert?: boolean;      // for metrics where lower values are more favorable
  badAt?: number;        // value where an inverted positive metric bottoms out
  cap?: number;
  desc: string;          // hover description
}

const GROUPS: { title: string; metrics: MetricSpec[] }[] = [
  {
    title: 'Instability',
    metrics: [
      { key: 'mlcape', label: 'MLCAPE', unit: 'J/kg', greenAt: 3000, desc: 'Mixed-Layer CAPE. Buoyant energy of a parcel averaged over the lowest 100 mb. Best for mid-day surface-based convection.' },
      { key: 'mucape', label: 'MUCAPE', unit: 'J/kg', greenAt: 3500, desc: 'Most-Unstable CAPE. Buoyancy of the most unstable parcel in the lowest 300 mb. Captures elevated convection.' },
      { key: 'sbcape', label: 'SBCAPE', unit: 'J/kg', greenAt: 3000, desc: 'Surface-Based CAPE. Buoyancy of a parcel lifted from the surface. Sensitive to surface inversions.' },
      { key: 'cin',    label: 'CIN',    unit: 'J/kg', greenAt: -200, invert: true, fmt: (v) => Math.round(v).toString(), desc: 'Convective Inhibition. Negative energy suppressing convection. Weak CIN favors initiation.' },
    ],
  },
  {
    title: 'Moisture',
    metrics: [
      { key: 'sfcDewpointF',   label: 'Sfc Td',         unit: '°F', greenAt: 70, desc: 'Surface dewpoint. Low-level moisture available to feed storm inflow.' },
      { key: 'pwatIn',         label: 'PWAT',           unit: 'in', greenAt: 1.7, fmt: (v) => v.toFixed(2), desc: 'Precipitable Water. Total column water vapor depth. Higher values indicate heavy rain/moisture depth.' },
      { key: 'lclM',           label: 'LCL',            unit: 'm AGL', greenAt: 800, invert: true, badAt: 2200, desc: 'Lifted Condensation Level. Cloud base height. Lower heights (<1000m) favor tornado formation.' },
      { key: 'moistureDepthM', label: 'PWAT depth proxy', unit: 'm',  greenAt: 3500, desc: 'Moisture depth proxy. Measures thickness of the boundary layer moisture feed.' },
    ],
  },
  {
    title: 'Kinematics',
    metrics: [
      { key: 'srh01',          label: '0–1 km SRH',     unit: 'm²/s²', greenAt: 250, desc: '0–1 km Storm-Relative Helicity. Low-level shear. High values (>150–250) favor near-surface rotation.' },
      { key: 'srh03',          label: '0–3 km SRH',     unit: 'm²/s²', greenAt: 400, desc: '0–3 km Storm-Relative Helicity. Deep-layer helicity supporting supercell rotation and longevity.' },
      { key: 'shear06Kt',      label: 'Sfc–500 Shear',  unit: 'kt',    greenAt: 50, desc: '0–6 km bulk wind shear. Defines storm organization: values >= 35–40 kt favor supercells.' },
      { key: 'stormRelWindKt', label: 'SR wind proxy',  unit: 'kt',    greenAt: 40, desc: 'Storm-relative inflow proxy. Estimates flow rate of warm, moist air into the updraft core.' },
    ],
  },
  {
    title: 'Forcing & Storm Mode',
    metrics: [], // rendered specially below
  },
  {
    title: 'Composite Signals',
    metrics: [
      { key: 'stp',              label: 'STP',  unit: '',  greenAt: 4, fmt: (v) => v.toFixed(1), desc: 'Significant Tornado Parameter. Composite parameter. Values >= 1 favor significant tornadoes.' },
      { key: 'scp',              label: 'SCP',  unit: '',  greenAt: 8, fmt: (v) => v.toFixed(1), desc: 'Supercell Composite Parameter. Combines instability, shear, and storm-relative helicity.' },
      { key: 'ehi',              label: 'EHI',  unit: '',  greenAt: 4, fmt: (v) => v.toFixed(1), desc: 'Energy Helicity Index. Blends instability (CAPE) and low-level shear (SRH) into a single index.' },
      { key: 'ship',             label: 'SHIP', unit: '',  greenAt: 3, fmt: (v) => v.toFixed(1), desc: 'Significant Hail Parameter. Composite index highlighting environments favorable for large hail (>2").' },
      { key: 'tornadoComposite', label: 'TorComp', unit: '', greenAt: 3, fmt: (v) => v.toFixed(1), desc: 'Internal tornado composite blending machine learning hazard assessment with severity indices.' },
    ],
  },
];

export default function EnvironmentalIngredientsGrid({ snapshot }: EnvironmentalIngredientsGridProps) {
  const focus = focusLocationFromSnapshot(snapshot);
  return (
    <RetroPanel
      title="Environmental Ingredients"
      eyebrow="06 / HRRR fields + derived proxies"
      badge={<FocusLocationBadge focus={focus} />}
    >
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {GROUPS.map((g) => (
          <div key={g.title} className="border-[3px] border-ink bg-paper flex flex-col shadow-retro">
            <div className="border-b-[3px] border-ink bg-ink text-paper px-3 py-1.5 flex items-center justify-between select-none">
              <span className="font-display font-black uppercase text-[13px] md:text-sm tracking-wider text-signal-amber">
                {g.title}
              </span>
              <div className="flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-signal-lime animate-pulse" />
                <span className="font-mono text-[9px] uppercase tracking-widest text-signal-lime font-bold">
                  SYS OK
                </span>
              </div>
            </div>
            <div className="p-3 grid grid-cols-2 gap-3 flex-1 bg-paper/30">
              {g.title === 'Forcing & Storm Mode' ? (
                <ForcingCards snapshot={snapshot} />
              ) : (
                g.metrics.map((m) => (
                  <MetricCard key={m.key as string} spec={m} snapshot={snapshot} />
                ))
              )}
            </div>
          </div>
        ))}
      </div>
    </RetroPanel>
  );
}

function MetricCard({ spec, snapshot }: { spec: MetricSpec; snapshot: HourSnapshot | null }) {
  const raw = snapshot ? (snapshot.ingredients[spec.key] as number) : 0;
  const display = spec.fmt ? spec.fmt(raw) : String(Math.round(raw));
  const pct = metricPercent(raw, spec);
  return (
    <div className="group relative overflow-visible flex flex-col">
      <div className="border-[2px] border-signal-amber bg-ink p-2 shadow-retro-sm flex flex-col gap-1.5 select-none transform transition-all duration-200 hover:-translate-y-0.5 hover:shadow-retro cursor-default min-w-0 justify-between flex-1">
        <div>
          <div className="flex items-baseline justify-between gap-1">
            <span className="font-mono text-[9.5px] font-bold uppercase tracking-wider text-signal-lime truncate">
              {spec.label}
            </span>
            <span className="font-mono text-[8.5px] font-bold tracking-wider text-signal-lime/75 truncate">{spec.unit}</span>
          </div>
          <div className="font-mono font-black text-lg leading-none text-signal-lime tracking-wide mt-1 truncate">
            {display}
          </div>
        </div>
        <Bar pct={pct} />
      </div>

      {/* Neo-Brutalist Floating Tooltip */}
      <div className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-52 -translate-x-1/2 scale-90 opacity-0 transition-all duration-200 group-hover:scale-100 group-hover:opacity-100">
        <div className="border-[2px] border-signal-amber bg-ink text-signal-lime px-2.5 py-1.5 font-mono text-[9px] leading-normal shadow-[4px_4px_0_0_#9ad62a]">
          {spec.desc}
        </div>
      </div>
    </div>
  );
}

function metricPercent(raw: number, spec: MetricSpec): number {
  if (!Number.isFinite(raw)) return 0;

  if (spec.invert) {
    if (spec.greenAt < 0) {
      return Math.max(0, Math.min(1, Math.abs(raw) / Math.abs(spec.greenAt)));
    }
    const badAt = spec.badAt ?? spec.greenAt * 2.5;
    return Math.max(0, Math.min(1, (badAt - raw) / Math.max(1, badAt - spec.greenAt)));
  }

  const denominator = spec.cap ?? spec.greenAt;
  return Math.max(0, Math.min(1, raw / denominator));
}

function Bar({ pct }: { pct: number }) {
  const segs = 12;
  return (
    <div className="h-3.5 bg-ink border-[2px] border-ink flex gap-[1px] p-[1.5px] select-none mt-1">
      {Array.from({ length: segs }).map((_, i) => {
        const isLit = i / segs < pct;
        let colorClass = '';
        if (i < 6) {
          colorClass = isLit
            ? 'bg-signal-lime shadow-[0_0_4px_rgba(154,214,42,0.6)]'
            : 'bg-signal-lime/10 border-t border-signal-lime/5';
        } else if (i < 9) {
          colorClass = isLit
            ? 'bg-signal-amber shadow-[0_0_4px_rgba(247,181,0,0.6)]'
            : 'bg-signal-amber/10 border-t border-signal-amber/5';
        } else {
          colorClass = isLit
            ? 'bg-signal-red shadow-[0_0_4px_rgba(239,59,44,0.6)]'
            : 'bg-signal-red/10 border-t border-signal-red/5';
        }
        return (
          <div
            key={i}
            className={`flex-1 h-full transition-all duration-300 ${colorClass}`}
            aria-hidden
          />
        );
      })}
    </div>
  );
}

function ForcingCards({ snapshot }: { snapshot: HourSnapshot | null }) {
  const i = snapshot?.ingredients;

  const getForcingTone = () => {
    const s = i?.frontSignal;
    if (s === 'strong') return 'border-signal-lime text-signal-lime bg-signal-lime/5 shadow-[0_0_4px_rgba(154,214,42,0.4)]';
    if (s === 'moderate') return 'border-signal-amber text-signal-amber bg-signal-amber/5 shadow-[0_0_4px_rgba(247,181,0,0.4)]';
    if (s === 'weak') return 'border-signal-cyan text-signal-cyan bg-signal-cyan/5 shadow-[0_0_4px_rgba(22,193,255,0.4)]';
    return 'border-signal-lime/30 text-signal-lime/60 bg-transparent';
  };

  const getInitTone = () => {
    if (!i) return 'border-signal-lime/30 text-signal-lime/60 bg-transparent';
    if (i.initiationConf > 0.6) return 'border-signal-lime text-signal-lime bg-signal-lime/5 shadow-[0_0_4px_rgba(154,214,42,0.4)]';
    if (i.initiationConf > 0.4) return 'border-signal-amber text-signal-amber bg-signal-amber/5 shadow-[0_0_4px_rgba(247,181,0,0.4)]';
    return 'border-signal-lime/30 text-signal-lime/60 bg-transparent';
  };

  const getCapTone = () => {
    const s = i?.capStrength;
    if (s === 'strong') return 'border-signal-red text-signal-red bg-signal-red/5 shadow-[0_0_4px_rgba(239,59,44,0.4)]';
    if (s === 'moderate') return 'border-signal-amber text-signal-amber bg-signal-amber/5 shadow-[0_0_4px_rgba(247,181,0,0.4)]';
    return 'border-signal-lime/30 text-signal-lime/60 bg-transparent';
  };

  const rows = [
    {
      label: 'Front / Dryline',
      value: (i?.frontSignal ?? '—').toUpperCase(),
      styleClass: getForcingTone(),
      desc: 'Strength of surface boundaries (fronts, drylines, outflow) focusing lift and convection initiation.',
    },
    {
      label: 'Initiation Conf.',
      value: i ? `${Math.round(i.initiationConf * 100)}%` : '—',
      styleClass: getInitTone(),
      desc: 'Composite confidence that storms initiate over the focus region, weighing boundary lift, cap relief, and instability.',
    },
      {
        label: 'Storm Mode',
        value: (i?.stormMode ?? '—').toUpperCase(),
        styleClass: 'border-signal-lime/30 text-signal-lime/90 bg-transparent',
        desc: 'Expected convective storm character (discrete supercells, multicell, linear, or mixed) framing likely hazards.',
      },
    {
      label: 'Capping',
      value: (i?.capStrength ?? '—').toUpperCase(),
      styleClass: getCapTone(),
      desc: 'Warm layer strength aloft resisting storm initiation. Strong caps may entirely suppress convection.',
    },
  ];

  return (
    <>
      {rows.map((r) => (
        <div key={r.label} className="group relative overflow-visible flex flex-col">
          <div className="border-[2px] border-signal-amber bg-ink p-2 shadow-retro-sm flex flex-col justify-between select-none transform transition-all duration-200 hover:-translate-y-0.5 hover:shadow-retro cursor-default min-w-0 flex-1">
            <span className="font-mono text-[9.5px] font-bold uppercase tracking-wider text-signal-lime truncate">
              {r.label}
            </span>
            <div className={`mt-2 inline-block self-start border-[2px] px-2 py-0.5 font-mono font-black text-[11px] tracking-widest ${r.styleClass} truncate max-w-full`}>
              {r.value}
            </div>
          </div>

          {/* Neo-Brutalist Floating Tooltip */}
          <div className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-52 -translate-x-1/2 scale-90 opacity-0 transition-all duration-200 group-hover:scale-100 group-hover:opacity-100">
            <div className="border-[2px] border-signal-amber bg-ink text-signal-lime px-2.5 py-1.5 font-mono text-[9px] leading-normal shadow-[4px_4px_0_0_#9ad62a]">
              {r.desc}
            </div>
          </div>
        </div>
      ))}
    </>
  );
}
