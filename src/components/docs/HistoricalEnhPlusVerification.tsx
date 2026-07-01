import { useEffect, useMemo, useState } from 'react';
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
import { useEnhPlusArchiveEvents, type OutlookArtifactState } from '../../hooks/useOutlookArtifacts';
import {
  DEFAULT_BACKING_MODE,
  backingModeToSpcBacked,
  type MergedArtifactsOverride,
} from '../../utils/backingResolver';
import {
  loadEnhPlusCatalogEvent,
  loadEnhPlusCatalogIndex,
  type EnhPlusArchiveCatalogItem,
  type HistoricalEnhPlusEvent,
} from '../../data/historicalEnhPlusVerification';
import { orderArchiveEvents } from '../../utils/archiveEventOrdering';

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
  const { events: liveEvents } = useEnhPlusArchiveEvents('conus', true);
  const [catalogIndex, setCatalogIndex] = useState<EnhPlusArchiveCatalogItem[]>([]);

  // Load the lightweight catalog index (metadata only). The heavy per-event
  // geojson is fetched lazily when an event is selected.
  useEffect(() => {
    const controller = new AbortController();
    loadEnhPlusCatalogIndex(controller.signal)
      .then(setCatalogIndex)
      .catch(() => setCatalogIndex([]));
    return () => controller.abort();
  }, []);

  // Merge live + catalog metadata into one ordered list; live events (full)
  // win date ties over catalog metadata (Requirement 5.1-5.4).
  const orderedList = useMemo(
    () => orderArchiveEvents<EnhPlusArchiveCatalogItem>(liveEvents, catalogIndex),
    [liveEvents, catalogIndex],
  );
  const eventDates = useMemo(() => orderedList.map((item) => item.eventDate), [orderedList]);

  const [selectedDate, setSelectedDate] = useState('');
  const [viewType, setViewType] = useState<'hourly' | 'merged'>('merged');
  const [stormReportsMode, setStormReportsMode] = useState<'none' | 'all' | 'tornado' | 'hail' | 'wind'>('all');
  // SPC backing comparison mode; defaults to "SPC Blend" and resets per event.
  const [spcBacked, setSpcBacked] = useState(backingModeToSpcBacked(DEFAULT_BACKING_MODE));

  const selectedMeta = useMemo(
    () => orderedList.find((item) => item.eventDate === selectedDate) ?? orderedList[0],
    [orderedList, selectedDate],
  );
  const resolvedDate = selectedMeta?.eventDate;

  // Resolve the selected event's full payload: a live event already carries it;
  // a catalog event lazy-loads its static per-event JSON on demand.
  const [event, setEvent] = useState<HistoricalEnhPlusEvent | null>(null);
  useEffect(() => {
    if (!resolvedDate) {
      setEvent(null);
      return undefined;
    }
    const live = liveEvents.find((item) => item.eventDate === resolvedDate);
    if (live) {
      setEvent(live);
      return undefined;
    }
    let cancelled = false;
    const controller = new AbortController();
    setEvent(null);
    loadEnhPlusCatalogEvent(resolvedDate, controller.signal)
      .then((full) => { if (!cancelled) setEvent(full); })
      .catch(() => { if (!cancelled) setEvent(null); });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [resolvedDate, liveEvents]);

  useEffect(() => {
    setSpcBacked(backingModeToSpcBacked(DEFAULT_BACKING_MODE));
  }, [resolvedDate]);

  const snapshot = useMemo(() => (event ? buildSnapshot(event) : null), [event]);
  const bundle = useMemo(
    () => (event && snapshot ? buildBundle(event, snapshot) : null),
    [event, snapshot],
  );
  // Both backing variants come from the loaded event: "SPC Blend" is the
  // SPC-backed merged outlook and "Our Model" is the pure HRRR/XGBoost outlook.
  const blendArtifactState = useMemo(() => (event ? buildArtifactState(event, 'blend') : null), [event]);
  const pureArtifactState = useMemo(() => (event ? buildArtifactState(event, 'pure') : null), [event]);
  const mergedOverride = useMemo<MergedArtifactsOverride | undefined>(
    () => (pureArtifactState && blendArtifactState
      ? { pure: pureArtifactState, blend: blendArtifactState }
      : undefined),
    [pureArtifactState, blendArtifactState],
  );

  if (!selectedMeta) {
    return (
      <div className="border-[3px] border-ink bg-paper p-4 font-mono text-[11px] font-bold uppercase tracking-widest text-ink/60 shadow-retro-sm">
        Loading the 2026 risk verification archive…
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
        <ArchiveMetric label="Event" value={selectedMeta.label} />
        <ArchiveMetric label="SPC Peak" value={selectedMeta.maxSpcCategory} />
        <ArchiveMetric label="Run Cycle" value={formatUtc(selectedMeta.cycleTimeISO)} />
        <ArchiveMetric label="Risk Window" value={`${formatHour(selectedMeta.eventWindowStartISO)}-${formatHour(selectedMeta.eventWindowEndISO)}`} />
        <ArchiveMetric
          label="Grid / Tile Stride"
          value={`${selectedMeta.gridStride ?? '—'} / ${selectedMeta.tileStride ?? '—'}`}
        />
      </div>

      {event && snapshot && bundle && blendArtifactState && mergedOverride ? (
        <OutlookMapPanel
          snapshot={snapshot}
          outlookArtifacts={blendArtifactState}
          bundle={bundle}
          selectedIndex={0}
          isPlaying={false}
          onIndexChange={() => undefined}
          setPlaying={() => undefined}
          activeRegion="conus"
          selectedMergedDate={resolvedDate ?? ''}
          setSelectedMergedDate={setSelectedDate}
          viewType={viewType}
          setViewType={setViewType}
          spcBacked={spcBacked}
          setSpcBacked={setSpcBacked}
          stormReportsMode={stormReportsMode}
          setStormReportsMode={setStormReportsMode}
          stormReports={event.stormReports}
          availableMergedDatesOverride={eventDates}
          mergedArtifactsOverride={mergedOverride}
          spcDay1Override={event.spcDay1}
          spcHazardProbabilityShapesOverride={event.spcHazardProbabilityShapes}
          initialSpcComparisonMode="overlay"
          staticStormReportsAvailable
        />
      ) : (
        <div className="border-[3px] border-ink bg-paper p-6 text-center font-mono text-[11px] font-bold uppercase tracking-widest text-ink/60 shadow-retro-sm">
          Loading outlook for {selectedMeta.label}…
        </div>
      )}
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

function buildArtifactState(
  event: HistoricalEnhPlusEvent,
  backing: 'blend' | 'pure' = 'blend',
): OutlookArtifactState {
  const verification = event.summary as MergedD1VerificationSummary;
  const categoryCounts = verification.predictedCategories;
  // Pick the baked variant: "SPC Blend" uses the SPC-backed merged outlook,
  // "Our Model" uses the pure HRRR/XGBoost merged outlook. Both are archived
  // per event so the backing toggle needs no live backend call.
  const riskPolygons = backing === 'pure' ? event.riskPolygonsPure : event.riskPolygons;
  const hazardShapes = backing === 'pure'
    ? event.hazardProbabilityShapesPure
    : event.hazardProbabilityShapes;
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
    riskShapes: riskPolygons,
    hazardProbabilityShapes: hazardShapes,
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
      riskPolygons,
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
