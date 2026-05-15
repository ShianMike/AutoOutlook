import { useAutoForecast } from './hooks/useAutoForecast';
import { useForecastHour } from './hooks/useForecastHour';
import { useOutlookArtifacts } from './hooks/useOutlookArtifacts';
import { FORECAST_HOUR_LABELS } from './types/forecast';

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

export default function App() {
  const auto = useAutoForecast();
  const hour = useForecastHour(auto.bundle);
  const snapshot = hour.snapshot;
  const outlookArtifacts = useOutlookArtifacts(snapshot?.forecastHour, snapshot?.validTimeISO);
  const mlDriven = Boolean(auto.bundle?.mlModel?.active && auto.bundle.mlHazardHours);
  const hourLabel = snapshot
    ? FORECAST_HOUR_LABELS[snapshot.forecastHour] ?? `+${snapshot.forecastHour}h`
    : undefined;

  return (
    <div className="min-h-screen bg-paper text-ink lg:flex">
      <DashboardSidebar
        onRefresh={auto.refreshNow}
      />

      <div className="flex min-w-0 flex-1 flex-col">
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

        <footer className="border-t-[3px] border-ink bg-ink text-paper">
          <div className="w-full px-4 py-3 xl:px-5 flex items-center justify-between flex-wrap gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
              AutoOutlook · Automated Convective Risk Intelligence · v0.1
            </span>
            <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/40">
              {mlDriven
                ? 'XGBoost hazard model · Provider chain: NOMADS → Open-Meteo → mock'
                : 'Rule-based outlook engine · Provider chain: NOMADS → Open-Meteo → mock'}
            </span>
          </div>
        </footer>
      </div>
    </div>
  );
}
