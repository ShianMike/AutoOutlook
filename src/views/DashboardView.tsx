import { useState } from 'react';

import AppFooter from '../components/AppFooter';
import CommandHeader from '../components/CommandHeader';
import DashboardSidebar from '../components/DashboardSidebar';
import EnvironmentalIngredientsGrid from '../components/EnvironmentalIngredientsGrid';
import ForecastDiscussion from '../components/ForecastDiscussion';
import ForecastTimeSlider from '../components/ForecastTimeSlider';
import HazardProbabilityBoard from '../components/HazardProbabilityBoard';
import OutlookMapPanel from '../components/OutlookMapPanel';
import PrimaryOutlookBanner from '../components/PrimaryOutlookBanner';
import RiskTimeline from '../components/RiskTimeline';
import SystemStatusPanel from '../components/SystemStatusPanel';
import VerificationPanel from '../components/VerificationPanel';
import WatchReadinessPanel from '../components/WatchReadinessPanel';
import { useAutoForecast } from '../hooks/useAutoForecast';
import { useForecastHour } from '../hooks/useForecastHour';
import {
  useMergedD1Artifacts,
  useMergedD1Verification,
  useOutlookArtifacts,
  useSpcStormReports,
} from '../hooks/useOutlookArtifacts';
import { FORECAST_HOUR_LABELS, type ActiveRegion } from '../types/forecast';

export default function DashboardView() {
  const activeRegion: ActiveRegion = 'conus';
  const [selectedMergedDate, setSelectedMergedDate] = useState('');
  const [mergedDay, setMergedDay] = useState<1 | 2>(1);
  const [viewType, setViewType] = useState<'hourly' | 'merged'>('merged');
  const [spcBacked, setSpcBacked] = useState(true);
  const [stormReportsMode, setStormReportsMode] = useState<'none' | 'all' | 'tornado' | 'hail' | 'wind'>('none');

  const auto = useAutoForecast(activeRegion, true);
  const hour = useForecastHour(auto.bundle);
  const snapshot = hour.snapshot;
  const outlookArtifacts = useOutlookArtifacts(
    snapshot?.forecastHour,
    snapshot?.validTimeISO,
    activeRegion,
    15 * 1000,
    true,
  );
  const mergedD1Verification = useMergedD1Verification(activeRegion, selectedMergedDate, true, mergedDay);
  const stormReports = useSpcStormReports(activeRegion, selectedMergedDate, true);
  const isMerged = viewType === 'merged';
  const mergedArtifacts = useMergedD1Artifacts(activeRegion, selectedMergedDate, {
    enabled: isMerged,
    day: mergedDay,
    backing: spcBacked ? 'blend' : 'pure',
  });
  const panelArtifactState = isMerged ? mergedArtifacts : outlookArtifacts;
  const panelSnapshot = isMerged && snapshot ? { ...snapshot, forecastHour: 0 } : snapshot;
  const mlDriven = Boolean(auto.bundle?.mlModel?.active && auto.bundle.mlHazardHours);
  const hourLabel = snapshot
    ? FORECAST_HOUR_LABELS[snapshot.forecastHour] ?? `+${snapshot.forecastHour}h`
    : undefined;

  return (
    <div className="min-h-screen bg-paper text-ink lg:flex">
      <DashboardSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
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
              spcBacked={spcBacked}
              setSpcBacked={setSpcBacked}
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
              artifacts={panelArtifactState.artifacts}
              artifactStatus={panelArtifactState.status}
              viewType={viewType}
              onHourChange={(forecastHour) => {
                if (!auto.bundle) return;
                const index = auto.bundle.hours.findIndex((item) => item.forecastHour === forecastHour);
                if (index !== -1) hour.setIndex(index);
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

        <AppFooter mlDriven={mlDriven} />
      </div>
    </div>
  );
}
