import { useRef, useState } from 'react';
import { toPng } from 'html-to-image';
import type { HourSnapshot } from '../types/forecast';
import { generateDiscussion } from '../utils/discussionGenerator';
import { focusLocationFromSnapshot } from '../utils/focusLocation';
import FocusLocationBadge from './FocusLocationBadge';
import RetroPanel from './retro/RetroPanel';

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
  const focus = focusLocationFromSnapshot(snapshot);
  const discussionRef = useRef<HTMLElement | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const saveDiscussionPng = async () => {
    if (!discussionRef.current || !snapshot || isExporting) return;
    setIsExporting(true);
    setExportError(null);
    try {
      const dataUrl = await toPng(discussionRef.current, {
        backgroundColor: '#f5f0e6',
        cacheBust: true,
        skipFonts: true,
        pixelRatio: 2,
      });
      const link = document.createElement('a');
      link.href = dataUrl;
      link.download = `autooutlook-discussion-f${String(snapshot.forecastHour).padStart(3, '0')}.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'unknown export error';
      setExportError(`Discussion export failed: ${message}`);
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <RetroPanel
      title="Automated Forecast Discussion"
      eyebrow={mlDriven ? '06 / Generated from ML hazard probabilities' : '06 / Generated from rule engines'}
      badge={(
        <div className="flex flex-wrap items-center justify-end gap-2">
          <FocusLocationBadge focus={focus} />
          <button
            type="button"
            onClick={saveDiscussionPng}
            disabled={!snapshot || isExporting}
            className="border-[2px] border-paper bg-signal-cyan px-2 py-1 font-display text-[11px] font-extrabold uppercase tracking-wider text-ink shadow-[2px_2px_0_0_#f5f0e6] transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isExporting ? 'Saving...' : 'Export PNG'}
          </button>
        </div>
      )}
    >
      <article ref={discussionRef} className="border-[3px] border-ink bg-paper relative retro-scanline">
        <div className="border-b-[2px] border-ink bg-paper px-3 py-1.5 flex items-center justify-between font-mono text-[10px] uppercase tracking-widest text-ink/70">
          <span>AUTO-DISC.{snapshot ? ` ${fmtIssued(snapshot.validTimeISO)}` : ''}</span>
          <span>AOOOC / AUTOOUTLOOK</span>
        </div>
        <div className="border-b-[2px] border-ink bg-signal-amber/20 px-3 py-2 font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-ink">
          Automated experimental discussion. Not an official forecast discussion. Use official meteorological sources for decisions.
        </div>
        <div className="bg-gradient-to-b from-paper via-white to-paper p-4 space-y-4">
          {text.split('\n\n').map((section, i) => {
            const headerMatch = section.match(/^\.\.\.([A-Z /]+)\.\.\.\n?/);
            if (headerMatch) {
              const heading = headerMatch[1];
              const body = section.slice(headerMatch[0].length);
              return (
                <div key={i}>
                  <h3 className={`font-mono text-[10px] font-bold uppercase tracking-[0.3em] mb-1.5 ${headingTone(heading)}`}>
                    {heading}
                  </h3>
                  <p className="font-mono text-[13px] leading-[1.65] text-ink whitespace-pre-line">
                    {highlightDiscussionText(body)}
                  </p>
                </div>
              );
            }
            return (
              <p key={i} className="font-mono text-[13px] leading-[1.65] text-ink whitespace-pre-line">
                {highlightDiscussionText(section)}
              </p>
            );
          })}
        </div>
        <div className="border-t-[2px] border-ink bg-paper px-3 py-1.5 flex items-center justify-between font-mono text-[10px] uppercase tracking-widest text-ink/50">
          <span>$$ END DISCUSSION $$</span>
          <span>v1 · auto-generated</span>
        </div>
      </article>
      {exportError && (
        <div className="mt-2 border-[2px] border-signal-red bg-paper px-3 py-2 font-mono text-[11px] font-bold text-signal-red">
          {exportError}
        </div>
      )}
    </RetroPanel>
  );
}

function headingTone(heading: string): string {
  if (heading.includes('HAZARD')) return 'text-signal-red';
  if (heading.includes('UNCERTAINTY')) return 'text-signal-orange';
  if (heading.includes('MESOSCALE')) return 'text-signal-amber';
  if (heading.includes('DISCUSSION')) return 'text-signal-cyan';
  return 'text-ink/55';
}

function highlightDiscussionText(text: string) {
  const pattern = /(HIGH|MOD|ENH|SLGT|MRGL|TSTM|SIGNIFICANT|TORNADO|HAIL|WIND|FLOOD|UPSIDE SCENARIO|DOWNSIDE SCENARIO|KEY UNCERTAINTY DRIVERS)(?=:|\b)/g;
  return text.split(pattern).map((part, index) => {
    if (!part) return null;
    const tone = highlightTone(part);
    if (!tone) return part;
    return (
      <span key={`${part}-${index}`} className={`font-bold ${tone}`}>
        {part}
      </span>
    );
  });
}

function highlightTone(value: string): string {
  const exact = value.trim();
  if (['HIGH', 'MOD', 'ENH'].includes(exact)) return 'text-signal-red';
  if (['SLGT', 'MRGL'].includes(exact)) return 'text-signal-amber';
  if (exact === 'TSTM') return 'text-signal-cyan';
  if (['TORNADO', 'HAIL', 'WIND', 'FLOOD', 'SIGNIFICANT'].includes(exact)) return 'text-signal-red';
  if (exact === 'UPSIDE SCENARIO') return 'text-signal-lime';
  if (['DOWNSIDE SCENARIO', 'KEY UNCERTAINTY DRIVERS'].includes(exact)) return 'text-signal-orange';
  return '';
}
