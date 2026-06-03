import { useEffect, useState } from 'react';
import type { SpcVerificationSummary, MergedD1VerificationSummary } from '../types/outlookArtifacts';
import RetroPanel from './retro/RetroPanel';

interface VerificationPanelProps {
  spcVerification?: SpcVerificationSummary | null;
  mergedD1Verification?: MergedD1VerificationSummary | null;
  viewType?: 'hourly' | 'merged';
}

const CATEGORY_ORDER = ['NONE', 'TSTM', 'MRGL', 'SLGT', 'ENH', 'MDT', 'HIGH'];

const STATUS_STYLES = {
  aligned: {
    label: 'ALIGNED',
    className: 'bg-signal-lime text-ink border-signal-lime',
  },
  caution: {
    label: 'CALIBRATION WATCH',
    className: 'bg-signal-amber text-ink border-signal-amber',
  },
  drift: {
    label: 'DRIFT DETECTED',
    className: 'bg-signal-red text-paper border-signal-red',
  },
} as const;

export default function VerificationPanel({ spcVerification, mergedD1Verification, viewType }: VerificationPanelProps) {
  const [showDetails, setShowDetails] = useState(false);
  const [activeTab, setActiveTab] = useState<'single' | 'merged'>('single');

  // Sync tab with map panel view type
  useEffect(() => {
    if (viewType === 'merged') {
      setActiveTab('merged');
    } else {
      setActiveTab('single');
    }
  }, [viewType]);

  const activeVerification = activeTab === 'merged' ? mergedD1Verification : spcVerification;
  const agreementFraction = normalizeFraction(activeVerification?.agreementFraction);

  const singleMissing = !spcVerification || normalizeFraction(spcVerification.agreementFraction) === null;

  return (
    <RetroPanel
      title="System Calibration / SPC QC"
      eyebrow="07 / Forecast verification against SPC Day 1"
      scanline
    >
      <div className="mb-4 flex gap-0">
        <button
          type="button"
          onClick={() => setActiveTab('single')}
          className={`border-[3px] border-ink px-4 py-2 font-mono text-[10px] font-black uppercase tracking-widest transition-colors ${activeTab === 'single' ? 'bg-ink text-paper' : 'bg-paper text-ink'}`}
        >
          Single Cycle
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('merged')}
          className={`-ml-[3px] border-[3px] border-ink px-4 py-2 font-mono text-[10px] font-black uppercase tracking-widest transition-colors ${activeTab === 'merged' ? 'bg-ink text-paper' : 'bg-paper text-ink'}`}
        >
          Merged D1 (Multi-Cycle)
        </button>
      </div>

      {activeTab === 'merged' && !mergedD1Verification ? (
        <MissingMergedD1Panel />
      ) : activeTab === 'merged' && mergedD1Verification && agreementFraction !== null ? (
        <MergedD1Content
          verification={mergedD1Verification}
          agreementFraction={agreementFraction}
          showDetails={showDetails}
          setShowDetails={setShowDetails}
        />
      ) : activeTab === 'single' && singleMissing ? (
        <MissingVerificationPanel spcVerification={spcVerification} />
      ) : activeTab === 'single' && spcVerification && normalizeFraction(spcVerification.agreementFraction) !== null ? (
        <SingleCycleContent
          spcVerification={spcVerification}
          agreementFraction={normalizeFraction(spcVerification.agreementFraction)!}
          showDetails={showDetails}
          setShowDetails={setShowDetails}
        />
      ) : null}
    </RetroPanel>
  );
}

function MissingMergedD1Panel() {
  return (
    <div className="border-[3px] border-ink bg-ink p-5 font-mono text-xs text-signal-lime shadow-retro">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-signal-lime/30 pb-2">
        <span className="font-black uppercase tracking-widest text-signal-amber">
          STATUS: MERGED_D1_PENDING
        </span>
        <span className="bg-signal-amber/20 px-2 py-0.5 text-[10px] font-black uppercase tracking-widest text-signal-amber">
          AWAITING MULTI-CYCLE MERGE
        </span>
      </div>
      <p className="leading-relaxed">
        Merged D1 multi-cycle verification is not available yet. This panel will populate once multiple HRRR cycles have been merged and verified against the SPC Day 1 outlook.
      </p>
      <p className="mt-3 leading-relaxed text-signal-lime/70">
        The merge combines the maximum risk category across contributing forecast hours from different model cycles within the SPC Day 1 valid window.
      </p>
    </div>
  );
}

function MergedD1Content({
  verification,
  agreementFraction,
  showDetails,
  setShowDetails,
}: {
  verification: MergedD1VerificationSummary;
  agreementFraction: number;
  showDetails: boolean;
  setShowDetails: (fn: (v: boolean) => boolean) => void;
}) {
  const agreementPct = Math.round(agreementFraction * 100);
  const underforecastCells = safeCount(verification.underforecastCells);
  const overforecastCells = safeCount(verification.overforecastCells);
  const displacementTotal = underforecastCells + overforecastCells;
  const underSharePct = displacementTotal > 0 ? Math.round((underforecastCells / displacementTotal) * 100) : 0;
  const overSharePct = displacementTotal > 0 ? Math.round((overforecastCells / displacementTotal) * 100) : 0;
  const interpretation = displacementInterpretation(underforecastCells, overforecastCells, agreementFraction);
  const status = agreementStatus(agreementFraction);
  const mergedCyclesLabel = verification.mergedCycles?.join(' + ') ?? '--';
  const d1Window = verification.d1WindowValidISO && verification.d1WindowExpireISO
    ? `${formatUtc(verification.d1WindowValidISO)} → ${formatUtc(verification.d1WindowExpireISO)}`
    : '--';

  return (
    <div className="grid gap-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <InfoStrip label="Merged cycles" value={mergedCyclesLabel} />
        <InfoStrip label="D1 window coverage" value={d1Window} />
      </div>

      <VerificationCards
        verification={verification}
        agreementPct={agreementPct}
        underforecastCells={underforecastCells}
        overforecastCells={overforecastCells}
        displacementTotal={displacementTotal}
        underSharePct={underSharePct}
        overSharePct={overSharePct}
        interpretation={interpretation}
        status={status}
      />

      <VerificationFooter
        verification={verification}
        showDetails={showDetails}
        setShowDetails={setShowDetails}
      />
    </div>
  );
}

function SingleCycleContent({
  spcVerification,
  agreementFraction,
  showDetails,
  setShowDetails,
}: {
  spcVerification: SpcVerificationSummary;
  agreementFraction: number;
  showDetails: boolean;
  setShowDetails: (fn: (v: boolean) => boolean) => void;
}) {
  const agreementPct = Math.round(agreementFraction * 100);
  const underforecastCells = safeCount(spcVerification.underforecastCells);
  const overforecastCells = safeCount(spcVerification.overforecastCells);
  const displacementTotal = underforecastCells + overforecastCells;
  const underSharePct = displacementTotal > 0 ? Math.round((underforecastCells / displacementTotal) * 100) : 0;
  const overSharePct = displacementTotal > 0 ? Math.round((overforecastCells / displacementTotal) * 100) : 0;
  const interpretation = displacementInterpretation(underforecastCells, overforecastCells, agreementFraction);
  const status = agreementStatus(agreementFraction);
  const tickerText = [
    `SPC FORECASTER: ${spcVerification.spcForecaster || 'UNKNOWN'}`,
    `ISSUED: ${formatUtc(spcVerification.spcIssueTimeISO)}`,
    `VALID: ${formatUtc(spcVerification.spcValidTimeISO)}`,
    `EXPIRES: ${formatUtc(spcVerification.spcExpireTimeISO)}`,
    `SOURCE: ${spcVerification.source || 'SPC DAY 1 CATEGORICAL OUTLOOK'}`,
  ].join(' // ');

  return (
    <div className="grid gap-4">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(230px,0.32fr)]">
        <div className="min-w-0 border-[3px] border-ink bg-ink text-paper shadow-retro">
          <div className="flex items-center justify-between gap-3 border-b-[2px] border-paper/25 px-3 py-2">
            <span className="font-mono text-[10px] font-black uppercase tracking-[0.3em] text-signal-amber">
              SPC metadata ticker
            </span>
            <span className="border-[2px] border-signal-lime px-2 py-0.5 font-mono text-[9px] font-black uppercase tracking-widest text-signal-lime">
              OK
            </span>
          </div>
          <div className="overflow-hidden py-3">
            <div className="flex w-max animate-ticker whitespace-nowrap font-mono text-[11px] font-bold uppercase tracking-[0.22em] text-signal-lime">
              <span className="pr-10">{tickerText}</span>
              <span className="pr-10" aria-hidden="true">{tickerText}</span>
            </div>
          </div>
        </div>

        <InfoStrip label="Verification fetched" value={formatUtc(spcVerification.spcFetchedAtISO)} />
      </div>

      <VerificationCards
        verification={spcVerification}
        agreementPct={agreementPct}
        underforecastCells={underforecastCells}
        overforecastCells={overforecastCells}
        displacementTotal={displacementTotal}
        underSharePct={underSharePct}
        overSharePct={overSharePct}
        interpretation={interpretation}
        status={status}
      />

      <VerificationFooter
        verification={spcVerification}
        showDetails={showDetails}
        setShowDetails={setShowDetails}
      />
    </div>
  );
}

function VerificationCards({
  verification,
  agreementPct,
  underforecastCells,
  overforecastCells,
  displacementTotal,
  underSharePct,
  overSharePct,
  interpretation,
  status,
}: {
  verification: SpcVerificationSummary;
  agreementPct: number;
  underforecastCells: number;
  overforecastCells: number;
  displacementTotal: number;
  underSharePct: number;
  overSharePct: number;
  interpretation: 'ALIGNED' | 'CONSERVATIVE' | 'AGGRESSIVE' | 'MIXED';
  status: (typeof STATUS_STYLES)[keyof typeof STATUS_STYLES];
}) {
  return (
    <div className="grid grid-cols-1 items-stretch gap-4 xl:grid-cols-[minmax(280px,0.95fr)_minmax(320px,1fr)_minmax(360px,1.05fr)]">
      <section
        data-spc-qc-card="agreement"
        className="flex h-full min-h-[410px] min-w-0 flex-col overflow-visible border-[3px] border-ink bg-ink text-paper shadow-retro"
      >
        <div className="flex min-h-[34px] items-center justify-between gap-3 border-b-[2px] border-paper/25 px-4 py-2">
          <span className="font-mono text-[10px] font-black uppercase tracking-[0.22em] text-signal-amber">
            SPC Agreement
          </span>
          <span className={`max-w-[58%] truncate border-[2px] px-2 py-0.5 font-mono text-[9px] font-black uppercase tracking-widest shadow-retro-sm ${status.className}`}>
            {status.label}
          </span>
        </div>

        <div className="grid flex-1 grid-rows-[1fr_auto_auto] gap-3 p-4">
          <div className="grid min-h-[116px] content-center border-[3px] border-paper bg-paper px-4 py-3 text-ink shadow-retro-sm">
            <p className="font-mono text-[9px] font-black uppercase tracking-[0.24em] text-ink/55">
              Calibration watch / QC reading
            </p>
            <div className="mt-2 grid gap-3 sm:grid-cols-[auto_minmax(0,1fr)] sm:items-center xl:grid-cols-1 2xl:grid-cols-[auto_minmax(0,1fr)]">
              <div className="flex items-end gap-2">
                <span className="font-display text-[4rem] font-black leading-[0.78] tracking-tight">
                  {agreementPct}
                </span>
                <span className="pb-1 font-display text-3xl font-black">%</span>
              </div>
              <div className="border-l-0 border-ink pl-0 sm:border-l-[3px] sm:pl-3 xl:border-l-0 xl:pl-0 2xl:border-l-[3px] 2xl:pl-3">
                <p className="font-display text-lg font-extrabold uppercase leading-tight">
                  {interpretationLabel(interpretation, agreementPct)}
                </p>
                <p className="mt-2 font-mono text-[10px] font-black uppercase tracking-[0.2em] text-ink/50">
                  {formatCount(verification.agreementCells)} aligned / {formatCount(verification.comparisonGridCells)} evaluated
                </p>
              </div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 font-mono text-[10px]">
            <MetricTile
              label="Cells evaluated"
              value={formatCount(verification.comparisonGridCells)}
              description="Grid cells where AutoOutlook or SPC had a nonzero risk and were included in this QC comparison."
            />
            <MetricTile
              label="Cells aligned"
              value={formatCount(verification.agreementCells)}
              description="Cells where the AutoOutlook risk category matched the official SPC Day 1 category."
            />
          </div>
          <LeakageGuardTile requiresCheck={verification.spcFetchedAfterPredictionArtifacts === false} />
        </div>
      </section>

      <div
        data-spc-qc-card="displacement"
        className="flex h-full min-h-[410px] min-w-0 flex-col border-[3px] border-ink bg-paper p-4 shadow-retro"
      >
        <div className="mb-3 flex min-h-[34px] items-center justify-between gap-3 border-b-[2px] border-ink pb-2">
          <span className="font-mono text-[10px] font-black uppercase tracking-[0.22em] text-ink/60">
            Displacement ratio
          </span>
          <span className="border-[2px] border-ink bg-paper px-2 py-0.5 font-mono text-[9px] font-black uppercase tracking-widest shadow-retro-sm">
            {interpretation}
          </span>
        </div>

        <div className="mb-3 border-[2px] border-ink bg-paper p-2 shadow-[2px_2px_0_0_#111111]">
          <div className="mb-1.5 flex items-center justify-between gap-3 font-mono text-[9px] font-black uppercase tracking-[0.18em] text-ink/55">
            <span>{formatCount(displacementTotal)} displaced cells</span>
            <span>{underSharePct}% / {overSharePct}%</span>
          </div>
          <div className="flex h-6 overflow-hidden border-[2px] border-ink bg-paper">
            <div
              className="grid place-items-center border-r-[2px] border-ink bg-signal-cyan font-mono text-[9px] font-black text-ink"
              style={{ width: `${underSharePct}%` }}
              aria-hidden
            >
              {underSharePct >= 18 ? 'UNF' : ''}
            </div>
            <div
              className="grid place-items-center bg-signal-amber font-mono text-[9px] font-black text-ink"
              style={{ width: `${overSharePct}%` }}
              aria-hidden
            >
              {overSharePct >= 18 ? 'OVF' : ''}
            </div>
          </div>
        </div>

        <div className="grid flex-1 content-start gap-3">
          <ForecastBar
            label="UNDERFORECAST"
            value={underforecastCells}
            total={displacementTotal}
            tone="cyan"
            caption="SPC risk exceeds AutoOutlook"
            description="Underforecast means the official SPC category is higher than AutoOutlook on those grid cells."
          />
          <ForecastBar
            label="OVERFORECAST"
            value={overforecastCells}
            total={displacementTotal}
            tone="amber"
            caption="AutoOutlook risk exceeds SPC"
            description="Overforecast means AutoOutlook assigned a higher risk category than the official SPC outlook on those grid cells."
          />
        </div>
      </div>

      <div
        data-spc-qc-card="ledger"
        className="flex h-full min-h-[410px] min-w-0 flex-col border-[3px] border-ink bg-paper p-4 shadow-retro"
      >
        <div className="mb-3 flex min-h-[34px] items-center justify-between gap-3 border-b-[2px] border-ink pb-2">
          <span className="font-mono text-[10px] font-black uppercase tracking-[0.22em] text-ink/60">
            Category ledger
          </span>
          <span className="font-mono text-[9px] font-black uppercase tracking-widest text-ink/50">
            AUTO / SPC
          </span>
        </div>
        <CategoryLedger
          predicted={verification.predictedCategories}
          official={verification.officialCategories}
        />
      </div>
    </div>
  );
}

function VerificationFooter({
  verification,
  showDetails,
  setShowDetails,
}: {
  verification: SpcVerificationSummary;
  showDetails: boolean;
  setShowDetails: (fn: (v: boolean) => boolean) => void;
}) {
  return (
    <>
      <div className="mt-4 grid grid-cols-1 gap-3 border-[3px] border-ink bg-paper p-3 shadow-retro-sm lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div>
          <p className="font-mono text-[9px] font-black uppercase tracking-[0.24em] text-ink/45">
            Verification policy
          </p>
          <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.16em] text-ink/65">
            {verification.leakageGuard || 'Official SPC outlook is used only after prediction artifacts are generated.'}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowDetails((value) => !value)}
          className="justify-self-start border-[3px] border-ink bg-signal-amber px-3 py-2 font-display text-xs font-extrabold uppercase tracking-wider text-ink shadow-retro-sm transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-retro focus:outline-none lg:justify-self-end"
        >
          {showDetails ? 'Hide diagnostic logs' : 'View diagnostic logs'}
        </button>
      </div>

      {showDetails && (
        <DiagnosticLog spcVerification={verification} />
      )}
    </>
  );
}

function MissingVerificationPanel({ spcVerification }: { spcVerification?: SpcVerificationSummary | null }) {
  const error = spcVerification?.error;
  return (
    <div className="border-[3px] border-ink bg-ink p-5 font-mono text-xs text-signal-lime shadow-retro">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-signal-lime/30 pb-2">
        <span className="font-black uppercase tracking-widest text-signal-amber">
          STATUS: {error ? 'VERIFICATION_ERROR' : 'VERIFICATION_PENDING'}
        </span>
        <span className="bg-signal-amber/20 px-2 py-0.5 text-[10px] font-black uppercase tracking-widest text-signal-amber">
          AWAITING SPC QC
        </span>
      </div>
      <p className="leading-relaxed">
        SPC Day 1 verification will appear here when the artifact metadata or /api/outlook/verification endpoint returns agreement metrics.
      </p>
      {error && (
        <p className="mt-3 border border-signal-red/50 bg-signal-red/10 p-2 leading-relaxed text-signal-red">
          {error}
        </p>
      )}
      <p className="mt-3 leading-relaxed text-signal-lime/70">
        Official SPC outlook data is held behind the post-prediction leakage guard and is used only for calibration review.
      </p>
    </div>
  );
}

function MetricTile({ label, value, description }: { label: string; value: string; description: string }) {
  const tooltipId = label.toLowerCase().replace(/\s+/g, '-');

  return (
    <div
      tabIndex={0}
      aria-label={`${label}: ${value}. ${description}`}
      data-spc-tooltip={tooltipId}
      className="spc-tooltip-host relative border-[2px] border-ink bg-paper p-2 shadow-retro-sm outline-none transition-transform hover:-translate-x-0.5 hover:-translate-y-0.5 focus-visible:-translate-x-0.5 focus-visible:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-paper"
    >
      <div className="truncate text-[8px] font-black uppercase tracking-[0.2em] text-ink/45">{label}</div>
      <div className="mt-1 truncate font-mono text-lg font-black text-ink">{value}</div>
      <HoverDescription>{description}</HoverDescription>
    </div>
  );
}

function LeakageGuardTile({ requiresCheck }: { requiresCheck: boolean }) {
  const description = requiresCheck
    ? 'SPC verification data was not confirmed as post-prediction; review the run ordering before trusting this QC panel.'
    : 'SPC Day 1 data was fetched after AutoOutlook artifacts were generated, so it is used only for verification and not as model input.';

  return (
    <div
      tabIndex={0}
      aria-label={`Leakage guard: ${requiresCheck ? 'Check required' : 'Post-prediction only'}. ${description}`}
      data-spc-tooltip="leakage-guard"
      className={`spc-tooltip-host relative border-[3px] border-paper p-3 text-ink shadow-retro-sm outline-none transition-transform hover:-translate-x-0.5 hover:-translate-y-0.5 focus-visible:-translate-x-0.5 focus-visible:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-paper ${requiresCheck ? 'bg-signal-red text-paper' : 'bg-signal-lime'}`}
    >
      <div className="font-mono text-[9px] font-black uppercase tracking-[0.24em] opacity-60">Leakage guard</div>
      <div className="mt-1 font-display text-lg font-extrabold uppercase leading-tight">
        {requiresCheck ? 'Check required' : 'Post-prediction only'}
      </div>
      <HoverDescription>{description}</HoverDescription>
    </div>
  );
}

function HoverDescription({ children }: { children: string }) {
  return (
    <div
      data-tooltip-bubble="true"
      className="spc-tooltip-bubble pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-64 max-w-[calc(100vw-2rem)] border-[2px] border-paper bg-ink p-2 font-mono text-[10px] font-black uppercase leading-relaxed tracking-[0.14em] text-paper shadow-[4px_4px_0_0_#f7f1e6] transition-all duration-150"
    >
      {children}
    </div>
  );
}

function ForecastBar({
  label,
  value,
  total,
  tone,
  caption,
  description,
}: {
  label: string;
  value: number;
  total: number;
  tone: 'cyan' | 'amber';
  caption: string;
  description: string;
}) {
  const share = total > 0 ? value / total : 0;
  const toneClass = tone === 'cyan' ? 'text-signal-cyan' : 'text-signal-amber';
  const toneBg = tone === 'cyan' ? 'bg-signal-cyan' : 'bg-signal-amber';
  const tooltipId = label.toLowerCase();

  return (
    <div
      tabIndex={0}
      aria-label={`${label}: ${formatCount(value)} cells, ${Math.round(share * 100)} percent of displaced cells. ${description}`}
      data-spc-tooltip={tooltipId}
      className="spc-tooltip-host relative overflow-visible border-[2px] border-ink bg-paper text-ink shadow-retro-sm outline-none transition-transform hover:-translate-x-0.5 hover:-translate-y-0.5 focus-visible:-translate-x-0.5 focus-visible:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-ink"
    >
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3 border-b-[2px] border-ink p-3">
        <div className="min-w-0">
          <div className={`font-mono text-[10px] font-black uppercase tracking-widest ${toneClass}`}>
            {label}
          </div>
          <div className="mt-1 truncate font-mono text-[9px] font-black uppercase tracking-[0.16em] text-ink/50">
            {caption}
          </div>
        </div>
        <div className="text-right">
          <div className="font-mono text-[13px] font-black leading-none tabular-nums">
            {formatCount(value)}
          </div>
          <div className="mt-1 font-mono text-[8px] font-black uppercase tracking-[0.2em] text-ink/45">
            cells
          </div>
        </div>
      </div>
      <div className="p-3">
        <div className="relative h-8 border-[2px] border-ink bg-paper">
          <div className={`h-full border-r-[2px] border-ink ${toneBg}`} style={{ width: `${Math.round(share * 100)}%` }} />
          <div className="absolute inset-0 flex items-center justify-between px-2 font-mono text-[10px] font-black uppercase tracking-[0.16em] text-ink">
            <span>{Math.round(share * 100)}%</span>
            <span className="text-ink/45">of displacement</span>
          </div>
        </div>
      </div>
      <HoverDescription>{description}</HoverDescription>
    </div>
  );
}

function CategoryLedger({
  predicted,
  official,
}: {
  predicted?: Record<string, number>;
  official?: Record<string, number>;
}) {
  const categories = CATEGORY_ORDER;

  if (categories.length === 0) {
    return (
      <div className="border-[2px] border-ink bg-ink/5 p-3 font-mono text-[10px] uppercase tracking-widest text-ink/55">
        Category counts unavailable
      </div>
    );
  }

  return (
    <div className="grid min-h-0 flex-1 grid-rows-[auto_repeat(7,minmax(0,1fr))] gap-1.5 font-mono text-[10px]">
      <div className="grid grid-cols-[42px_minmax(0,1fr)_minmax(0,1fr)] items-center gap-2 pb-1 text-[8px] font-black uppercase tracking-[0.2em] text-ink/45">
        <span />
        <span>Auto</span>
        <span>SPC</span>
      </div>
      {categories.map((category) => {
        const modelCount = safeCount(predicted?.[category]);
        const officialCount = safeCount(official?.[category]);
        const maxCount = Math.max(modelCount, officialCount, 1);
        return (
          <div key={category} className="grid min-h-0 min-w-0 grid-cols-[42px_minmax(0,1fr)_minmax(0,1fr)] items-stretch gap-2">
            <span className="flex h-full min-h-7 items-center justify-center border-[2px] border-ink bg-paper px-1 py-0.5 text-center font-black shadow-[2px_2px_0_0_#111111]">
              {category}
            </span>
            <MiniCountBar value={modelCount} max={maxCount} tone="amber" />
            <MiniCountBar value={officialCount} max={maxCount} tone="lime" />
          </div>
        );
      })}
    </div>
  );
}

function MiniCountBar({ value, max, tone }: { value: number; max: number; tone: 'amber' | 'lime' }) {
  const width = value > 0 ? `${Math.max(4, Math.round((value / max) * 100))}%` : '0%';
  const toneClass = tone === 'amber' ? 'bg-signal-amber' : 'bg-signal-lime';

  return (
    <div className="relative h-full min-h-7 min-w-0 overflow-hidden border-[2px] border-ink bg-paper">
      <div className={`h-full border-r-[2px] border-ink ${toneClass}`} style={{ width }} />
      <span className="absolute inset-0 flex items-center justify-end truncate pl-2 pr-5 text-[9px] font-black tabular-nums text-ink md:pr-6">
        {formatCount(value)}
      </span>
    </div>
  );
}

function InfoStrip({
  label,
  value,
  tone = 'paper',
}: {
  label: string;
  value: string;
  tone?: 'paper' | 'lime' | 'red';
}) {
  const toneClass = tone === 'lime'
    ? 'bg-signal-lime'
    : tone === 'red'
      ? 'bg-signal-red text-paper'
      : 'bg-paper';

  return (
    <div className={`border-[3px] border-ink p-3 shadow-retro-sm ${toneClass}`}>
      <div className="font-mono text-[9px] font-black uppercase tracking-[0.24em] opacity-55">{label}</div>
      <div className="mt-1 break-words font-display text-lg font-extrabold uppercase leading-tight">{value}</div>
    </div>
  );
}

function DiagnosticLog({ spcVerification }: { spcVerification: SpcVerificationSummary }) {
  const explanations = spcVerification.meteorologicalExplanations ?? [];
  return (
    <div className="mt-4 max-h-80 overflow-y-auto border-[3px] border-ink bg-ink p-4 font-mono text-xs text-signal-lime shadow-retro select-text">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 border-b border-signal-lime/20 pb-2 text-[10px] font-black uppercase tracking-widest text-signal-lime/55">
        <span>Verification logs / system output</span>
        <span>{spcVerification.spcFetchedAtISO ? `FETCHED: ${spcVerification.spcFetchedAtISO}` : ''}</span>
      </div>
      <div className="space-y-3">
        {explanations.length > 0 ? explanations.map((text, index) => (
          <p key={`${index}-${text.slice(0, 16)}`} className="leading-relaxed">
            <span className="font-black text-signal-amber">LOG_{String(index + 1).padStart(2, '0')}:</span> {text}
          </p>
        )) : (
          <p className="leading-relaxed text-signal-lime/70">No meteorological explanation records were included in this verification artifact.</p>
        )}
        {spcVerification.spcDay1Url && (
          <p className="border-t border-signal-lime/10 pt-3 text-[10px] leading-relaxed">
            <span className="font-black text-signal-lime/45">SOURCE URL:</span>{' '}
            <a
              href={spcVerification.spcDay1Url}
              target="_blank"
              rel="noopener noreferrer"
              className="break-all text-signal-cyan underline hover:text-signal-lime"
            >
              {spcVerification.spcDay1Url}
            </a>
          </p>
        )}
      </div>
    </div>
  );
}

function normalizeFraction(value: number | null | undefined): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return Math.max(0, Math.min(1, value));
}

function safeCount(value: number | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0;
}

function formatCount(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return '--';
  return Math.round(value).toLocaleString();
}

function formatUtc(iso: string | undefined): string {
  if (!iso) return '--';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')} ${String(date.getUTCHours()).padStart(2, '0')}${String(date.getUTCMinutes()).padStart(2, '0')}Z`;
}

function agreementStatus(fraction: number) {
  if (fraction >= 0.6) return STATUS_STYLES.aligned;
  if (fraction >= 0.35) return STATUS_STYLES.caution;
  return STATUS_STYLES.drift;
}

function displacementInterpretation(under: number, over: number, agreementFraction: number): 'ALIGNED' | 'CONSERVATIVE' | 'AGGRESSIVE' | 'MIXED' {
  const total = under + over;
  if (agreementFraction >= 0.6 && total > 0 && Math.abs(under - over) / total < 0.2) return 'ALIGNED';
  if (under > over * 1.25) return 'CONSERVATIVE';
  if (over > under * 1.25) return 'AGGRESSIVE';
  return 'MIXED';
}

function interpretationLabel(interpretation: string, agreementPct: number): string {
  if (interpretation === 'CONSERVATIVE') return `${agreementPct}% match, underforecast dominant`;
  if (interpretation === 'AGGRESSIVE') return `${agreementPct}% match, overforecast dominant`;
  if (interpretation === 'ALIGNED') return `${agreementPct}% match, balanced displacement`;
  return `${agreementPct}% match, mixed displacement`;
}
