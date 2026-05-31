import { useEffect, useRef, useState } from 'react';
import ForecastDisclaimer from './ForecastDisclaimer';
import { viewLinkHandler } from '../utils/navigateView';

const NAV_ITEMS = [
  { id: 'outlook-map', href: '#outlook-map', label: 'Outlook Map', code: '01' },
  { id: 'primary-outlook', href: '#primary-outlook', label: 'Primary Outlook', code: '02' },
  { id: 'hazards', href: '#hazards', label: 'Hazards', code: '03' },
  { id: 'ingredients', href: '#ingredients', label: 'Parameters', code: '04' },
  { id: 'timeline', href: '#timeline', label: 'Risk Timeline', code: '05' },
  { id: 'discussion', href: '#discussion', label: 'Discussion', code: '06' },
  { id: 'verification', href: '#verification', label: 'SPC Verification', code: '07' },
  { id: 'system-status', href: '#system-status', label: 'System Status', code: '08' },
];

export default function DashboardSidebar() {
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
        <div className="border-b-[3px] border-ink p-5 lg:p-3 xl:p-5 lg:max-[900px]:p-3">
          <a
            href="#"
            onClick={viewLinkHandler('')}
            aria-label="AutoOutlook home"
            title="Back to home"
            className="group block focus:outline-none"
          >
            <div className="mb-4 inline-flex border-[3px] border-ink bg-paper px-2 py-1 font-mono text-[10px] font-bold tracking-[0.35em] text-ink shadow-retro-sm transition-transform group-hover:-translate-x-0.5 group-hover:-translate-y-0.5 lg:max-[900px]:mb-2 lg:max-[900px]:px-1.5 lg:max-[900px]:py-0.5 lg:max-[900px]:text-[8px]">
              AO/01
            </div>
            <h1 className="font-display text-4xl font-extrabold uppercase leading-[0.88] tracking-tight lg:max-[900px]:text-3xl lg:max-[760px]:text-2xl">
              Auto<br />
              <span className="text-signal-amber">Outlook</span>
            </h1>
            <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.24em] text-ink/60 lg:max-[900px]:mt-2 lg:max-[900px]:text-[8px] lg:max-[760px]:hidden">
              <span className="group-hover:text-ink">▸ Home</span> · Dashboard Navigation
            </p>
          </a>
        </div>

        <nav className="flex-1 overflow-hidden p-3 lg:max-[900px]:p-2 lg:max-[760px]:p-1.5" aria-label="Dashboard sections">
          <div className="flex h-full flex-col justify-center gap-2 lg:max-[900px]:gap-1.5 lg:max-[760px]:gap-1">
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
                  className={`group relative flex min-h-0 items-center gap-3 border-[3px] border-ink px-3 py-3 font-display text-sm font-extrabold uppercase tracking-wider transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-retro lg:max-[900px]:gap-2 lg:max-[900px]:px-2 lg:max-[900px]:py-2 lg:max-[900px]:text-[12px] lg:max-[760px]:border-[2px] lg:max-[760px]:px-2 lg:max-[760px]:py-1 lg:max-[760px]:text-[10px] ${isActive ? 'bg-signal-amber translate-x-[3px] translate-y-[3px] shadow-[1px_1px_0_0_#111111]' : 'bg-paper shadow-retro-sm'}`}
                >
                <span className={`grid h-8 w-8 shrink-0 place-items-center border-[2px] border-ink bg-paper font-mono text-[10px] text-ink group-hover:bg-paper lg:max-[900px]:h-7 lg:max-[900px]:w-7 lg:max-[900px]:text-[9px] lg:max-[760px]:h-5 lg:max-[760px]:w-5 lg:max-[760px]:text-[8px] ${isActive ? 'shadow-none translate-x-[1px] translate-y-[1px]' : 'shadow-retro-sm'}`}>
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

        <div className="border-t-[3px] border-ink p-3 lg:max-[900px]:p-2 lg:max-[760px]:p-1.5">
          <div
            className="mb-3 border-[3px] border-ink bg-paper p-2.5 shadow-retro-sm lg:max-[900px]:mb-2 lg:max-[900px]:p-2 lg:max-[760px]:hidden"
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
          <div className="grid grid-cols-2 gap-2 lg:max-[900px]:gap-1.5 lg:max-[760px]:gap-1">
            <a
              href="#docs-overview"
              className="group flex items-center gap-2 border-[3px] border-ink bg-signal-cyan px-2.5 py-2.5 font-display text-sm font-extrabold uppercase tracking-wider text-ink shadow-retro-sm transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-retro lg:max-[900px]:gap-1.5 lg:max-[900px]:px-2 lg:max-[900px]:py-2 lg:max-[900px]:text-[12px] lg:max-[760px]:border-[2px] lg:max-[760px]:py-1 lg:max-[760px]:text-[10px]"
              aria-label="Open documentation"
            >
              <span className="grid h-7 w-7 shrink-0 place-items-center border-[2px] border-ink bg-paper font-mono text-[10px] text-ink shadow-[1px_1px_0_0_#111111] lg:max-[900px]:h-6 lg:max-[900px]:w-6 lg:max-[900px]:text-[9px] lg:max-[760px]:h-5 lg:max-[760px]:w-5 lg:max-[760px]:text-[8px]">
                DOC
              </span>
              <span className="min-w-0 flex-1 truncate">Docs</span>
            </a>
            <a
              href="#changelog"
              onClick={viewLinkHandler('#changelog')}
              className="group flex items-center gap-2 border-[3px] border-ink bg-signal-amber px-2.5 py-2.5 font-display text-sm font-extrabold uppercase tracking-wider text-ink shadow-retro-sm transition-all hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-retro lg:max-[900px]:gap-1.5 lg:max-[900px]:px-2 lg:max-[900px]:py-2 lg:max-[900px]:text-[12px] lg:max-[760px]:border-[2px] lg:max-[760px]:py-1 lg:max-[760px]:text-[10px]"
              aria-label="Open changelog"
            >
              <span className="grid h-7 w-7 shrink-0 place-items-center border-[2px] border-ink bg-paper font-mono text-[10px] text-ink shadow-[1px_1px_0_0_#111111] lg:max-[900px]:h-6 lg:max-[900px]:w-6 lg:max-[900px]:text-[9px] lg:max-[760px]:h-5 lg:max-[760px]:w-5 lg:max-[760px]:text-[8px]">
                LOG
              </span>
              <span className="min-w-0 flex-1 truncate">Changelog</span>
            </a>
          </div>
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
