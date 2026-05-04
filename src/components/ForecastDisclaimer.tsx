type ForecastDisclaimerProps = {
  variant?: 'sidebar' | 'export';
};

const baseText =
  'Forecasts on this website are produced by automated algorithms and are experimental. Always rely on data from the national hydrometeorological institutes. Convective outlooks for the U.S. prepared by experts are available at';
const exportText =
  'Automated experimental forecast. Use official hydrometeorological data. U.S. expert outlooks:';

export const SPC_OUTLOOK_URL = 'https://www.spc.noaa.gov/';

export default function ForecastDisclaimer({ variant = 'sidebar' }: ForecastDisclaimerProps) {
  const isExport = variant === 'export';

  if (!isExport) {
    return (
      <div className="font-mono text-[9px] text-ink">
        <div className="grid gap-1.5">
          <div className="border-l-[3px] border-ink pl-2">
            <div className="text-[8px] font-bold uppercase tracking-[0.22em] text-ink/50">
              Status
            </div>
            <p className="mt-0.5 leading-snug tracking-[0.04em] text-ink/80">
              Automated experimental guidance.
            </p>
          </div>
          <div className="border-l-[3px] border-signal-amber pl-2">
            <div className="text-[8px] font-bold uppercase tracking-[0.22em] text-ink/50">
              Verify
            </div>
            <p className="mt-0.5 leading-snug tracking-[0.04em] text-ink/80">
              Use official hydrometeorological sources for decisions.
            </p>
          </div>
        </div>
        <a
          href={SPC_OUTLOOK_URL}
          target="_blank"
          rel="noreferrer"
          className="mt-2 flex items-center justify-between border-[2px] border-ink bg-white px-2 py-1.5 font-bold uppercase tracking-[0.12em] text-ink shadow-[2px_2px_0_0_#111111] transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:bg-signal-amber hover:shadow-retro-sm"
        >
          <span>SPC Outlooks</span>
          <span aria-hidden>↗</span>
        </a>
      </div>
    );
  }

  return (
    <p
      className={[
        'font-mono leading-relaxed',
        isExport
          ? 'text-[9px] tracking-[0.14em] text-paper/85'
          : 'text-[9px] tracking-[0.08em] text-ink/70',
      ].join(' ')}
    >
      {isExport ? exportText : baseText}{' '}
      <a
        href={SPC_OUTLOOK_URL}
        target="_blank"
        rel="noreferrer"
        className={[
          'font-bold underline decoration-2 underline-offset-4',
          isExport ? 'text-signal-amber' : 'text-ink hover:text-signal-amber',
        ].join(' ')}
      >
        www.spc.noaa.gov
      </a>
    </p>
  );
}
