import type { HourSnapshot } from '../types/forecast';
import { generateDiscussion } from '../utils/discussionGenerator';
import RetroPanel from './retro/RetroPanel';
import RetroBadge from './retro/RetroBadge';

interface ForecastDiscussionProps {
  snapshot: HourSnapshot | null;
}

function fmtIssued(iso: string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${String(d.getUTCHours()).padStart(2, '0')}${String(d.getUTCMinutes()).padStart(2, '0')}Z ${d.getUTCDate()}/${d.getUTCMonth() + 1}`;
}

export default function ForecastDiscussion({ snapshot }: ForecastDiscussionProps) {
  const text = snapshot ? generateDiscussion(snapshot) : 'Awaiting forecast bundle…';
  const mlDriven = Boolean(snapshot?.mlHazards);
  return (
    <RetroPanel
      title="Automated Forecast Discussion"
      eyebrow={mlDriven ? '07 / Generated from ML hazard probabilities' : '07 / Generated from rule engines'}
      badge={<RetroBadge tone="paper">{mlDriven ? 'XGBOOST' : 'RULE-BASED'}</RetroBadge>}
    >
      <article className="border-[3px] border-ink bg-paper relative retro-scanline">
        <div className="border-b-[2px] border-ink bg-paper px-3 py-1.5 flex items-center justify-between font-mono text-[10px] uppercase tracking-widest text-ink/70">
          <span>AUTO-DISC.{snapshot ? ` ${fmtIssued(snapshot.validTimeISO)}` : ''}</span>
          <span>AOOOC / AUTOOUTLOOK</span>
        </div>
        <div className="p-4 space-y-4">
          {text.split('\n\n').map((section, i) => {
            const headerMatch = section.match(/^\.\.\.([A-Z /]+)\.\.\.\n?/);
            if (headerMatch) {
              const heading = headerMatch[1];
              const body = section.slice(headerMatch[0].length);
              return (
                <div key={i}>
                  <h3 className="font-mono text-[10px] font-bold uppercase tracking-[0.3em] text-ink/50 mb-1.5">
                    {heading}
                  </h3>
                  <p className="font-mono text-[13px] leading-[1.65] text-ink whitespace-pre-line">
                    {body}
                  </p>
                </div>
              );
            }
            return (
              <p key={i} className="font-mono text-[13px] leading-[1.65] text-ink whitespace-pre-line">
                {section}
              </p>
            );
          })}
        </div>
        <div className="border-t-[2px] border-ink bg-paper px-3 py-1.5 flex items-center justify-between font-mono text-[10px] uppercase tracking-widest text-ink/50">
          <span>$$ END DISCUSSION $$</span>
          <span>v1 · auto-generated</span>
        </div>
      </article>
    </RetroPanel>
  );
}
