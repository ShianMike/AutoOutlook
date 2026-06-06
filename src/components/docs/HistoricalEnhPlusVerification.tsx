import { useMemo, useState } from 'react';
import type {
  ForecastBundle,
  HazardAssessment,
  HazardKey,
  HourSnapshot,
  Ingredients,
  RiskCategory,
} from '../../types/forecast';
import type {
  ArtifactRiskCategory,
  MergedD1VerificationSummary,
  OutlookArtifactMetadata,
  OutlookProbabilityTile,
  OutlookProbabilityTiles,
} from '../../types/outlookArtifacts';
import OutlookMapPanel from '../OutlookMapPanel';
import type { OutlookArtifactState } from '../../hooks/useOutlookArtifacts';
import {
  HISTORICAL_ENH_PLUS_EVENTS,
  type HistoricalEnhPlusEvent,
} from '../../data/historicalEnhPlusVerification';

const EMPTY_INGREDIENTS: Ingredients = {
  mlcape: 0,
  mucape: 0,
  sbcape: 0,
  cin: 0,
  sfcDewpointF: 0,
  pwatIn: 0,
  lclM: 0,
  moistureDepthM: 0,
  srh01: 0,
  srh03: 0,
  shear06Kt: 0,
  stormRelWindKt: 0,
  frontSignal: 'none',
  initiationConf: 0,
  stormMode: 'mixed',
  capStrength: 'none',
  stp: 0,
  scp: 0,
  ehi: 0,
  ship: 0,
  tornadoComposite: 0,
};

export default function HistoricalEnhPlusVerification() {
  const eventDates = useMemo(
    () => HISTORICAL_ENH_PLUS_EVENTS.map((event) => event.eventDate),
    [],
  );
  const [selectedDate, setSelectedDate] = useState(eventDates[0] ?? '');
  const [viewType, setViewType] = useState<'hourly' | 'merged'>('merged');
  const [stormReportsMode, setStormReportsMode] = useState<'none' | 'all' | 'tornado' | 'hail' | 'wind'>('all');

  const event = HISTORICAL_ENH_PLUS_EVENTS.find((item) => item.eventDate === selectedDate)
    ?? HISTORICAL_ENH_PLUS_EVENTS[0];

  const snapshot = useMemo(() => buildSnapshot(event), [event]);
  const bundle = useMemo(() => buildBundle(event, snapshot), [event, snapshot]);
  const artifactState = useMemo(() => buildArtifactState(event), [event]);

  if (!event) {
    return (
      <div className="border-[3px] border-ink bg-paper p-4 font-mono text-[11px] font-bold uppercase tracking-widest text-ink/60 shadow-retro-sm">
        No historical 2026 risk verification events are hardcoded yet.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
        <ArchiveMetric label="Event" value={event.label} />
        <ArchiveMetric label="SPC Peak" value={event.maxSpcCategory} />
        <ArchiveMetric label="Run Cycle" value={formatUtc(event.cycleTimeISO)} />
        <ArchiveMetric label="Risk Window" value={`${formatHour(event.eventWindowStartISO)}-${formatHour(event.eventWindowEndISO)}`} />
        <ArchiveMetric
          label="Grid / Tile Stride"
          value={`${event.gridStride ?? '—'} / ${event.tileStride ?? '—'}`}
        />
      </div>

      <OutlookMapPanel
        snapshot={snapshot}
        outlookArtifacts={artifactState}
        bundle={bundle}
        selectedIndex={0}
        isPlaying={false}
        onIndexChange={() => undefined}
        setPlaying={() => undefined}
        activeRegion="conus"
        selectedMergedDate={selectedDate}
        setSelectedMergedDate={setSelectedDate}
        viewType={viewType}
        setViewType={setViewType}
        stormReportsMode={stormReportsMode}
        setStormReportsMode={setStormReportsMode}
        stormReports={event.stormReports}
        availableMergedDatesOverride={eventDates}
        mergedArtifactsOverride={artifactState}
        spcDay1Override={event.spcDay1}
        spcHazardProbabilityShapesOverride={event.spcHazardProbabilityShapes}
        initialSpcComparisonMode="overlay"
        staticStormReportsAvailable
      />
    </div>
  );
}

function ArchiveMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border-[2px] border-ink bg-paper p-2 shadow-retro-sm">
      <div className="font-mono text-[9px] uppercase tracking-widest text-ink/55">{label}</div>
      <div className="mt-1 truncate font-display text-base font-extrabold uppercase leading-none text-ink">
        {value}
      </div>
    </div>
  );
}

function buildArtifactState(event: HistoricalEnhPlusEvent): OutlookArtifactState {
  const verification = event.summary as MergedD1VerificationSummary;
  const categoryCounts = verification.predictedCategories;
  const tile: OutlookProbabilityTile = {
    forecastHour: 0,
    validTimeISO: event.eventWindowStartISO,
    stride: event.tileStride ?? 0,
    shape: event.tileShape ?? [],
    lats: [],
    lons: [],
    categoryOrdinal: [],
    categoryLabel: [],
    probabilities: {
      tornado: [],
      hail: [],
      wind: [],
    },
    riskShapes: event.riskPolygons,
    hazardProbabilityShapes: event.hazardProbabilityShapes,
  };
  const probabilityTiles: OutlookProbabilityTiles = {
    cycle: cycleLabel(event),
    riskLabels: ['NONE', 'TSTM', 'MRGL', 'SLGT', 'ENH', 'MDT', 'HIGH'],
    gridStride: event.gridStride ?? undefined,
    tileStride: event.tileStride ?? undefined,
    hours: [
      {
        forecastHour: 0,
        validTimeISO: event.eventWindowStartISO,
        categoryCounts,
        tile,
      },
    ],
  };
  const metadata: OutlookArtifactMetadata = {
    generatedAtISO: stringFromRecord(event.summary, 'spcFetchedAtISO') ?? event.cycleTimeISO,
    cycle: cycleLabel(event),
    cycleTimeISO: event.cycleTimeISO,
    requestedForecastHours: event.forecastHours,
    readyForecastHours: [0],
    pendingForecastHours: [],
    failedForecastHours: [],
    forecastHours: [0],
    selectedArtifactForecastHour: 0,
    artifactForecastHour: 0,
    artifactValidTimeISO: event.eventWindowStartISO,
    categoryCounts,
    spcVerification: verification,
    mode: 'full',
    status: 'complete',
  };

  return {
    status: 'ready',
    artifacts: {
      metadata,
      riskPolygons: event.riskPolygons,
      probabilityTiles,
      selectedArtifactForecastHour: 0,
      selectedHourStatus: 'ready',
    },
    message: null,
  };
}

function buildBundle(event: HistoricalEnhPlusEvent, snapshot: HourSnapshot): ForecastBundle {
  return {
    cycle: cycleLabel(event),
    issuedAtISO: event.cycleTimeISO,
    hours: [snapshot],
    source: 'live',
    providerId: 'backend',
    providerNotes: 'Hardcoded local historical 2026 risk verification artifact.',
    latencyMs: 0,
    fetchedAtISO: stringFromRecord(event.summary, 'spcFetchedAtISO') ?? event.cycleTimeISO,
    mlHazardHours: 1,
    mlModel: {
      active: true,
      version: 'historical-enh-plus-static',
      featureSchemaHash: 'historical-static',
      reason: 'Local hardcoded historical verification artifact.',
    },
  };
}

function buildSnapshot(event: HistoricalEnhPlusEvent): HourSnapshot {
  const category = riskCategoryFromCounts((event.summary as MergedD1VerificationSummary).predictedCategories);
  const hazards = {
    tornado: hazardAssessment('tornado'),
    hail: hazardAssessment('hail'),
    wind: hazardAssessment('wind'),
    flood: hazardAssessment('flood'),
  };
  return {
    forecastHour: 0,
    validTimeISO: event.eventWindowStartISO,
    region: {
      label: `${event.label} risk verification`,
      centerLat: 38,
      centerLon: -97,
      bbox: [-125, 24, -66, 50],
      states: ['CONUS'],
    },
    ingredients: EMPTY_INGREDIENTS,
    hazards,
    outlook: {
      category,
      mainHazard: 'wind',
      confidence: 1,
      significantSevere: category === 'ENH' || category === 'MOD' || category === 'HIGH',
      headline: `${event.label} historical risk verification`,
    },
    outlookAreas: [],
    riskPolygons: [],
    cities: [],
  };
}

function hazardAssessment(hazard: HazardKey): HazardAssessment {
  return {
    level: 'TSTM',
    probability: 0,
    confidence: 1,
    significantSevere: false,
    source: 'ml',
    supporting: [],
    explanation: `${hazard} probability comes from the hardcoded historical artifact shapes.`,
  };
}

function riskCategoryFromCounts(counts: Record<string, number> | undefined): RiskCategory {
  if (!counts) return 'TSTM';
  const order: ArtifactRiskCategory[] = ['NONE', 'TSTM', 'MRGL', 'SLGT', 'ENH', 'MDT', 'HIGH'];
  let best: ArtifactRiskCategory = 'NONE';
  order.forEach((category) => {
    if ((counts[category] ?? 0) > 0) best = category;
  });
  if (best === 'NONE' || best === 'TSTM') return 'TSTM';
  if (best === 'MDT') return 'MOD';
  return best as RiskCategory;
}

function cycleLabel(event: HistoricalEnhPlusEvent): string {
  return `HRRR 00Z ${event.eventDate.replace(/-/g, '')}`;
}

function stringFromRecord(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key];
  return typeof value === 'string' ? value : undefined;
}

function formatUtc(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${String(date.getUTCHours()).padStart(2, '0')}Z ${String(date.getUTCMonth() + 1).padStart(2, '0')}/${String(date.getUTCDate()).padStart(2, '0')}`;
}

function formatHour(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${String(date.getUTCHours()).padStart(2, '0')}Z`;
}
