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
}

const GROUPS: { title: string; metrics: MetricSpec[] }[] = [
  {
    title: 'Instability',
    metrics: [
      { key: 'mlcape', label: 'MLCAPE', unit: 'J/kg', greenAt: 3000 },
      { key: 'mucape', label: 'MUCAPE', unit: 'J/kg', greenAt: 3500 },
      { key: 'sbcape', label: 'SBCAPE', unit: 'J/kg', greenAt: 3000 },
      { key: 'cin',    label: 'CIN',    unit: 'J/kg', greenAt: -200, invert: true,
        fmt: (v) => Math.round(v).toString() },
    ],
  },
  {
    title: 'Moisture',
    metrics: [
      { key: 'sfcDewpointF',   label: 'Sfc Td',         unit: '°F', greenAt: 70 },
      { key: 'pwatIn',         label: 'PWAT',           unit: 'in', greenAt: 1.7,
        fmt: (v) => v.toFixed(2) },
      { key: 'lclM',           label: 'LCL',            unit: 'm AGL', greenAt: 800, invert: true, badAt: 2200 },
      { key: 'moistureDepthM', label: 'PWAT depth proxy', unit: 'm',  greenAt: 3500 },
    ],
  },
  {
    title: 'Kinematics',
    metrics: [
      { key: 'srh01',          label: '0–1 km SRH',     unit: 'm²/s²', greenAt: 250 },
      { key: 'srh03',          label: '0–3 km SRH',     unit: 'm²/s²', greenAt: 400 },
      { key: 'shear06Kt',      label: 'Sfc–500 Shear',  unit: 'kt',    greenAt: 50 },
      { key: 'stormRelWindKt', label: 'SR wind proxy',  unit: 'kt',    greenAt: 40 },
    ],
  },
  {
    title: 'Forcing & Storm Mode',
    metrics: [], // rendered specially below
  },
  {
    title: 'Composite Signals',
    metrics: [
      { key: 'stp',              label: 'STP',  unit: '',  greenAt: 4, fmt: (v) => v.toFixed(1) },
      { key: 'scp',              label: 'SCP',  unit: '',  greenAt: 8, fmt: (v) => v.toFixed(1) },
      { key: 'ehi',              label: 'EHI',  unit: '',  greenAt: 4, fmt: (v) => v.toFixed(1) },
      { key: 'ship',             label: 'SHIP', unit: '',  greenAt: 3, fmt: (v) => v.toFixed(1) },
      { key: 'tornadoComposite', label: 'TorComp', unit: '', greenAt: 3, fmt: (v) => v.toFixed(1) },
    ],
  },
];

export default function EnvironmentalIngredientsGrid({ snapshot }: EnvironmentalIngredientsGridProps) {
  const focus = focusLocationFromSnapshot(snapshot);
  return (
    <RetroPanel
      title="Environmental Ingredients"
      eyebrow="05 / HRRR fields + derived proxies"
      badge={<FocusLocationBadge focus={focus} />}
    >
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {GROUPS.map((g) => (
          <div key={g.title} className="border-[3px] border-ink bg-paper">
            <div className="border-b-[3px] border-ink bg-paper px-3 py-1.5 flex items-center justify-between">
              <span className="font-display font-extrabold uppercase text-[12px] tracking-wider">
                {g.title}
              </span>
              <span className="font-mono text-[10px] tracking-widest text-ink/50">
                AUTO
              </span>
            </div>
            <div className="p-2.5 grid grid-cols-2 gap-2">
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
    <div className="border-[2px] border-ink bg-paper p-2 shadow-retro-sm flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-1">
        <span className="font-mono text-[9px] uppercase tracking-widest text-ink/60">
          {spec.label}
        </span>
        <span className="font-mono text-[9px] tracking-widest text-ink/40">{spec.unit}</span>
      </div>
      <div className="font-display font-extrabold text-lg leading-none text-ink">
        {display}
      </div>
      <Bar pct={pct} />
    </div>
  );
}

function metricPercent(raw: number, spec: MetricSpec): number {
  if (!Number.isFinite(raw)) return 0;

  if (spec.invert) {
    // Negative inverted metrics like CIN: stronger cap magnitude fills toward
    // the threshold. Positive inverted metrics like LCL: lower is better.
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
    <div className="h-1.5 border-[1.5px] border-ink relative overflow-hidden">
      {Array.from({ length: segs }).map((_, i) => (
        <div
          key={i}
          className={`absolute top-0 h-full ${
            i / segs < pct ? (pct > 0.7 ? 'bg-signal-red' : pct > 0.4 ? 'bg-signal-amber' : 'bg-signal-lime') : ''
          }`}
          style={{ left: `${(i / segs) * 100}%`, width: `${100 / segs - 0.5}%` }}
          aria-hidden
        />
      ))}
    </div>
  );
}

function ForcingCards({ snapshot }: { snapshot: HourSnapshot | null }) {
  const i = snapshot?.ingredients;
  const rows: { label: string; value: string; tone: string }[] = [
    {
      label: 'Front / Dryline',
      value: (i?.frontSignal ?? '—').toUpperCase(),
      tone: signalTone(i?.frontSignal),
    },
    {
      label: 'Initiation Conf.',
      value: i ? `${Math.round(i.initiationConf * 100)}%` : '—',
      tone: i && i.initiationConf > 0.6 ? 'bg-signal-lime' : i && i.initiationConf > 0.4 ? 'bg-signal-amber' : 'bg-paper',
    },
    {
      label: 'Storm Mode',
      value: (i?.stormMode ?? '—').toUpperCase(),
      tone: 'bg-paper',
    },
    {
      label: 'Capping',
      value: (i?.capStrength ?? '—').toUpperCase(),
      tone: i?.capStrength === 'strong' ? 'bg-signal-red text-paper' :
            i?.capStrength === 'moderate' ? 'bg-signal-amber' :
            'bg-paper',
    },
  ];
  return (
    <>
      {rows.map((r) => (
        <div key={r.label} className="border-[2px] border-ink bg-paper p-2 shadow-retro-sm flex flex-col gap-1">
          <span className="font-mono text-[9px] uppercase tracking-widest text-ink/60">
            {r.label}
          </span>
          <div className={`mt-0.5 inline-block self-start border-[2px] border-ink px-2 py-0.5 font-display font-extrabold text-[12px] tracking-widest ${r.tone}`}>
            {r.value}
          </div>
        </div>
      ))}
    </>
  );
}

function signalTone(s: string | undefined): string {
  if (s === 'strong') return 'bg-signal-lime';
  if (s === 'moderate') return 'bg-signal-amber';
  if (s === 'weak') return 'bg-signal-cyan';
  return 'bg-paper';
}
