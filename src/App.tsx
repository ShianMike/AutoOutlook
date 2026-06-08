import { useEffect, useState } from 'react';

import { useAutoForecast } from './hooks/useAutoForecast';
import { useForecastHour } from './hooks/useForecastHour';
import { useOutlookArtifacts, useMergedD1Verification, useSpcStormReports } from './hooks/useOutlookArtifacts';
import { FORECAST_HOUR_LABELS, type ActiveRegion } from './types/forecast';

import DashboardSidebar from './components/DashboardSidebar';
import CommandHeader from './components/CommandHeader';
import ForecastTimeSlider from './components/ForecastTimeSlider';
import PrimaryOutlookBanner from './components/PrimaryOutlookBanner';
import OutlookMapPanel from './components/OutlookMapPanel';
import HazardProbabilityBoard from './components/HazardProbabilityBoard';
import EnvironmentalIngredientsGrid from './components/EnvironmentalIngredientsGrid';
import ForecastDiscussion from './components/ForecastDiscussion';
import RiskTimeline from './components/RiskTimeline';
import WatchReadinessPanel from './components/WatchReadinessPanel';
import SystemStatusPanel from './components/SystemStatusPanel';
import VerificationPanel from './components/VerificationPanel';
import DocsSidebar from './components/docs/DocsSidebar';
import DocumentationPage from './components/docs/DocumentationPage';
import LandingPage from './components/landing/LandingPage';
import ChangelogPage from './components/changelog/ChangelogPage';
import ViewTransitionOverlay from './components/ViewTransitionOverlay';

type AppView = 'landing' | 'dashboard' | 'docs' | 'changelog';

const DASHBOARD_ANCHORS = new Set([
  'dashboard',
  'time-scrubber',
  'outlook-map',
  'primary-outlook',
  'hazards',
  'ingredients',
  'timeline',
  'discussion',
  'readiness',
  'verification',
  'system-status',
]);

function viewFromHash(): AppView {
  if (typeof window === 'undefined') return 'landing';
  const id = window.location.hash.replace(/^#/, '');
  if (id === 'docs' || id.startsWith('docs-')) return 'docs';
  if (id === 'changelog' || id.startsWith('release-')) return 'changelog';
  if (DASHBOARD_ANCHORS.has(id)) return 'dashboard';
  return 'landing';
}

export default function App() {
  const activeRegion: ActiveRegion = 'conus';

  const [selectedMergedDate, setSelectedMergedDate] = useState<string>('');
  const [viewType, setViewType] = useState<'hourly' | 'merged'>('hourly');
  const [stormReportsMode, setStormReportsMode] = useState<'none' | 'all' | 'tornado' | 'hail' | 'wind'>('none');
  const [view, setView] = useState<AppView>(() => viewFromHash());

  const dashboardDataEnabled = view === 'dashboard';
  const auto = useAutoForecast(activeRegion, dashboardDataEnabled);
  const hour = useForecastHour(auto.bundle);
  const snapshot = hour.snapshot;
  const outlookArtifacts = useOutlookArtifacts(
    snapshot?.forecastHour,
    snapshot?.validTimeISO,
    activeRegion,
    15 * 1000,
    dashboardDataEnabled,
  );
  const mergedD1Verification = useMergedD1Verification(activeRegion, selectedMergedDate, dashboardDataEnabled);
  const stormReports = useSpcStormReports(activeRegion, selectedMergedDate, dashboardDataEnabled);
  const mlDriven = Boolean(auto.bundle?.mlModel?.active && auto.bundle.mlHazardHours);
  const hourLabel = snapshot
    ? FORECAST_HOUR_LABELS[snapshot.forecastHour] ?? `+${snapshot.forecastHour}h`
    : undefined;

  useEffect(() => {
    const sync = () => setView(viewFromHash());
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);

  // After a view change, scroll to the hash target (or the top of the page).
  // Same-view hash changes are handled natively by the browser.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const hash = window.location.hash.replace(/^#/, '');
    const raf = window.requestAnimationFrame(() => {
      if (!hash) {
        window.scrollTo({ top: 0 });
        return;
      }
      const el = document.getElementById(hash);
      if (el) {
        el.scrollIntoView({ block: 'start' });
      } else {
        window.scrollTo({ top: 0 });
      }
    });
    return () => window.cancelAnimationFrame(raf);
  }, [view]);

  if (view === 'landing') {
    return (
      <>
        <LandingPage />
        <ViewTransitionOverlay key={`tx-${view}`} view={view} cycle={0} />
      </>
    );
  }

  if (view === 'changelog') {
    return (
      <>
        <ChangelogPage />
        <ViewTransitionOverlay key={`tx-${view}`} view={view} cycle={0} />
      </>
    );
  }

  return (
    <>
    <div className="min-h-screen bg-paper text-ink lg:flex">
      {view === 'docs' ? <DocsSidebar /> : <DashboardSidebar />}

      <div className="flex min-w-0 flex-1 flex-col">
        {view === 'docs' ? (
          <DocumentationPage />
        ) : (
          <>
        <CommandHeader
          bundle={auto.bundle}
          selectedValidTime={snapshot?.validTimeISO}
          selectedHourLabel={hourLabel}
          artifacts={outlookArtifacts.artifacts}
          artifactStatus={outlookArtifacts.status}
        />

        <main className="w-full min-w-0 flex-1 px-3 py-2 sm:px-4 xl:px-5 flex flex-col gap-3 xl:gap-4">
          <section id="time-scrubber" className="scroll-mt-4">
            <ForecastTimeSlider
              bundle={auto.bundle}
              index={hour.index}
              isPlaying={hour.isPlaying}
              onIndexChange={hour.setIndex}
              onNext={hour.next}
              onPrev={hour.prev}
              onTogglePlay={hour.togglePlay}
              artifactIndex={outlookArtifacts.artifacts?.incrementalIndex}
            />
          </section>

          <section id="outlook-map" className="scroll-mt-4">
            <OutlookMapPanel
              snapshot={snapshot}
              outlookArtifacts={outlookArtifacts}
              bundle={auto.bundle}
              selectedIndex={hour.index}
              isPlaying={hour.isPlaying}
              onIndexChange={hour.setIndex}
              setPlaying={hour.setPlaying}
              activeRegion={activeRegion}
              selectedMergedDate={selectedMergedDate}
              setSelectedMergedDate={setSelectedMergedDate}
              viewType={viewType}
              setViewType={setViewType}
              stormReportsMode={stormReportsMode}
              setStormReportsMode={setStormReportsMode}
              stormReports={stormReports}
            />
          </section>

          <section id="primary-outlook" className="scroll-mt-4">
            <PrimaryOutlookBanner snapshot={snapshot} artifacts={outlookArtifacts.artifacts} artifactStatus={outlookArtifacts.status} />
          </section>

          <section id="hazards" className="scroll-mt-4">
            <HazardProbabilityBoard snapshot={snapshot} artifacts={outlookArtifacts.artifacts} artifactStatus={outlookArtifacts.status} />
          </section>

          <section id="ingredients" className="scroll-mt-4">
            <EnvironmentalIngredientsGrid snapshot={snapshot} />
          </section>

          <section id="timeline" className="scroll-mt-4">
            <RiskTimeline
              bundle={auto.bundle}
              selectedForecastHour={snapshot?.forecastHour}
              artifacts={outlookArtifacts.artifacts}
              artifactStatus={outlookArtifacts.status}
              onHourChange={(h) => {
                if (auto.bundle) {
                  const idx = auto.bundle.hours.findIndex((snap) => snap.forecastHour === h);
                  if (idx !== -1) {
                    hour.setIndex(idx);
                  }
                }
              }}
            />
          </section>

          <section id="discussion" className="scroll-mt-4">
            <ForecastDiscussion snapshot={snapshot} />
          </section>

          <section id="readiness" className="scroll-mt-4">
            <WatchReadinessPanel
              snapshot={snapshot}
              artifacts={outlookArtifacts.artifacts}
              artifactStatus={outlookArtifacts.status}
            />
          </section>

          <section id="verification" className="scroll-mt-4">
            <VerificationPanel
              spcVerification={outlookArtifacts.artifacts?.metadata?.spcVerification}
              mergedD1Verification={mergedD1Verification}
              viewType={viewType}
            />
          </section>

          <section id="system-status" className="scroll-mt-4">
            <SystemStatusPanel
              bundle={auto.bundle}
              status={auto.status}
              attempted={auto.attempted}
              selectedHour={snapshot?.forecastHour}
              selectedValidTime={snapshot?.validTimeISO}
              outlookArtifacts={outlookArtifacts}
              refreshIntervalMs={auto.refreshIntervalMs}
              onRefresh={auto.refreshNow}
            />
          </section>
        </main>
          </>
        )}

        <footer className="border-t-[3px] border-ink bg-ink text-paper">
          <div className="w-full px-4 py-3 xl:px-5 flex items-center justify-between flex-wrap gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
              AutoOutlook · Automated Convective Risk Intelligence · v1.1
            </span>
            <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/40">
              {mlDriven
                ? 'Hazard-probability model · Provider chain: live → fallback → mock'
                : 'Rule-based outlook engine · Provider chain: live → fallback → mock'}
            </span>
          </div>
        </footer>
      </div>
    </div>
    <ViewTransitionOverlay key={`tx-${view}`} view={view} cycle={0} />
    </>
  );
}
