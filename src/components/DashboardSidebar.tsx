import { useEffect, useRef, useState } from 'react';
import ForecastDisclaimer from './ForecastDisclaimer';

interface DashboardSidebarProps {
  onRefresh: () => void;
}

const NAV_ITEMS = [
  { id: 'time-scrubber', href: '#time-scrubber', label: 'Time Scrubber', code: '01' },
  { id: 'outlook-map', href: '#outlook-map', label: 'Outlook Map', code: '02' },
  { id: 'primary-outlook', href: '#primary-outlook', label: 'Primary Outlook', code: '03' },
  { id: 'hazards', href: '#hazards', label: 'Hazards', code: '04' },
  { id: 'ingredients', href: '#ingredients', label: 'Parameters', code: '05' },
  { id: 'timeline', href: '#timeline', label: 'Risk Timeline', code: '06' },
  { id: 'discussion', href: '#discussion', label: 'Discussion', code: '07' },
  { id: 'readiness', href: '#readiness', label: 'Watch Readiness', code: '08' },
  { id: 'system-status', href: '#system-status', label: 'System Status', code: '09' },
];

export default function DashboardSidebar({ onRefresh }: DashboardSidebarProps) {
  const [activeId, setActiveId] = useState(() => activeIdFromHash() ?? NAV_ITEMS[0].id);
  const hashLockUntilRef = useRef(0);

  useEffect(() => {
    const sectionIds = NAV_ITEMS.map((item) => item.id);
    const sections = sectionIds
      .map((id) => document.getElementById(id))
      .filter((section): section is HTMLElement => section !== null);

    if (sections.length === 0) return;

    const updateActiveSection = () => {
      if (Date.now() < hashLockUntilRef.current) return;
      const viewportAnchor = Math.min(window.innerHeight * 0.34, 260);
      const hashId = activeIdFromHash();
      const hashSection = hashId ? sections.find((section) => section.id === hashId) : undefined;
      if (hashSection) {
        const rect = hashSection.getBoundingClientRect();
        if (rect.top <= viewportAnchor && rect.bottom > 0) {
          setActiveId(hashSection.id);
          return;
        }
      }
      const containing = sections.find((section) => {
        const rect = section.getBoundingClientRect();
        return rect.top <= viewportAnchor && rect.bottom > viewportAnchor;
      });
      if (containing) {
        setActiveId(containing.id);
        return;
      }

      let currentId = sections[0].id;
      let bestDistance = Number.POSITIVE_INFINITY;
      for (const section of sections) {
        const rect = section.getBoundingClientRect();
        const distance = Math.abs(rect.top - viewportAnchor);
        if (distance < bestDistance) {
          bestDistance = distance;
          currentId = section.id;
        }
      }

      setActiveId(currentId);
    };

    const updateFromHash = () => {
      const hashId = activeIdFromHash();
      if (!hashId) return;
      hashLockUntilRef.current = Date.now() + 900;
      setActiveId(hashId);
      window.setTimeout(updateActiveSection, 950);
    };

    updateFromHash();
    updateActiveSection();
    window.addEventListener('scroll', updateActiveSection, { passive: true });
    window.addEventListener('resize', updateActiveSection);
    window.addEventListener('hashchange', updateFromHash);

    return () => {
      window.removeEventListener('scroll', updateActiveSection);
      window.removeEventListener('resize', updateActiveSection);
      window.removeEventListener('hashchange', updateFromHash);
    };
  }, []);

  return (
    <aside className="bg-paper text-ink border-b-[4px] border-ink lg:sticky lg:top-0 lg:h-screen lg:w-[300px] lg:shrink-0 lg:border-b-0 lg:border-r-[5px] retro-scanline">
      <div className="flex h-full flex-col">
        <div className="border-b-[3px] border-ink p-5">
          <div className="mb-4 inline-flex border-[3px] border-ink bg-paper px-2 py-1 font-mono text-[10px] font-bold tracking-[0.35em] text-ink shadow-retro-sm">
            AO/01
          </div>
          <h1 className="font-display text-4xl font-extrabold uppercase leading-[0.88] tracking-tight">
            Auto<br />
            <span className="text-signal-amber">Outlook</span>
          </h1>
          <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.24em] text-ink/60">
            Dashboard Navigation
          </p>
        </div>

        <nav className="flex-1 overflow-y-auto p-3" aria-label="Dashboard sections">
          <div className="flex flex-col gap-2">
            {NAV_ITEMS.map((item) => {
              const isActive = activeId === item.id;
              return (
                <a
                  key={item.href}
                  href={item.href}
                  aria-current={isActive ? 'location' : undefined}
                  onClick={() => {
                    hashLockUntilRef.current = Date.now() + 900;
                    setActiveId(item.id);
                  }}
                  className={`group relative flex items-center gap-3 border-[3px] border-ink px-3 py-3 font-display text-sm font-extrabold uppercase tracking-wider transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-retro ${isActive ? 'bg-signal-amber translate-x-[3px] translate-y-[3px] shadow-[1px_1px_0_0_#111111]' : 'bg-paper shadow-retro-sm'}`}
                >
                <span className={`grid h-8 w-8 shrink-0 place-items-center border-[2px] border-ink bg-paper font-mono text-[10px] text-ink group-hover:bg-paper ${isActive ? 'shadow-none translate-x-[1px] translate-y-[1px]' : 'shadow-retro-sm'}`}>
                  {item.code}
                </span>
                <span className="min-w-0 flex-1 truncate">{item.label}</span>
                <span
                  className={`h-5 w-1.5 shrink-0 border-[1.5px] border-ink transition-opacity ${isActive ? 'bg-ink opacity-100' : 'bg-signal-amber opacity-0 group-hover:opacity-100'}`}
                  aria-hidden
                />
              </a>
              );
            })}
          </div>
        </nav>

        <div className="border-t-[3px] border-ink p-3">
          <div
            className="mb-3 border-[3px] border-ink bg-paper p-2.5 shadow-retro-sm"
            aria-label="Experimental forecast disclaimer"
          >
            <div className="mb-2 flex items-center justify-between border-b-[2px] border-ink pb-1.5">
              <div className="bg-ink px-1.5 py-0.5 font-mono text-[8px] font-bold uppercase tracking-[0.24em] text-paper">
                Experimental
              </div>
              <div className="font-mono text-[8px] font-bold uppercase tracking-[0.2em] text-ink/45">
                Auto
              </div>
            </div>
            <ForecastDisclaimer />
          </div>
          <button type="button" onClick={onRefresh} className="retro-button w-full">
            Refresh Data
          </button>
        </div>
      </div>
    </aside>
  );
}

function activeIdFromHash(): string | null {
  if (typeof window === 'undefined') return null;
  const id = window.location.hash.replace(/^#/, '');
  return NAV_ITEMS.some((item) => item.id === id) ? id : null;
}
