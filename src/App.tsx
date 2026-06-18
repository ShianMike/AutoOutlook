import { useEffect, useRef, useState } from 'react';

import { useAutoForecast } from './hooks/useAutoForecast';
import { useForecastHour } from './hooks/useForecastHour';
import { useOutlookArtifacts, useMergedD1Verification, useSpcStormReports, useMergedD1Artifacts } from './hooks/useOutlookArtifacts';
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
import { DASHBOARD_ANCHORS, NAVIGATION_UNRESOLVED_EVENT } from './utils/navigateView';

type AppView = 'landing' | 'dashboard' | 'docs' | 'changelog';

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
  const [mergedDay, setMergedDay] = useState<1 | 2>(1);
  const [viewType, setViewType] = useState<'hourly' | 'merged'>('merged');
  const [stormReportsMode, setStormReportsMode] = useState<'none' | 'all' | 'tornado' | 'hail' | 'wind'>('none');
  const [view, setView] = useState<AppView>(() => viewFromHash());
  // Monotonically increasing navigation counter. Bumped on every active-view
  // change so the transition overlay remounts (and replays) even when the
  // destination view is the same as the current one (re-navigation). Rapid
  // successive view changes all funnel through the single `view`/`cycle` pair,
  // so only one overlay — for the most recently requested destination — is ever
  // mounted at a time (Req 9.1, 9.5).
  const [cycle, setCycle] = useState(0);

  // Transient, non-blocking indication shown when a navigation request cannot be
  // resolved to a known destination view. The active view/cycle is left
  // untouched (so no overlay mounts for the unresolved destination); we only
  // surface a `role="status"` notice that the view change did not complete
  // (Req 9.6). The `nonce` lets repeated failures re-arm the auto-dismiss timer.
  const [navNotice, setNavNotice] = useState<{ message: string; nonce: number } | null>(null);

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
  const mergedD1Verification = useMergedD1Verification(activeRegion, selectedMergedDate, dashboardDataEnabled, mergedDay);
  const stormReports = useSpcStormReports(activeRegion, selectedMergedDate, dashboardDataEnabled);
  const isMerged = viewType === 'merged';
  // In merged mode the summary panels must describe the multi-cycle Day 1
  // outlook (which the map already shows), not the selected forecast hour.
  // Feed them the merged artifact + a hour-0 snapshot so the category and
  // hazard probabilities match the merged map instead of reading e.g. MRGL
  // from the scrubber hour while the map shows ENH.
  const mergedArtifacts = useMergedD1Artifacts(activeRegion, selectedMergedDate, {
    enabled: dashboardDataEnabled && isMerged,
    day: mergedDay,
  });
  const panelArtifactState = isMerged ? mergedArtifacts : outlookArtifacts;
  const panelSnapshot = isMerged && snapshot ? { ...snapshot, forecastHour: 0 } : snapshot;
  const mlDriven = Boolean(auto.bundle?.mlModel?.active && auto.bundle.mlHazardHours);
  const hourLabel = snapshot
    ? FORECAST_HOUR_LABELS[snapshot.forecastHour] ?? `+${snapshot.forecastHour}h`
    : undefined;

  useEffect(() => {
    const sync = () => setView(viewFromHash());
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);

  // Surface a non-blocking notice when a navigation request fails to resolve to
  // a known destination view. The current view is retained (we do not call
  // setView/setCycle), so no transition overlay is mounted for the unresolved
  // destination (Req 9.6).
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onUnresolved = () =>
      setNavNotice((prev) => ({
        message: 'View change did not complete',
        nonce: (prev?.nonce ?? 0) + 1,
      }));
    window.addEventListener(NAVIGATION_UNRESOLVED_EVENT, onUnresolved);
    return () => window.removeEventListener(NAVIGATION_UNRESOLVED_EVENT, onUnresolved);
  }, []);

  // Auto-dismiss the transient navigation notice. Re-arms whenever a new failure
  // bumps the nonce so consecutive failures keep the message visible briefly.
  useEffect(() => {
    if (!navNotice) return;
    const timer = window.setTimeout(() => setNavNotice(null), 4000);
    return () => window.clearTimeout(timer);
  }, [navNotice?.nonce]);

  // Bump the navigation cycle whenever the resolved view actually changes.
  // Intermediate requests in a rapid sequence collapse into the single `view`
  // state, so this coalesces them into one overlay for the latest destination.
  // The first effect run (initial mount) leaves `cycle` at 0 so the overlay
  // does not double-mount on load.
  const prevViewRef = useRef(view);
  useEffect(() => {
    if (prevViewRef.current !== view) {
      prevViewRef.current = view;
      setCycle((c) => c + 1);
    }
  }, [view]);

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

  // Non-blocking, accessible indication that a navigation request did not
  // resolve. Rendered alongside whichever view is currently active so the user
  // sees the failure without the active view changing (Req 9.6).
  const navNoticeEl = navNotice ? (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 border-[3px] border-ink bg-signal-amber px-4 py-2 font-mono text-[11px] uppercase tracking-[0.2em] text-ink shadow-retro"
    >
      {navNotice.message}
    </div>
  ) : null;

  if (view === 'landing') {
    return (
      <>
        <LandingPage />
        <ViewTransitionOverlay key={`tx-${view}-${cycle}`} view={view} cycle={cycle} />
        {navNoticeEl}
      </>
    );
  }

  if (view === 'changelog') {
    return (
      <>
        <ChangelogPage />
        <ViewTransitionOverlay key={`tx-${view}-${cycle}`} view={view} cycle={cycle} />
        {navNoticeEl}
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
          {viewType !== 'merged' && (
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
          )}

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
              mergedDay={mergedDay}
              setMergedDay={setMergedDay}
              viewType={viewType}
              setViewType={setViewType}
              stormReportsMode={stormReportsMode}
              setStormReportsMode={setStormReportsMode}
              stormReports={stormReports}
            />
          </section>

          <section id="primary-outlook" className="scroll-mt-4">
            <PrimaryOutlookBanner snapshot={panelSnapshot} artifacts={panelArtifactState.artifacts} artifactStatus={panelArtifactState.status} viewType={viewType} />
          </section>

          <section id="discussion" className="scroll-mt-4">
            <ForecastDiscussion snapshot={snapshot} artifacts={panelArtifactState.artifacts} artifactStatus={panelArtifactState.status} viewType={viewType} />
          </section>

          <section id="verification" className="scroll-mt-4">
            <VerificationPanel
              spcVerification={outlookArtifacts.artifacts?.metadata?.spcVerification}
              mergedD1Verification={mergedD1Verification}
              viewType={viewType}
            />
          </section>

          <section id="hazards" className="scroll-mt-4">
            <HazardProbabilityBoard snapshot={panelSnapshot} artifacts={panelArtifactState.artifacts} artifactStatus={panelArtifactState.status} viewType={viewType} />
          </section>

          <section id="ingredients" className="scroll-mt-4">
            <EnvironmentalIngredientsGrid snapshot={snapshot} artifacts={panelArtifactState.artifacts} viewType={viewType} />
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

          <section id="readiness" className="scroll-mt-4">
            <WatchReadinessPanel
              snapshot={snapshot}
              artifacts={outlookArtifacts.artifacts}
              artifactStatus={outlookArtifacts.status}
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
              AutoOutlook · Automated Convective Risk Intelligence · v1.2.3
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
    <ViewTransitionOverlay key={`tx-${view}-${cycle}`} view={view} cycle={cycle} />
    {navNoticeEl}
    </>
  );
}
