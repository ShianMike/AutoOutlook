import { useEffect, useRef, useState, type ReactNode } from 'react';
import { toPng } from 'html-to-image';
import type { ForecastBundle, HourSnapshot, ActiveRegion } from '../types/forecast';
import { FORECAST_HOUR_LABELS } from '../types/forecast';
import RetroPanel from './retro/RetroPanel';
import RetroBadge from './retro/RetroBadge';
import HazardOutlookMap from './HazardOutlookMap';
import GeneratedOutlookMap, { type SpcComparisonMode } from './GeneratedOutlookMap';
import GeneratedHazardProbabilityMap, { hasGeneratedHazardTile } from './GeneratedHazardProbabilityMap';
import ForecastDisclaimer from './ForecastDisclaimer';
import { useMergedD1Artifacts, useSpcBackedHourArtifacts, useSpcHazardShapes, type OutlookArtifactState } from '../hooks/useOutlookArtifacts';
import { resolveBacking, type MergedArtifactsOverride } from '../utils/backingResolver';
import type {
  OutlookArtifacts,
  MergedD1VerificationSummary,
  OutlookProbabilityShapeFeatureCollection,
  SpcCategoryFeatureCollection,
  SpcStormReport,
} from '../types/outlookArtifacts';
import { focusLocationFromSnapshot } from '../utils/focusLocation';
import { recordCanvasesToGif } from '../utils/gifRecorder';
import type { OutlookHazardKey } from '../utils/hazardProbabilityBands';
import { MAP_ZOOM_REGIONS, MAP_ZOOM_REGION_ORDER, type MapZoomRegion } from '../utils/mapZoomRegions';
import { apiUrl } from '../utils/apiBase';

interface OutlookMapPanelProps {
  snapshot: HourSnapshot | null;
  outlookArtifacts: OutlookArtifactState;
  bundle: ForecastBundle | null;
  selectedIndex: number;
  isPlaying: boolean;
  onIndexChange: (index: number) => void;
  setPlaying: (playing: boolean) => void;
  activeRegion: ActiveRegion;
  selectedMergedDate: string;
  setSelectedMergedDate: (date: string) => void;
  mergedDay?: 1 | 2;
  setMergedDay?: (day: 1 | 2) => void;
  viewType: 'hourly' | 'merged';
  setViewType: (type: 'hourly' | 'merged') => void;
  stormReportsMode?: StormReportsMode;
  setStormReportsMode?: (mode: StormReportsMode) => void;
  stormReports?: SpcStormReport[];
  availableMergedDatesOverride?: string[];
  mergedArtifactsOverride?: MergedArtifactsOverride;
  spcDay1Override?: SpcCategoryFeatureCollection | null;
  spcHazardProbabilityShapesOverride?: OutlookProbabilityShapeFeatureCollection | null;
  spcBacked?: boolean;
  setSpcBacked?: (value: boolean) => void;
  initialSpcComparisonMode?: SpcComparisonMode;
  staticStormReportsAvailable?: boolean;
}

type OutlookMode = 'levels' | 'hazards';
type StormReportsMode = 'none' | 'all' | 'tornado' | 'hail' | 'wind';
type GifQualityPreset = 'small' | 'medium' | 'large';

// Displayed when an archive backing mode is selected but no outlook data is
// available for it (and nothing was previously displayed to retain). Drives the
// "unavailable comparison" notice (Requirement 6.5).
const UNAVAILABLE_BACKING_STATE: OutlookArtifactState = {
  status: 'missing',
  artifacts: null,
  message: 'Selected comparison unavailable for this event.',
};

interface GifProgressState {
  current: number;
  total: number;
  phase: 'capturing' | 'encoding';
}

const GIF_DEFAULT_DELAY_MS = 600;
const GIF_DELAY_OPTIONS = [300, 500, 600, 800, 1200];
const GIF_CAPTURE_TIMEOUT_MS = 8000;
const GIF_CAPTURE_POLL_MS = 150;
const EXPORT_CAPTURE_CSS_WIDTH = 2000;
const EXPORT_CAPTURE_CSS_HEIGHT = 1320;
const EXPORT_PIXEL_RATIO = 2;
const EXPORT_BACKGROUND_COLOR = '#f5f0e6';
const EXPORT_FIXED_LAYOUT_CSS = `
[data-outlook-export-area="true"] {
  box-sizing: border-box !important;
  width: ${EXPORT_CAPTURE_CSS_WIDTH}px !important;
  min-width: ${EXPORT_CAPTURE_CSS_WIDTH}px !important;
  max-width: ${EXPORT_CAPTURE_CSS_WIDTH}px !important;
  height: ${EXPORT_CAPTURE_CSS_HEIGHT}px !important;
  min-height: ${EXPORT_CAPTURE_CSS_HEIGHT}px !important;
  max-height: ${EXPORT_CAPTURE_CSS_HEIGHT}px !important;
  overflow: hidden !important;
  display: flex !important;
  flex-direction: column !important;
}
[data-outlook-export-area="true"] * {
  box-sizing: border-box !important;
}
[data-outlook-export-area="true"] .outlook-export-topbar {
  flex: 0 0 92px !important;
  height: 92px !important;
  min-height: 92px !important;
  max-height: 92px !important;
  display: flex !important;
  flex-wrap: nowrap !important;
}
[data-outlook-export-area="true"] .outlook-export-metabar {
  flex: 0 0 36px !important;
  height: 36px !important;
  min-height: 36px !important;
  max-height: 36px !important;
  display: flex !important;
  flex-wrap: nowrap !important;
}
[data-outlook-export-area="true"] .outlook-export-stage {
  flex: 1 1 auto !important;
  min-height: 0 !important;
  height: auto !important;
  overflow: hidden !important;
}
[data-outlook-export-area="true"] .outlook-export-hazard-grid {
  display: grid !important;
  grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  grid-template-rows: repeat(2, minmax(0, 1fr)) !important;
  gap: 8px !important;
}
[data-outlook-export-area="true"] .outlook-export-hazard-grid > * {
  min-width: 0 !important;
  min-height: 0 !important;
}
[data-outlook-export-area="true"] .outlook-export-unavailable {
  grid-column: 1 / -1 !important;
  grid-row: 1 / -1 !important;
  height: 100% !important;
}
[data-outlook-export-area="true"] .outlook-export-map-card {
  display: flex !important;
  flex-direction: column !important;
  height: 100% !important;
  min-height: 0 !important;
  overflow: hidden !important;
}
[data-outlook-export-area="true"] .outlook-export-map-frame {
  flex: 1 1 auto !important;
  height: auto !important;
  min-height: 0 !important;
  aspect-ratio: auto !important;
}
[data-outlook-export-area="true"] .outlook-export-footer {
  flex: 0 0 34px !important;
  height: 34px !important;
  min-height: 34px !important;
  max-height: 34px !important;
  flex-wrap: nowrap !important;
  overflow: hidden !important;
}
[data-outlook-export-area="true"] .outlook-export-disclaimer {
  flex: 0 0 48px !important;
  height: 48px !important;
  min-height: 48px !important;
  max-height: 48px !important;
  overflow: hidden !important;
}
[data-outlook-export-area="true"] .outlook-export-hide {
  display: none !important;
}
`;
const GIF_QUALITY_CONFIG: Record<GifQualityPreset, { label: string; encoderQuality: number }> = {
  small: { label: 'Small', encoderQuality: 20 },
  medium: { label: 'Medium', encoderQuality: 12 },
  large: { label: 'Large', encoderQuality: 8 },
};

// Shared styling for the control-deck selects so every dropdown is identical.
const CONTROL_SELECT_CLASS =
  'retro-select h-8 w-full bg-paper border-[2px] border-ink pl-2.5 pr-7 font-mono text-[11px] font-bold text-ink uppercase tracking-wider shadow-retro-sm cursor-pointer outline-none transition-colors hover:bg-signal-amber focus-visible:bg-signal-amber disabled:cursor-not-allowed disabled:bg-paper-dark/30 disabled:text-ink/45';

function fmtCoord(lat: number, lon: number): string {
  const ns = lat >= 0 ? 'N' : 'S';
  const ew = lon >= 0 ? 'E' : 'W';
  return `${Math.abs(lat).toFixed(1)}°${ns} ${Math.abs(lon).toFixed(1)}°${ew}`;
}

function waitForPaint(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function fmtUTC(iso: string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}${String(d.getUTCMinutes()).padStart(2, '0')}Z`;
}

function isNewerCycle(candidateISO: string | undefined, selectedISO: string | undefined): boolean {
  const candidateMs = Date.parse(candidateISO ?? '');
  const selectedMs = Date.parse(selectedISO ?? '');
  return Number.isFinite(candidateMs) && Number.isFinite(selectedMs) && candidateMs > selectedMs;
}

function fmtValidSelect(iso: string | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const day = `${String(d.getUTCMonth() + 1).padStart(2, '0')}/${String(d.getUTCDate()).padStart(2, '0')}`;
  const hr = String(d.getUTCHours()).padStart(2, '0');
  const mn = String(d.getUTCMinutes()).padStart(2, '0');
  return `${day} · ${hr}${mn}Z`;
}

function stampISO(iso: string | undefined): string {
  if (!iso) return 'unknown';
  return iso.replace(/[:.]/g, '').replace('T', '_').replace('Z', 'z');
}

function hasRiskLayerForHour(artifacts: OutlookArtifacts | null, forecastHour: number): boolean {
  if (!artifacts) return false;
  if (artifacts.riskPolygons.features.some((feature) => feature.properties.forecastHour === forecastHour)) return true;
  if (artifacts.aggregateRiskPolygons?.features.some((feature) => feature.properties.forecastHour === forecastHour)) return true;
  if (artifacts.probabilityTiles?.hours.some((hour) => hour.forecastHour === forecastHour)) return true;
  return artifacts.selectedHourStatus === 'ready';
}

function dataUrlToCanvas(dataUrl: string): Promise<HTMLCanvasElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        reject(new Error('Could not create GIF frame canvas.'));
        return;
      }
      ctx.drawImage(image, 0, 0);
      resolve(canvas);
    };
    image.onerror = () => reject(new Error('Could not decode captured GIF frame.'));
    image.src = dataUrl;
  });
}

async function captureFixedExportCanvas(element: HTMLElement): Promise<HTMLCanvasElement> {
  const dataUrl = await toPng(element, {
    backgroundColor: EXPORT_BACKGROUND_COLOR,
    cacheBust: true,
    skipFonts: true,
    pixelRatio: EXPORT_PIXEL_RATIO,
    width: EXPORT_CAPTURE_CSS_WIDTH,
    height: EXPORT_CAPTURE_CSS_HEIGHT,
    style: {
      width: `${EXPORT_CAPTURE_CSS_WIDTH}px`,
      minWidth: `${EXPORT_CAPTURE_CSS_WIDTH}px`,
      maxWidth: `${EXPORT_CAPTURE_CSS_WIDTH}px`,
      height: `${EXPORT_CAPTURE_CSS_HEIGHT}px`,
      minHeight: `${EXPORT_CAPTURE_CSS_HEIGHT}px`,
      maxHeight: `${EXPORT_CAPTURE_CSS_HEIGHT}px`,
      overflow: 'hidden',
      display: 'flex',
      flexDirection: 'column',
    },
  });
  return dataUrlToCanvas(dataUrl);
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function OutlookMapPanel({
  snapshot,
  outlookArtifacts,
  bundle,
  selectedIndex,
  isPlaying,
  onIndexChange,
  setPlaying,
  activeRegion,
  selectedMergedDate,
  setSelectedMergedDate,
  mergedDay = 1,
  setMergedDay,
  viewType,
  setViewType,
  stormReportsMode = 'none',
  setStormReportsMode,
  stormReports = [],
  availableMergedDatesOverride,
  mergedArtifactsOverride,
  spcDay1Override = null,
  spcHazardProbabilityShapesOverride = null,
  spcBacked: spcBackedProp,
  setSpcBacked: setSpcBackedProp,
  initialSpcComparisonMode = 'auto',
  staticStormReportsAvailable = false,
}: OutlookMapPanelProps) {
  const [mode, setMode] = useState<OutlookMode>('levels');
  // SPC backing toggle, shared across hourly + merged views: false = "Our Model"
  // (pure HRRR/XGBoost), true = "SPC Blend" (50/50 blend with the SPC envelope).
  // Controlled by the parent when provided, else managed locally (defaults to
  // the SPC blend, matching the previous always-blended merged product).
  const [internalSpcBacked, setInternalSpcBacked] = useState(true);
  const spcBacked = spcBackedProp ?? internalSpcBacked;
  const setSpcBacked = setSpcBackedProp ?? setInternalSpcBacked;
  const [spcComparisonMode, setSpcComparisonMode] = useState<SpcComparisonMode>(initialSpcComparisonMode);
  const [hazardLayout, setHazardLayout] = useState<'all' | 'single'>('all');
  const [selectedHazard, setSelectedHazard] = useState<'thunder' | 'hail' | 'wind' | 'tornado'>('thunder');
  const [zoomRegion, setZoomRegion] = useState<MapZoomRegion>('conus');
  const [isExporting, setIsExporting] = useState(false);
  const [isExportingGif, setIsExportingGif] = useState(false);
  const [gifDialogOpen, setGifDialogOpen] = useState(false);
  const [gifStartIndex, setGifStartIndex] = useState(0);
  const [gifEndIndex, setGifEndIndex] = useState(0);
  const [gifDelayMs, setGifDelayMs] = useState(GIF_DEFAULT_DELAY_MS);
  const [gifQuality, setGifQuality] = useState<GifQualityPreset>('medium');
  const [gifProgress, setGifProgress] = useState<GifProgressState | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const exportRef = useRef<HTMLDivElement | null>(null);

  // Fetch available merged dates for the selected outlook day (D1 or D2).
  const [availableMergedDates, setAvailableMergedDates] = useState<string[]>([]);
  useEffect(() => {
    if (availableMergedDatesOverride) {
      setAvailableMergedDates(availableMergedDatesOverride);
      if (!selectedMergedDate && availableMergedDatesOverride.length > 0) {
        setSelectedMergedDate(availableMergedDatesOverride[0]);
      }
      return undefined;
    }

    const controller = new AbortController();
    const loadDates = async () => {
      try {
        const response = await fetch(apiUrl(`/api/outlook/merged-d${mergedDay}-available-dates?region=${activeRegion}`), { signal: controller.signal });
        if (response.ok) {
          const res = await response.json();
          const dates: string[] = Array.isArray(res.dates) ? res.dates : [];
          setAvailableMergedDates(dates);
          // When the selected date is missing from the active day's list (e.g.
          // after toggling D1 <-> D2), snap to the newest available date.
          if (dates.length > 0 && !dates.includes(selectedMergedDate)) {
            setSelectedMergedDate(dates[0]);
          } else if (dates.length === 0) {
            setAvailableMergedDates([]);
          }
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        console.error('Failed to load available merge dates:', err);
      }
    };
    loadDates();
    return () => controller.abort();
  }, [activeRegion, availableMergedDatesOverride, selectedMergedDate, setSelectedMergedDate, mergedDay]);

  const liveMergedArtifacts = useMergedD1Artifacts(activeRegion, selectedMergedDate, {
    enabled: !mergedArtifactsOverride,
    day: mergedDay,
    backing: spcBacked ? 'blend' : 'pure',
  });

  // On the archive path both backing variants ("Our Model" = pure, "SPC Blend"
  // = blend) are supplied through the override. Resolve the effective merged
  // outlook from the `spcBacked` toggle so switching modes replaces the display
  // without navigating away (Requirement 6.4). When the selected mode's data is
  // unavailable the previously displayed outlook is retained and the
  // "unavailable comparison" notice is surfaced (Requirement 6.5).
  const previousBackingRef = useRef<OutlookArtifactState | null>(null);
  const backingResolution = mergedArtifactsOverride
    ? resolveBacking(spcBacked, mergedArtifactsOverride, previousBackingRef.current)
    : null;
  const backingUnavailable = backingResolution?.unavailable ?? false;
  const mergedArtifacts = mergedArtifactsOverride
    ? (backingResolution?.effective ?? UNAVAILABLE_BACKING_STATE)
    : liveMergedArtifacts;

  useEffect(() => {
    // Remember the last successfully displayed override outlook so that toggling
    // to an unavailable mode can retain it (Requirement 6.5, 6.7).
    if (mergedArtifactsOverride && !backingUnavailable && mergedArtifacts.artifacts) {
      previousBackingRef.current = mergedArtifacts;
    }
  }, [mergedArtifactsOverride, backingUnavailable, mergedArtifacts]);

  // When showing the merged Day 2 outlook, the SPC comparison overlay must show
  // the SPC Day 2 categorical (not the default Day 1). Fetch it for the selected
  // anchor date and feed it to the maps through the SPC override.
  const [mergedD2Spc, setMergedD2Spc] = useState<SpcCategoryFeatureCollection | null>(null);
  useEffect(() => {
    if (viewType !== 'merged' || mergedDay !== 2 || !selectedMergedDate || availableMergedDatesOverride) {
      setMergedD2Spc(null);
      return undefined;
    }
    const controller = new AbortController();
    fetch(apiUrl(`/api/outlook/merged-d2-spc-category?date=${selectedMergedDate}&region=${activeRegion}`), {
      signal: controller.signal,
      cache: 'no-store',
    })
      .then((response) => (response.ok ? (response.json() as Promise<SpcCategoryFeatureCollection>) : null))
      .then((payload) => setMergedD2Spc(payload))
      .catch((error) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setMergedD2Spc(null);
      });
    return () => controller.abort();
  }, [viewType, mergedDay, selectedMergedDate, activeRegion, availableMergedDatesOverride]);

  const effectiveSpcOverride = spcDay1Override ?? (mergedDay === 2 ? mergedD2Spc : null);

  // Live SPC hazard outlook (tornado/hail/wind/thunder probability shapes).
  // Fetched only on the live path; the archive path supplies its own shapes via
  // `spcHazardProbabilityShapesOverride`. On `missing`/`error` the generated
  // hazard outlook keeps rendering and the selected hazard type is preserved,
  // while a visible "unavailable" notice is surfaced (Requirement 1.6).
  const liveSpcHazard = useSpcHazardShapes(activeRegion, !mergedArtifactsOverride);
  const effectiveSpcHazardShapes = spcHazardProbabilityShapesOverride ?? liveSpcHazard.shapes;
  const liveSpcHazardUnavailable = !mergedArtifactsOverride
    && !spcHazardProbabilityShapesOverride
    && (liveSpcHazard.status === 'missing' || liveSpcHazard.status === 'error');

  // SPC-backed scrubber: apply the SPC day envelope (50/50 blend) to the
  // selected forecast hour at serve time. The SPC Day 1/Day 2 window is chosen
  // automatically from the hour's valid time by the backend.
  const spcBackedHourArtifacts = useSpcBackedHourArtifacts(
    activeRegion,
    snapshot?.forecastHour,
    viewType === 'hourly' && spcBacked,
  );

  const effectiveArtifactState = viewType === 'merged'
    ? mergedArtifacts
    : (spcBacked && spcBackedHourArtifacts.status === 'ready')
      ? spcBackedHourArtifacts
      : outlookArtifacts;

  // True when the user asked for the hourly SPC blend but the backend could not
  // apply it (no cached SPC envelope covers this hour's window), so the map is
  // really showing the unbacked model. Used to surface a small notice.
  const hourlySpcUnavailable = viewType === 'hourly'
    && spcBacked
    && spcBackedHourArtifacts.status === 'ready'
    && spcBackedHourArtifacts.artifacts?.probabilityTiles?.hours?.[0]?.tile?.spcBacking?.spcSupportApplied === false;
  const effectiveMetadata = effectiveArtifactState.artifacts?.metadata;
  const effectiveSnapshot = viewType === 'merged' && snapshot ? {
    ...snapshot,
    forecastHour: 0,
    validTimeISO: (effectiveMetadata?.spcVerification as MergedD1VerificationSummary)?.d1WindowValidISO || snapshot.validTimeISO,
  } : snapshot;

  const latestExportStateRef = useRef({ snapshot: effectiveSnapshot, outlookArtifacts: effectiveArtifactState });
  const cancelGifRef = useRef(false);
  const gifAbortRef = useRef<AbortController | null>(null);
  const forecastStops = bundle?.hours ?? [];
  const isAnyExporting = isExporting || isExportingGif;
  // Derived GIF range summary (kept order-agnostic so start/end can be either way).
  const gifFrameStart = Math.min(gifStartIndex, gifEndIndex);
  const gifFrameEnd = Math.max(gifStartIndex, gifEndIndex);
  const gifFrameCount = forecastStops.length === 0 ? 0 : gifFrameEnd - gifFrameStart + 1;
  const gifLoopSeconds = (gifFrameCount * gifDelayMs) / 1000;
  const gifStartStop = forecastStops[gifFrameStart];
  const gifEndStop = forecastStops[gifFrameEnd];
  const artifactMetadata = effectiveMetadata;
  const latestCandidate = artifactMetadata?.latestExtendedCandidate ?? undefined;
  const staleArtifacts = isNewerCycle(latestCandidate?.cycleTimeISO, artifactMetadata?.cycleTimeISO);
  const generatedHazardsReady = hasGeneratedHazardTile(effectiveArtifactState.artifacts, effectiveSnapshot?.forecastHour, effectiveArtifactState.status);
  const mlDriven = Boolean(effectiveSnapshot?.mlHazards);
  const useRuleHazardFallback = !mlDriven && effectiveArtifactState.status === 'missing';
  const engineLabel = viewType === 'merged'
    ? `Multi-Cycle Merged Day ${mergedDay} Outlook (Element-wise Maximum)`
    : mlDriven
      ? effectiveArtifactState.status === 'ready'
        ? 'Auto-generated · HRRR/XGBoost artifact pipeline'
        : 'Auto-generated · XGBoost hazard model · artifact pending'
      : 'Auto-generated · rule-based outlook engine v1';
  const hourLabel = viewType === 'merged'
    ? 'MERGED'
    : effectiveSnapshot
      ? FORECAST_HOUR_LABELS[effectiveSnapshot.forecastHour] ?? `+${effectiveSnapshot.forecastHour}h`
      : '—';
  const panelTitle = viewType === 'merged'
    ? 'Merged Automated Convective Outlook'
    : `F${String(snapshot?.forecastHour ?? 0).padStart(3, '0')}h Automated Convective Outlook`;
  const exportHourTitle = viewType === 'merged'
    ? 'Merged Outlook'
    : `F${String(snapshot?.forecastHour ?? 0).padStart(3, '0')}h`;

  const shear = effectiveSnapshot ? `${Math.round(effectiveSnapshot.ingredients.shear06Kt)} kt SHR` : '—';
  const cape = effectiveSnapshot ? `${Math.round(effectiveSnapshot.ingredients.mucape)} CAPE` : '—';

  const validTimeText = viewType === 'merged' && (effectiveMetadata?.spcVerification as MergedD1VerificationSummary)?.d1WindowValidISO
    ? `${fmtValidSelect((effectiveMetadata?.spcVerification as MergedD1VerificationSummary).d1WindowValidISO)} – ${fmtValidSelect((effectiveMetadata?.spcVerification as MergedD1VerificationSummary).d1WindowExpireISO)}`
    : fmtUTC(effectiveSnapshot?.validTimeISO);
  const mergedSummaryMeta = effectiveMetadata?.spcVerification as MergedD1VerificationSummary | undefined;
  // Export header (metabar) values, made merged-aware so the cycle / valid
  // window / generated time are populated correctly in the exported image.
  const exportCycleLabel = viewType === 'merged' ? 'Merged cycle' : 'HRRR cycle';
  const exportCycleText = viewType === 'merged'
    ? (Array.from(new Set(mergedSummaryMeta?.mergedCycles ?? [])).join(', ') || (mergedSummaryMeta?.mergedCycles?.[0] ?? 'Merged Outlook'))
    : fmtUTC(artifactMetadata?.cycleTimeISO);
  const exportValidText = validTimeText;
  const exportGeneratedText = viewType === 'merged'
    ? fmtUTC(mergedSummaryMeta?.generatedAtISO ?? artifactMetadata?.generatedAtISO)
    : fmtUTC(artifactMetadata?.generatedAtISO);
  const latestAvailableReportDate = latestAvailableSpcReportDate();
  const reportsPendingForSelectedDate = !staticStormReportsAvailable
    && viewType === 'merged'
    && Boolean(selectedMergedDate)
    && selectedMergedDate > latestAvailableReportDate;
  const mapStormReportsMode: StormReportsMode = viewType === 'merged' && !reportsPendingForSelectedDate
    ? stormReportsMode
    : 'none';
  const mapStormReports = mapStormReportsMode === 'none' ? [] : stormReports;
  const reportStatusLabel = reportsPendingForSelectedDate
    ? `Pending · SPC thru ${fmtShortDate(latestAvailableReportDate)}`
    : stormReportsMode !== 'none' && stormReports.length === 0
      ? `No reports · SPC thru ${fmtShortDate(latestAvailableReportDate)}`
      : `SPC thru ${fmtShortDate(latestAvailableReportDate)}`;
  const mergedCycleLabel = (effectiveMetadata?.spcVerification as MergedD1VerificationSummary)?.mergedCycles?.[0] ?? 'Merged Outlook';

  const timeRows = [
    ['Cycle', viewType === 'merged' ? mergedCycleLabel : (artifactMetadata?.cycle ?? fmtUTC(artifactMetadata?.cycleTimeISO))],
    ['Forecast valid', validTimeText],
    ['Artifact generated', fmtUTC(artifactMetadata?.generatedAtISO)],
  ] as const;

  useEffect(() => {
    latestExportStateRef.current = { snapshot: effectiveSnapshot, outlookArtifacts: effectiveArtifactState };
  }, [effectiveSnapshot, effectiveArtifactState]);


  useEffect(() => {
    if (forecastStops.length === 0) {
      setGifStartIndex(0);
      setGifEndIndex(0);
      return;
    }
    setGifStartIndex((index) => Math.max(0, Math.min(index, forecastStops.length - 1)));
    setGifEndIndex((index) => Math.max(0, Math.min(index, forecastStops.length - 1)));
  }, [forecastStops.length]);

  // GIF animation only applies to the hourly scrubber — close the dialog if the
  // user switches to the merged outlook while it is open.
  useEffect(() => {
    if (viewType === 'merged' && gifDialogOpen) {
      setGifDialogOpen(false);
    }
  }, [viewType, gifDialogOpen]);

  const openGifDialog = () => {
    if (!snapshot || forecastStops.length === 0 || isAnyExporting || viewType === 'merged') return;
    const safeIndex = Math.max(0, Math.min(selectedIndex, forecastStops.length - 1));
    setGifStartIndex(safeIndex);
    setGifEndIndex(Math.min(forecastStops.length - 1, safeIndex + 6));
    setGifDialogOpen(true);
    setExportError(null);
  };

  const cancelGifExport = () => {
    cancelGifRef.current = true;
    gifAbortRef.current?.abort();
  };

  const isFrameReadyForCapture = (target: HourSnapshot, captureMode: OutlookMode): boolean => {
    const { snapshot: latestSnapshot, outlookArtifacts: latestArtifacts } = latestExportStateRef.current;
    if (latestSnapshot?.forecastHour !== target.forecastHour || latestSnapshot.validTimeISO !== target.validTimeISO) {
      return false;
    }
    if (captureMode === 'hazards') {
      if (hasGeneratedHazardTile(latestArtifacts.artifacts, target.forecastHour, latestArtifacts.status)) return true;
      return latestArtifacts.status === 'missing' || latestArtifacts.status === 'failed' || latestArtifacts.status === 'error';
    }
    if (hasRiskLayerForHour(latestArtifacts.artifacts, target.forecastHour)) return true;
    return latestArtifacts.status === 'missing' || latestArtifacts.status === 'failed' || latestArtifacts.status === 'error';
  };

  const waitForFrameReady = async (target: HourSnapshot, captureMode: OutlookMode): Promise<boolean> => {
    const start = window.performance.now();
    while (window.performance.now() - start < GIF_CAPTURE_TIMEOUT_MS) {
      if (cancelGifRef.current) throw new Error('GIF export cancelled.');
      if (isFrameReadyForCapture(target, captureMode)) return true;
      await wait(GIF_CAPTURE_POLL_MS);
    }
    return false;
  };

  const saveCurrentMap = async () => {
    if (!snapshot || !exportRef.current || isAnyExporting) return;
    setIsExporting(true);
    setExportError(null);
    try {
      await waitForPaint();
      if (!exportRef.current) return;
      const canvas = await captureFixedExportCanvas(exportRef.current);
      const dataUrl = canvas.toDataURL('image/png');
      const validStamp = stampISO(snapshot.validTimeISO);
      const filename = `autooutlook_${mode === 'levels' ? spcComparisonMode : mode}_F${String(snapshot.forecastHour).padStart(3, '0')}_${validStamp}.png`;
      const link = document.createElement('a');
      link.href = dataUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setExportError(`Export failed: ${message}`);
    } finally {
      setIsExporting(false);
    }
  };

  const saveCurrentMapAsGif = async () => {
    if (!bundle || !snapshot || !exportRef.current || forecastStops.length === 0 || isAnyExporting) return;
    const startIndex = Math.min(gifStartIndex, gifEndIndex);
    const endIndex = Math.max(gifStartIndex, gifEndIndex);
    const frameStops = forecastStops.slice(startIndex, endIndex + 1);
    if (frameStops.length === 0) return;

    const originalIndex = selectedIndex;
    const originalPlaying = isPlaying;
    const captureMode = mode;
    const qualityConfig = GIF_QUALITY_CONFIG[gifQuality];
    const frames: HTMLCanvasElement[] = [];
    let frameWidth = 0;
    let frameHeight = 0;
    let timedOutFrames = 0;

    cancelGifRef.current = false;
    setGifDialogOpen(false);
    setIsExportingGif(true);
    setExportError(null);
    setGifProgress({ current: 0, total: frameStops.length, phase: 'capturing' });
    setPlaying(false);

    try {
      await waitForPaint();
      if (!exportRef.current) throw new Error('Map export area is unavailable.');

      for (let offset = 0; offset < frameStops.length; offset += 1) {
        if (cancelGifRef.current) throw new Error('GIF export cancelled.');
        const target = frameStops[offset];
        onIndexChange(startIndex + offset);
        const ready = await waitForFrameReady(target, captureMode);
        if (!ready) timedOutFrames += 1;
        await waitForPaint();
        await wait(50);
        if (!exportRef.current) throw new Error('Map export area is unavailable.');
        const canvas = await captureFixedExportCanvas(exportRef.current);
        if (frames.length === 0) {
          frameWidth = canvas.width;
          frameHeight = canvas.height;
        }
        frames.push(canvas);
        setGifProgress({ current: offset + 1, total: frameStops.length, phase: 'capturing' });
      }

      if (cancelGifRef.current) throw new Error('GIF export cancelled.');
      const abortController = new AbortController();
      gifAbortRef.current = abortController;
      setGifProgress({ current: 0, total: 100, phase: 'encoding' });
      const blob = await recordCanvasesToGif(frames, {
        width: frameWidth,
        height: frameHeight,
        delayMs: gifDelayMs,
        quality: qualityConfig.encoderQuality,
        signal: abortController.signal,
        onProgress: (progress) => {
          setGifProgress({ current: Math.round(progress * 100), total: 100, phase: 'encoding' });
        },
      });
      if (cancelGifRef.current) throw new Error('GIF export cancelled.');
      const startStop = frameStops[0];
      const endStop = frameStops[frameStops.length - 1];
      const filename = `autooutlook_${captureMode}_F${String(startStop.forecastHour).padStart(3, '0')}-F${String(endStop.forecastHour).padStart(3, '0')}_${stampISO(startStop.validTimeISO)}.gif`;
      downloadBlob(blob, filename);
      if (timedOutFrames > 0) {
        setExportError(`${timedOutFrames} GIF frame${timedOutFrames === 1 ? '' : 's'} captured before generated artifacts finished loading.`);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (message !== 'GIF export cancelled.') {
        setExportError(`GIF export failed: ${message}`);
      }
    } finally {
      gifAbortRef.current = null;
      cancelGifRef.current = false;
      setIsExportingGif(false);
      setGifProgress(null);
      onIndexChange(originalIndex);
      setPlaying(originalPlaying);
    }
  };

  return (
    <RetroPanel
      title={panelTitle}
      eyebrow="01 / automated categorical + hazard outlook · auto-detected focus region"
      badge={<RetroBadge tone="paper">FCST · {hourLabel}</RetroBadge>}
      size="sm"
      className="[&>div]:p-2"
    >


      <div className="mb-2 grid grid-cols-1 gap-2 lg:grid-cols-[1fr_auto]">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {timeRows.map(([label, value]) => (
            <div key={label} className="border-[2px] border-ink bg-paper px-2 py-1.5 shadow-retro-sm">
              <div className="font-mono text-[8px] font-bold uppercase tracking-[0.24em] text-ink/55">{label}</div>
              <div className="mt-0.5 font-mono text-[11px] font-bold uppercase tracking-wider text-ink">{value}</div>
            </div>
          ))}
        </div>
        <div
          className={[
            'border-[2px] border-ink px-2 py-1.5 font-mono text-[10px] font-bold uppercase tracking-widest shadow-retro-sm',
            staleArtifacts ? 'bg-signal-amber text-ink' : 'bg-paper text-ink/65',
          ].join(' ')}
        >
          {staleArtifacts
            ? `Artifact lag: latest ${latestCandidate?.label ?? 'extended HRRR'}`
            : `Cycle policy: ${artifactMetadata?.cyclePolicy?.name ?? '—'}`}
        </div>
      </div>

      <div
        ref={exportRef}
        className="bg-paper"
        data-testid="outlook-export-area"
        data-outlook-export-area={isAnyExporting ? 'true' : undefined}
        style={
          isAnyExporting
            ? {
                width: EXPORT_CAPTURE_CSS_WIDTH,
                minWidth: EXPORT_CAPTURE_CSS_WIDTH,
                maxWidth: EXPORT_CAPTURE_CSS_WIDTH,
                height: EXPORT_CAPTURE_CSS_HEIGHT,
                minHeight: EXPORT_CAPTURE_CSS_HEIGHT,
                maxHeight: EXPORT_CAPTURE_CSS_HEIGHT,
                overflow: 'hidden',
              }
            : undefined
        }
      >
        {isAnyExporting && <style>{EXPORT_FIXED_LAYOUT_CSS}</style>}
        <div
          className={[
            'outlook-export-topbar flex-wrap items-center justify-between gap-3 border-[3px] border-b-0 border-ink bg-paper px-3 py-2',
            isAnyExporting ? 'flex' : 'hidden',
          ].join(' ')}
        >
          <div className="w-[320px] shrink-0 overflow-hidden border-[3px] border-ink bg-paper px-3 py-2 shadow-retro-sm">
            <div
              className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap font-display text-[18px] font-extrabold uppercase leading-none tracking-normal text-ink"
              title="AutoOutlook"
            >
              AUTO<span className="text-signal-amber">OUTLOOK</span>
            </div>
            <div className="mt-1 max-w-full overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[9px] font-bold uppercase tracking-[0.2em] text-ink/65">
              autooutlook.tech
            </div>
          </div>
          <div className="min-w-[280px] flex-1 text-center">
            <div className="font-mono text-[9px] font-bold uppercase tracking-[0.32em] text-ink/55">
              Automated Convective Risk Intelligence
            </div>
            <div className="mt-1 font-display text-[18px] font-extrabold uppercase tracking-wide text-ink">
              {exportHourTitle} {mode === 'levels' ? 'Risk Levels' : 'Hazard Probabilities'}
            </div>
          </div>
          <div className="border-[2px] border-ink bg-ink px-2.5 py-1.5 font-mono text-[10px] font-bold uppercase tracking-widest text-paper shadow-retro-sm">
            {hourLabel}
          </div>
        </div>
        {/* Header strip — mimics the rawinsonde valid/init header */}
        <div
          className={[
            'outlook-export-metabar flex-wrap items-center justify-between gap-x-4 gap-y-1 border-[3px] border-b-0 border-ink bg-ink text-paper px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest',
            isAnyExporting ? 'flex' : 'hidden',
          ].join(' ')}
        >
          <span className="shrink-0">{exportCycleLabel}: {exportCycleText}</span>
          <span className="shrink-0">Forecast valid: {exportValidText}</span>
          <span className="shrink-0">Generated: {exportGeneratedText}</span>
          <span className="min-w-[220px] flex-1 text-center leading-snug text-paper/80">
            {snapshot ? focusLocationFromSnapshot(snapshot).label : 'AWAITING REGION DETECTION…'}
          </span>
          <span className="text-paper/80">{cape}</span>
          <span className="text-paper/80">{shear}</span>
          <span className="shrink-0">{effectiveSnapshot ? fmtCoord(effectiveSnapshot.region.centerLat, effectiveSnapshot.region.centerLon) : '—'}</span>
        </div>

        {mode === 'levels' ? (
          <div className="outlook-export-stage border-[3px] border-ink bg-paper p-2">
            <GeneratedOutlookMap
              snapshot={effectiveSnapshot}
              status={effectiveArtifactState.status}
              artifacts={effectiveArtifactState.artifacts}
              message={effectiveArtifactState.message}
              activeRegion={activeRegion}
              comparisonMode={spcComparisonMode}
              stormReportsMode={mapStormReportsMode}
              stormReports={mapStormReports}
              spcDay1Override={effectiveSpcOverride}
              zoomRegion={zoomRegion}
            />
          </div>
        ) : (
          <>
          {liveSpcHazardUnavailable && (
            <div
              className="outlook-export-hide mb-2 border-[3px] border-ink bg-signal-amber px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-widest text-ink shadow-retro-sm"
              role="status"
            >
              SPC hazard outlook unavailable
            </div>
          )}
          <div
            className={[
              'outlook-export-stage',
              hazardLayout === 'all'
                ? 'outlook-export-hazard-grid grid grid-cols-1 md:grid-cols-2 gap-2'
                : 'flex flex-col',
              'border-[3px] border-ink bg-paper p-2',
            ].join(' ')}
          >
            {generatedHazardsReady ? (
              hazardLayout === 'all' ? (
                <>
                  <GeneratedHazardProbabilityMap
                    snapshot={effectiveSnapshot}
                    hazard="thunder"
                    title="Thunderstorm Outlook"
                    artifacts={effectiveArtifactState.artifacts}
                    zoomRegion={zoomRegion}
                    status={effectiveArtifactState.status}
                    activeRegion={activeRegion}
                    stormReportsMode={mapStormReportsMode}
                    stormReports={mapStormReports}
                    comparisonMode={spcComparisonMode}
                    spcHazardProbabilityShapes={effectiveSpcHazardShapes}
                    cigOverlayEnabled={viewType === 'merged'}
                  />
                  <GeneratedHazardProbabilityMap
                    snapshot={effectiveSnapshot}
                    hazard="hail"
                    title="Hail Outlook"
                    artifacts={effectiveArtifactState.artifacts}
                    zoomRegion={zoomRegion}
                    status={effectiveArtifactState.status}
                    activeRegion={activeRegion}
                    stormReportsMode={mapStormReportsMode}
                    stormReports={mapStormReports}
                    comparisonMode={spcComparisonMode}
                    spcHazardProbabilityShapes={effectiveSpcHazardShapes}
                    cigOverlayEnabled={viewType === 'merged'}
                  />
                  <GeneratedHazardProbabilityMap
                    snapshot={effectiveSnapshot}
                    hazard="wind"
                    title="Damaging Wind Outlook"
                    artifacts={effectiveArtifactState.artifacts}
                    zoomRegion={zoomRegion}
                    status={effectiveArtifactState.status}
                    activeRegion={activeRegion}
                    stormReportsMode={mapStormReportsMode}
                    stormReports={mapStormReports}
                    comparisonMode={spcComparisonMode}
                    spcHazardProbabilityShapes={effectiveSpcHazardShapes}
                    cigOverlayEnabled={viewType === 'merged'}
                  />
                  <GeneratedHazardProbabilityMap
                    snapshot={effectiveSnapshot}
                    hazard="tornado"
                    title="Tornado Outlook"
                    artifacts={effectiveArtifactState.artifacts}
                    zoomRegion={zoomRegion}
                    status={effectiveArtifactState.status}
                    activeRegion={activeRegion}
                    stormReportsMode={mapStormReportsMode}
                    stormReports={mapStormReports}
                    comparisonMode={spcComparisonMode}
                    spcHazardProbabilityShapes={effectiveSpcHazardShapes}
                    cigOverlayEnabled={viewType === 'merged'}
                  />
                </>
              ) : (
                <GeneratedHazardProbabilityMap
                  snapshot={effectiveSnapshot}
                  hazard={selectedHazard}
                  title={
                    selectedHazard === 'thunder'
                      ? 'Thunderstorm Outlook'
                      : selectedHazard === 'hail'
                        ? 'Hail Outlook'
                        : selectedHazard === 'wind'
                          ? 'Damaging Wind Outlook'
                          : 'Tornado Outlook'
                  }
                  artifacts={effectiveArtifactState.artifacts}
                  status={effectiveArtifactState.status}
                  activeRegion={activeRegion}
                  stormReportsMode={mapStormReportsMode}
                  stormReports={mapStormReports}
                  comparisonMode={spcComparisonMode}
                  spcHazardProbabilityShapes={effectiveSpcHazardShapes}
                  cigOverlayEnabled={viewType === 'merged'}
                  zoomRegion={zoomRegion}
                />
              )
            ) : useRuleHazardFallback ? (
              hazardLayout === 'all' ? (
                <>
                  <HazardOutlookMap
                    snapshot={effectiveSnapshot}
                    hazard="thunder"
                    title="Thunderstorm Outlook"
                    sourceLabel="Rule fallback"
                    zoomRegion={zoomRegion}
                    activeRegion={activeRegion}
                    stormReportsMode={mapStormReportsMode}
                    stormReports={mapStormReports}
                  />
                  <HazardOutlookMap
                    snapshot={effectiveSnapshot}
                    hazard="hail"
                    title="Hail Outlook"
                    sourceLabel="Rule fallback"
                    zoomRegion={zoomRegion}
                    activeRegion={activeRegion}
                    stormReportsMode={mapStormReportsMode}
                    stormReports={mapStormReports}
                  />
                  <HazardOutlookMap
                    snapshot={effectiveSnapshot}
                    hazard="wind"
                    title="Damaging Wind Outlook"
                    sourceLabel="Rule fallback"
                    zoomRegion={zoomRegion}
                    activeRegion={activeRegion}
                    stormReportsMode={mapStormReportsMode}
                    stormReports={mapStormReports}
                  />
                  <HazardOutlookMap
                    snapshot={effectiveSnapshot}
                    hazard="tornado"
                    title="Tornado Outlook"
                    sourceLabel="Rule fallback"
                    zoomRegion={zoomRegion}
                    activeRegion={activeRegion}
                    stormReportsMode={mapStormReportsMode}
                    stormReports={mapStormReports}
                  />
                </>
              ) : (
                <HazardOutlookMap
                  snapshot={effectiveSnapshot}
                  hazard={selectedHazard}
                  title={
                    selectedHazard === 'thunder'
                      ? 'Thunderstorm Outlook'
                      : selectedHazard === 'hail'
                        ? 'Hail Outlook'
                        : selectedHazard === 'wind'
                          ? 'Damaging Wind Outlook'
                          : 'Tornado Outlook'
                  }
                  sourceLabel="Rule fallback"
                  activeRegion={activeRegion}
                  stormReportsMode={mapStormReportsMode}
                  stormReports={mapStormReports}
                  zoomRegion={zoomRegion}
                />
              )
            ) : (
              <GeneratedHazardsUnavailable message={effectiveArtifactState.message} status={effectiveArtifactState.status} />
            )}
          </div>
          </>
        )}

        {/* Footer strip */}
        <div className="outlook-export-footer border-[3px] border-t-0 border-ink bg-paper px-3 py-1.5 flex items-center justify-between gap-3 flex-wrap font-mono text-[10px] uppercase tracking-widest text-ink/70">
          <span>States in focus: {effectiveSnapshot?.region.states.join(' · ') ?? '—'}</span>
          <span>{engineLabel}</span>
        </div>


        <div className="outlook-export-disclaimer border-[3px] border-t-0 border-ink bg-ink px-3 py-2 text-paper">
          <ForecastDisclaimer variant="export" />
        </div>
      </div>

      {/* Unified Control Bar Row (Moved below the map) */}
      <div className="mt-2 border-[3px] border-ink bg-paper shadow-retro-sm flex flex-col animate-fadeIn">
        <div className="flex flex-nowrap items-start gap-x-2 gap-y-3 p-3 overflow-x-auto [&>*]:shrink-0">
          {/* Forecast type */}
          <ControlField label="Type">
            <SegmentedControl
              ariaLabel="Forecast type"
              value={mode}
              disabled={isAnyExporting}
              onChange={(value) => setMode(value)}
              options={[
                { value: 'levels', label: 'Risk Levels' },
                { value: 'hazards', label: 'Hazard Probs' },
              ]}
            />
          </ControlField>

          {/* View mode */}
          <ControlField label="View">
            <SegmentedControl
              ariaLabel="View mode"
              value={viewType}
              disabled={isAnyExporting}
              onChange={(value) => setViewType(value)}
              options={[
                { value: 'hourly', label: 'Hourly' },
                { value: 'merged', label: 'Merged' },
              ]}
            />
          </ControlField>

          {/* Map zoom region */}
          <ControlField label="Zoom Region" className="w-32">
            <select
              value={zoomRegion}
              onChange={(e) => setZoomRegion(e.target.value as MapZoomRegion)}
              disabled={isAnyExporting}
              title="Choose which part of CONUS to zoom the map into"
              className={CONTROL_SELECT_CLASS}
            >
              {MAP_ZOOM_REGION_ORDER.map((region) => (
                <option key={region} value={region}>
                  {MAP_ZOOM_REGIONS[region].label}
                </option>
              ))}
            </select>
          </ControlField>

          <ControlDivider />

          {/* SPC backing: pure "Our Model" vs the 50/50 SPC blend (both views) */}
          <ControlField label="SPC Backing" className="animate-fadeIn">
            <SegmentedControl
              ariaLabel="SPC backing"
              value={spcBacked ? 'spc' : 'hrrr'}
              disabled={isAnyExporting}
              onChange={(value) => setSpcBacked(value === 'spc')}
              options={[
                { value: 'hrrr', label: 'Our Model', title: 'Pure HRRR/XGBoost outlook (no SPC backing)' },
                { value: 'spc', label: 'SPC Blend', title: '50/50 blend of our model with the official SPC outlook' },
              ]}
            />
            {hourlySpcUnavailable && (
              <p className="mt-1 max-w-[150px] text-[9px] font-bold uppercase tracking-wider leading-tight text-signal-red" role="status">
                No SPC for this hour — showing unblended model.
              </p>
            )}
            {backingUnavailable && viewType === 'merged' && (
              <p className="mt-1 max-w-[150px] text-[9px] font-bold uppercase tracking-wider leading-tight text-signal-red" role="status">
                {spcBacked ? 'SPC Blend' : 'Our Model'} comparison unavailable for this event.
              </p>
            )}
          </ControlField>

          {/* Outlook day (D1 / D2) */}
          {viewType === 'merged' && setMergedDay && (
            <ControlField label="Outlook Day" className="animate-fadeIn">
              <SegmentedControl
                ariaLabel="Outlook day"
                value={`d${mergedDay}` as 'd1' | 'd2'}
                disabled={isAnyExporting}
                onChange={(value) => {
                  const day = value === 'd1' ? 1 : 2;
                  if (day === mergedDay) return;
                  // Reset the date so the new day's available list picks its newest.
                  setSelectedMergedDate('');
                  setMergedDay(day);
                }}
                options={[
                  { value: 'd1', label: 'D1', title: 'Day 1 convective outlook (12Z–12Z today)' },
                  { value: 'd2', label: 'D2', title: 'Day 2 convective outlook (12Z tomorrow – 12Z next day, from the 12Z cycle F24–F48)' },
                ]}
              />
            </ControlField>
          )}

          {/* Anchor date (merged) */}
          {viewType === 'merged' && availableMergedDates.length > 0 && (
            <ControlField label="Anchor Date" className="w-32 animate-fadeIn">
              <select
                value={selectedMergedDate}
                onChange={(e) => setSelectedMergedDate(e.target.value)}
                disabled={isAnyExporting}
                className={CONTROL_SELECT_CLASS}
              >
                {availableMergedDates.map((dateStr) => {
                  const d = new Date(dateStr + 'T12:00:00Z');
                  const formatted = d.toLocaleDateString('en-US', {
                    month: 'short',
                    day: '2-digit',
                    year: 'numeric',
                    timeZone: 'UTC',
                  });
                  return (
                    <option key={dateStr} value={dateStr}>
                      {formatted}
                    </option>
                  );
                })}
              </select>
            </ControlField>
          )}

          {/* Verified reports (merged) */}
          {viewType === 'merged' && setStormReportsMode && (
            <ControlField label="Verified Reports" className="w-36 animate-fadeIn">
              <select
                value={reportsPendingForSelectedDate ? 'none' : stormReportsMode}
                onChange={(e) => setStormReportsMode(e.target.value as StormReportsMode)}
                disabled={isAnyExporting || reportsPendingForSelectedDate}
                className={CONTROL_SELECT_CLASS}
              >
                <option value="none">No Reports</option>
                <option value="all">All Reports</option>
                <option value="tornado">Tornadoes</option>
                <option value="hail">Hail</option>
                <option value="wind">Wind</option>
              </select>
              <span className={[
                'truncate font-mono text-[8px] font-bold uppercase tracking-wider',
                reportsPendingForSelectedDate ? 'text-signal-red' : 'text-ink/45',
              ].join(' ')}>
                {reportStatusLabel}
              </span>
            </ControlField>
          )}

          {/* Hazard view (hazard mode) */}
          {mode === 'hazards' && (
            <ControlField label="Hazard" className="w-36 animate-fadeIn">
              <select
                value={hazardLayout === 'all' ? 'all' : selectedHazard}
                onChange={(e) => {
                  const val = e.target.value;
                  if (val === 'all') {
                    setHazardLayout('all');
                  } else {
                    setHazardLayout('single');
                    setSelectedHazard(val as 'thunder' | 'hail' | 'wind' | 'tornado');
                  }
                }}
                disabled={isAnyExporting}
                className={CONTROL_SELECT_CLASS}
              >
                <option value="all">All 4 Grid</option>
                <option value="thunder">Thunderstorm</option>
                <option value="hail">Hail</option>
                <option value="wind">Damaging Wind</option>
                <option value="tornado">Tornado</option>
              </select>
            </ControlField>
          )}

          {(mode === 'levels' || (mode === 'hazards' && effectiveSpcHazardShapes?.features?.length)) && (
            <ControlField label="SPC Compare" className="w-40 animate-fadeIn" >
              <select
                value={spcComparisonMode}
                onChange={(e) => setSpcComparisonMode(e.target.value as SpcComparisonMode)}
                disabled={isAnyExporting}
                data-spc-comparison-control="true"
                className={CONTROL_SELECT_CLASS}
              >
                <option value="auto">AutoOutlook Only</option>
                <option value="spc">{viewType === 'merged' ? `SPC Day ${mergedDay} Only` : 'SPC Day 1 Only'}</option>
                <option value="overlay">Overlay Compare</option>
              </select>
            </ControlField>
          )}

          {/* Export — pinned to the far right */}
          <div className="ml-auto flex items-start gap-2">
            <ControlDivider />
            <ControlField label="Export" className="w-36">
              <select
                value=""
                onChange={(e) => {
                  const val = e.target.value;
                  if (val === 'png') {
                    saveCurrentMap();
                  } else if (val === 'gif') {
                    openGifDialog();
                  }
                  e.target.value = ""; // Reset
                }}
                disabled={!snapshot || isAnyExporting}
                className={CONTROL_SELECT_CLASS}
              >
                <option value="" disabled hidden>Choose Export...</option>
                <option value="png">Save PNG Image</option>
                {viewType !== 'merged' && (
                  <option value="gif" disabled={forecastStops.length === 0}>Save GIF Animation</option>
                )}
              </select>
            </ControlField>
          </div>
        </div>

        {/* Exporter Dialogs / Messages (cleanly separated inside the container) */}
        {gifProgress && (
          <div className="border-t-[3px] border-ink flex flex-wrap items-center justify-between gap-2 bg-signal-amber px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-widest text-ink">
            <span>
              {gifProgress.phase === 'capturing'
                ? `GIF capture ${gifProgress.current}/${gifProgress.total}`
                : `GIF encode ${gifProgress.current}%`}
            </span>
            <button
              type="button"
              onClick={cancelGifExport}
              className="retro-button bg-paper px-2 py-1 text-[10px] leading-none text-ink hover:bg-signal-red hover:text-paper"
            >
              Cancel
            </button>
          </div>
        )}
        {gifDialogOpen && (
          <div className="border-t-[3px] border-ink bg-paper p-4">
            <div className="mb-3 flex items-center justify-between border-b-[2px] border-ink pb-2">
              <div className="flex items-center gap-2">
                <span className="border-[2px] border-ink bg-ink px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-widest text-signal-lime">
                  GIF
                </span>
                <span className="font-mono text-[10px] font-bold uppercase tracking-[0.28em] text-ink/65">
                  Animated Export · {mode === 'levels' ? 'Risk Levels' : 'Hazard Probabilities'}
                </span>
              </div>
              <button
                type="button"
                onClick={() => setGifDialogOpen(false)}
                aria-label="Close GIF export"
                className="border-[2px] border-ink bg-paper hover:bg-signal-red hover:text-paper px-1.5 py-0.5 font-mono text-[9px] font-bold leading-none shadow-retro-sm transition-colors cursor-pointer select-none"
              >
                ✕
              </button>
            </div>

            {/* Frame range */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <ControlField label="Start Frame (valid)">
                <select
                  value={gifStartIndex}
                  onChange={(event) => {
                    const next = Number(event.target.value);
                    setGifStartIndex(next);
                    setGifEndIndex((index) => Math.max(index, next));
                  }}
                  className={CONTROL_SELECT_CLASS}
                >
                  {forecastStops.map((stop, index) => (
                    <option key={`gif-start-${stop.forecastHour}-${stop.validTimeISO}`} value={index}>
                      {fmtValidSelect(stop.validTimeISO)} · F{String(stop.forecastHour).padStart(3, '0')}
                    </option>
                  ))}
                </select>
              </ControlField>
              <ControlField label="End Frame (valid)">
                <select
                  value={gifEndIndex}
                  onChange={(event) => setGifEndIndex(Number(event.target.value))}
                  className={CONTROL_SELECT_CLASS}
                >
                  {forecastStops.map((stop, index) => (
                    <option key={`gif-end-${stop.forecastHour}-${stop.validTimeISO}`} value={index} disabled={index < gifStartIndex}>
                      {fmtValidSelect(stop.validTimeISO)} · F{String(stop.forecastHour).padStart(3, '0')}
                    </option>
                  ))}
                </select>
              </ControlField>
            </div>

            {/* Playback + quality as segmented switches */}
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <ControlField label="Frame Delay">
                <SegmentedControl
                  ariaLabel="Frame delay"
                  fill
                  value={String(gifDelayMs)}
                  onChange={(value) => setGifDelayMs(Number(value))}
                  options={GIF_DELAY_OPTIONS.map((delayMs) => ({
                    value: String(delayMs),
                    label: `${delayMs}`,
                    title: `${delayMs} ms between frames`,
                  }))}
                />
              </ControlField>
              <ControlField label="Quality / File Size">
                <SegmentedControl
                  ariaLabel="Quality"
                  fill
                  value={gifQuality}
                  onChange={(value) => setGifQuality(value)}
                  options={(Object.keys(GIF_QUALITY_CONFIG) as GifQualityPreset[]).map((quality) => ({
                    value: quality,
                    label: GIF_QUALITY_CONFIG[quality].label,
                  }))}
                />
              </ControlField>
            </div>

            {/* Live summary + actions on one row */}
            <div className="mt-3 flex flex-wrap items-stretch gap-3">
              <div className="flex flex-1 flex-wrap items-center gap-x-5 gap-y-1 border-[2px] border-ink bg-paper-dark/20 px-3 py-2 font-mono text-[10px] font-bold uppercase tracking-wider text-ink/70">
                <span>Frames <span className="text-ink">{gifFrameCount}</span></span>
                <span>Loop <span className="text-ink">{gifLoopSeconds.toFixed(1)}s</span></span>
                <span className="hidden sm:inline">Delay <span className="text-ink">{gifDelayMs}ms</span></span>
                {gifStartStop && gifEndStop && (
                  <span className="ml-auto normal-case tracking-normal text-ink/55">
                    F{String(gifStartStop.forecastHour).padStart(3, '0')} → F{String(gifEndStop.forecastHour).padStart(3, '0')}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setGifDialogOpen(false)}
                  className="retro-button bg-paper px-3 py-1.5 text-[12px] leading-none text-ink hover:bg-paper"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={saveCurrentMapAsGif}
                  disabled={gifFrameCount === 0}
                  className="retro-button bg-signal-amber px-3 py-1.5 text-[12px] leading-none text-ink hover:bg-signal-amber disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Generate GIF · {gifFrameCount} {gifFrameCount === 1 ? 'frame' : 'frames'}
                </button>
              </div>
            </div>
          </div>
        )}
        {exportError && (
          <div className="border-t-[3px] border-ink bg-paper px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-widest text-signal-red">
            {exportError}
          </div>
        )}
      </div>
    </RetroPanel>
  );
}

function GeneratedHazardsUnavailable({ message, status }: { message: string | null; status: string }) {
  const isFetchingHour = status === 'loading' || status === 'pending';
  return (
    <div className="outlook-export-unavailable md:col-span-2 border-[3px] border-ink bg-paper min-h-[260px] flex items-center justify-center p-4 shadow-retro">
      <div className="max-w-[520px] border-[3px] border-ink bg-paper p-4 shadow-retro-sm">
        <div className="font-display text-[14px] font-extrabold uppercase tracking-wider">
          {isFetchingHour ? 'Forecast hour unavailable' : 'Generated hazard tiles unavailable'}
        </div>
        <p className="mt-2 font-mono text-[11px] leading-relaxed text-ink/70">
          {status === 'loading'
            ? 'Selected forecast hour is still fetching generated hazard tiles.'
            : status === 'pending'
              ? message ?? 'Selected forecast hour is still generating.'
            : message ?? 'Selected forecast hour does not have a generated HRRR/XGBoost probability tile yet.'}
        </p>
      </div>
    </div>
  );
}

function latestAvailableSpcReportDate(now = new Date()): string {
  const utcMidnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  // SPC daily storm reports use a 1200Z-to-1159Z report day across the US.
  const reportDayStart = now.getUTCHours() >= 12 ? utcMidnight : utcMidnight - 24 * 60 * 60 * 1000;
  return new Date(reportDayStart).toISOString().slice(0, 10);
}

function fmtShortDate(dateStr: string): string {
  const date = new Date(`${dateStr}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return dateStr;
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: '2-digit',
    timeZone: 'UTC',
  });
}

function ModeButton({
  active,
  children,
  onClick,
  disabled = false,
}: {
  active: boolean;
  children: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={[
        'retro-button min-h-8 px-3 py-1.5 text-[12px] leading-none',
        disabled ? 'cursor-not-allowed opacity-50' : '',
        active
          ? 'bg-signal-amber text-ink translate-x-[2px] translate-y-[2px] shadow-[1px_1px_0_0_#111111] hover:bg-signal-amber hover:text-ink'
          : 'bg-paper text-ink hover:bg-signal-amber hover:text-ink',
      ].join(' ')}
    >
      {children}
    </button>
  );
}

function SubModeButton({
  active,
  children,
  onClick,
  disabled = false,
}: {
  active: boolean;
  children: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={[
        'retro-button min-h-7 px-2.5 py-1 text-[11px] leading-none transition-all',
        disabled ? 'cursor-not-allowed opacity-50' : '',
        active
          ? 'bg-ink text-paper translate-x-[1.5px] translate-y-[1.5px] shadow-[0.5px_0.5px_0_0_#111111] hover:bg-ink hover:text-paper font-bold'
          : 'bg-paper text-ink hover:bg-ink hover:text-paper',
      ].join(' ')}
    >
      {children}
    </button>
  );
}


// A labeled control cell: a tiny uppercase caption stacked above its control.
function ControlField({
  label,
  children,
  className = '',
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={['flex min-w-0 flex-col gap-1', className].join(' ')}>
      <span className="font-mono text-[8px] font-bold uppercase leading-none tracking-[0.22em] text-ink/45 whitespace-nowrap">
        {label}
      </span>
      {children}
    </div>
  );
}

// Thin vertical rule used to separate clusters of controls (hidden once wrapped).
function ControlDivider() {
  return <span aria-hidden className="mt-[13px] hidden h-8 w-px self-start bg-ink/15 xl:block" />;
}

// A retro segmented switch — replaces binary dropdowns with a cleaner toggle.
function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  disabled = false,
  ariaLabel,
  fill = false,
}: {
  value: T;
  options: Array<{ value: T; label: string; title?: string }>;
  onChange: (value: T) => void;
  disabled?: boolean;
  ariaLabel?: string;
  fill?: boolean;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={[
        'flex h-8 border-[2px] border-ink shadow-retro-sm',
        fill ? 'w-full' : 'w-fit',
        disabled ? 'opacity-50' : '',
      ].join(' ')}
    >
      {options.map((opt, index) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            title={opt.title}
            aria-pressed={active}
            disabled={disabled}
            onClick={() => onChange(opt.value)}
            className={[
              'whitespace-nowrap px-3 font-mono text-[11px] font-bold uppercase tracking-wider outline-none transition-colors',
              fill ? 'flex-1' : '',
              disabled ? 'cursor-not-allowed' : 'cursor-pointer',
              active ? 'bg-ink text-signal-lime' : 'bg-paper text-ink hover:bg-signal-amber',
              index > 0 ? 'border-l-[2px] border-ink' : '',
            ].join(' ')}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
