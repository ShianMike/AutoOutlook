import type { HourSnapshot } from '../types/forecast';
import { HAZARD_META, RISK_META } from '../types/forecast';
import type { ArtifactStatus } from '../hooks/useOutlookArtifacts';
import type { OutlookArtifacts } from '../types/outlookArtifacts';
import { focusLocationFromSnapshot } from '../utils/focusLocation';
import { buildGeneratedOutlookSummary } from '../utils/generatedHeadline';
import RetroBadge from './retro/RetroBadge';

interface PrimaryOutlookBannerProps {
  snapshot: HourSnapshot | null;
  artifacts?: OutlookArtifacts | null;
  artifactStatus?: ArtifactStatus;
}

export default function PrimaryOutlookBanner({ snapshot, artifacts, artifactStatus }: PrimaryOutlookBannerProps) {
  if (!snapshot) {
    return (
      <section className="border-[4px] border-ink shadow-retro-lg bg-paper p-6 min-h-[180px] flex items-center justify-center font-mono text-ink/60">
        Awaiting forecast…
      </section>
    );
  }
  const { outlook } = snapshot;
  const outlookSummary = buildGeneratedOutlookSummary({ snapshot, artifacts, artifactStatus });
  const usingGeneratedArtifacts = outlookSummary.usingGeneratedArtifacts;
  const displayCategory = outlookSummary.category;
  const meta = RISK_META[displayCategory];
  const hazard = HAZARD_META[outlookSummary.hazard ?? outlook.mainHazard];
  const confPct = Math.round(outlook.confidence * 100);
  const isDarkChip = meta.tw.includes('text-paper');
  const headline = outlookSummary.headline;
  const regionLabel = focusLocationFromSnapshot(snapshot).label;

  return (
    <section
      className={[
        'border-[4px] border-ink shadow-retro-lg relative retro-scanline',
        meta.tw,
      ].join(' ')}
    >
      {/* Top strip */}
      <div className={`flex items-center justify-between gap-3 border-b-[3px] border-ink px-4 py-1.5 ${isDarkChip ? 'bg-ink text-paper' : 'bg-paper text-ink'}`}>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] opacity-70">
            Primary outlook
          </span>
          <span className="font-mono text-[11px] uppercase tracking-widest">
            {regionLabel}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {!usingGeneratedArtifacts && outlook.significantSevere && (
            <RetroBadge tone="red">SIG SEVERE</RetroBadge>
          )}
          <RetroBadge tone={isDarkChip ? 'paper' : 'ink'}>
            {usingGeneratedArtifacts ? 'ARTIFACT' : `CONF ${confPct}%`}
          </RetroBadge>
        </div>
      </div>

      <div className="px-4 py-2.5 sm:px-5 sm:py-3 grid grid-cols-1 lg:grid-cols-[auto,1fr,auto] gap-4 items-center">
        {/* Big risk chip */}
        <div className="flex flex-col items-start gap-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] opacity-70">
            Risk Category
          </span>
          <div
            className={[
              'border-[4px] border-ink shadow-retro px-4 py-2 font-display font-extrabold uppercase tracking-tight bg-paper text-ink',
            ].join(' ')}
          >
            <div className="text-[11px] tracking-[0.3em] opacity-60">{meta.chipText}</div>
            <div className="text-xl sm:text-2xl leading-none">{meta.label}</div>
          </div>
        </div>

        {/* Headline */}
        <div className="flex flex-col gap-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] opacity-70">
            Forecast headline
          </span>
          <p className="font-display text-lg sm:text-xl font-extrabold leading-tight">
            {headline}
          </p>
        </div>

        {/* Main hazard glyph */}
        <div className="flex flex-col items-center gap-1 sm:gap-2 min-w-[116px]">
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] opacity-70">
            Main Hazard
          </span>
          <div className="border-[3px] border-ink shadow-retro bg-paper text-ink h-[82px] w-[88px] px-2 py-2 flex flex-col items-center justify-center">
            <span className="text-[22px] leading-none">{hazard.glyph}</span>
            <span className="font-display font-extrabold text-[10px] uppercase mt-1.5 leading-[0.9rem] text-center max-w-[68px]">
              {hazard.label}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
