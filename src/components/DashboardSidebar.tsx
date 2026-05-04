import { useEffect, useState } from 'react';
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
  const [activeId, setActiveId] = useState(NAV_ITEMS[0].id);

  useEffect(() => {
    const sectionIds = NAV_ITEMS.map((item) => item.id);
    const sections = sectionIds
      .map((id) => document.getElementById(id))
      .filter((section): section is HTMLElement => section !== null);

    if (sections.length === 0) return;

    const updateActiveSection = () => {
      const viewportAnchor = window.innerHeight * 0.28;
      let currentId = sections[0].id;

      for (const section of sections) {
        const rect = section.getBoundingClientRect();
        if (rect.top <= viewportAnchor) currentId = section.id;
      }

      setActiveId(currentId);
    };

    updateActiveSection();
    window.addEventListener('scroll', updateActiveSection, { passive: true });
    window.addEventListener('resize', updateActiveSection);

    return () => {
      window.removeEventListener('scroll', updateActiveSection);
      window.removeEventListener('resize', updateActiveSection);
    };
  }, []);

  return (
    <aside className="bg-paper text-ink border-b-[4px] border-ink lg:sticky lg:top-0 lg:h-screen lg:w-[300px] lg:shrink-0 lg:border-b-0 lg:border-r-[5px] retro-scanline">
      <div className="flex h-full flex-col">
        <div className="border-b-[3px] border-ink p-5">
          <div className="mb-4 inline-flex border-[3px] border-ink bg-ink px-2 py-1 font-mono text-[10px] font-bold tracking-[0.35em] text-paper shadow-retro-sm">
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
                  onClick={() => setActiveId(item.id)}
                  className={`group flex items-center gap-3 border-[3px] border-ink px-3 py-3 font-display text-sm font-extrabold uppercase tracking-wider transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:bg-signal-amber hover:shadow-retro ${isActive ? 'translate-x-[3px] translate-y-[3px] bg-signal-amber shadow-[1px_1px_0_0_#111111]' : 'bg-paper shadow-retro-sm'}`}
                >
                <span className={`grid h-8 w-8 shrink-0 place-items-center border-[2px] border-ink font-mono text-[10px] ${isActive ? 'bg-ink text-paper shadow-retro-sm' : 'bg-ink text-paper group-hover:bg-paper group-hover:text-ink'}`}>
                  {item.code}
                </span>
                <span>{item.label}</span>
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
