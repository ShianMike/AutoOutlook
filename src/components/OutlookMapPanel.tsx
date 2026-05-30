import { useEffect, useRef, useState } from 'react';
import { toPng } from 'html-to-image';
import type { ForecastBundle, HourSnapshot } from '../types/forecast';
import { FORECAST_HOUR_LABELS } from '../types/forecast';
import RetroPanel from './retro/RetroPanel';
import RetroBadge from './retro/RetroBadge';
import HazardOutlookMap from './HazardOutlookMap';
import GeneratedOutlookMap from './GeneratedOutlookMap';
import GeneratedHazardProbabilityMap, { hasGeneratedHazardTile } from './GeneratedHazardProbabilityMap';
import ForecastDisclaimer from './ForecastDisclaimer';
import type { OutlookArtifactState } from '../hooks/useOutlookArtifacts';
import type { OutlookArtifacts } from '../types/outlookArtifacts';
import { focusLocationFromSnapshot } from '../utils/focusLocation';
import { recordCanvasesToGif } from '../utils/gifRecorder';

interface OutlookMapPanelProps {
  snapshot: HourSnapshot | null;
  outlookArtifacts: OutlookArtifactState;
  bundle: ForecastBundle | null;
  selectedIndex: number;
  isPlaying: boolean;
  onIndexChange: (index: number) => void;
  setPlaying: (playing: boolean) => void;
}

type OutlookMode = 'levels' | 'hazards';
type GifQualityPreset = 'small' | 'medium' | 'large';

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
const EXPORT_CAPTURE_CSS_HEIGHT = 1125;
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
`;
const GIF_QUALITY_CONFIG: Record<GifQualityPreset, { label: string; encoderQuality: number }> = {
  small: { label: 'Small', encoderQuality: 20 },
  medium: { label: 'Medium', encoderQuality: 12 },
  large: { label: 'Large', encoderQuality: 8 },
};

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
}: OutlookMapPanelProps) {
  const [mode, setMode] = useState<OutlookMode>('levels');
  const [hazardLayout, setHazardLayout] = useState<'all' | 'single'>('all');
  const [selectedHazard, setSelectedHazard] = useState<'thunder' | 'hail' | 'wind' | 'tornado'>('thunder');
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
  const latestExportStateRef = useRef({ snapshot, outlookArtifacts });
  const cancelGifRef = useRef(false);
  const gifAbortRef = useRef<AbortController | null>(null);
  const forecastStops = bundle?.hours ?? [];
  const isAnyExporting = isExporting || isExportingGif;
  const artifactMetadata = outlookArtifacts.artifacts?.metadata;
  const latestCandidate = artifactMetadata?.latestExtendedCandidate ?? undefined;
  const staleArtifacts = isNewerCycle(latestCandidate?.cycleTimeISO, artifactMetadata?.cycleTimeISO);
  const generatedHazardsReady = hasGeneratedHazardTile(outlookArtifacts.artifacts, snapshot?.forecastHour, outlookArtifacts.status);
  const mlDriven = Boolean(snapshot?.mlHazards);
  const useRuleHazardFallback = !mlDriven && outlookArtifacts.status === 'missing';
  const engineLabel = mlDriven
    ? outlookArtifacts.status === 'ready'
      ? 'Auto-generated · HRRR/XGBoost artifact pipeline'
      : 'Auto-generated · XGBoost hazard model · artifact pending'
    : 'Auto-generated · rule-based outlook engine v1';
  const hourLabel = snapshot
    ? FORECAST_HOUR_LABELS[snapshot.forecastHour] ?? `+${snapshot.forecastHour}h`
    : '—';

  const validTime = snapshot
    ? (() => {
        const d = new Date(snapshot.validTimeISO);
        const hh = String(d.getUTCHours()).padStart(2, '0');
        const mm = String(d.getUTCMinutes()).padStart(2, '0');
        return `${hh}${mm}Z ${String(d.getUTCDate()).padStart(2, '0')}/${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
      })()
    : '—';

  const shear = snapshot ? `${Math.round(snapshot.ingredients.shear06Kt)} kt SHR` : '—';
  const cape = snapshot ? `${Math.round(snapshot.ingredients.mucape)} CAPE` : '—';
  const timeRows = [
    ['HRRR cycle', artifactMetadata?.cycle ?? fmtUTC(artifactMetadata?.cycleTimeISO)],
    ['Forecast valid', fmtUTC(snapshot?.validTimeISO)],
    ['Artifact generated', fmtUTC(artifactMetadata?.generatedAtISO)],
  ] as const;

  useEffect(() => {
    latestExportStateRef.current = { snapshot, outlookArtifacts };
  }, [snapshot, outlookArtifacts]);

  useEffect(() => {
    if (forecastStops.length === 0) {
      setGifStartIndex(0);
      setGifEndIndex(0);
      return;
    }
    setGifStartIndex((index) => Math.max(0, Math.min(index, forecastStops.length - 1)));
    setGifEndIndex((index) => Math.max(0, Math.min(index, forecastStops.length - 1)));
  }, [forecastStops.length]);

  const openGifDialog = () => {
    if (!snapshot || forecastStops.length === 0 || isAnyExporting) return;
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
      const filename = `autooutlook_${mode}_F${String(snapshot.forecastHour).padStart(3, '0')}_${validStamp}.png`;
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
      title={`F${String(snapshot?.forecastHour ?? 0).padStart(3, '0')}h Automated Convective Outlook`}
      eyebrow="03 / automated categorical + hazard outlook · auto-detected focus region"
      badge={<RetroBadge tone="paper">FCST · {hourLabel}</RetroBadge>}
      size="sm"
      className="[&>div]:p-2"
    >
      <div className="mb-2 border-[3px] border-ink bg-paper shadow-retro-sm flex flex-col animate-fadeIn">
        {/* Main selector row */}
        <div className={`flex flex-wrap items-center justify-between gap-2 p-2 ${mode === 'hazards' ? 'border-b-[3px] border-ink' : ''}`}>
          <div className="border-[2px] border-ink bg-paper px-2 py-1 font-mono text-[9px] font-bold uppercase tracking-[0.28em] text-ink shadow-retro-sm">
            Forecast type
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <ModeButton active={mode === 'levels'} onClick={() => setMode('levels')} disabled={isAnyExporting}>
              Risk Levels
            </ModeButton>
            <ModeButton active={mode === 'hazards'} onClick={() => setMode('hazards')} disabled={isAnyExporting}>
              Hazard Probs
            </ModeButton>
            <ModeButton active={false} onClick={saveCurrentMap} disabled={!snapshot || isAnyExporting}>
              {isExporting ? 'Saving…' : `Save ${mode === 'levels' ? 'Levels' : 'Hazards'} PNG`}
            </ModeButton>
            <ModeButton active={false} onClick={openGifDialog} disabled={!snapshot || forecastStops.length === 0 || isAnyExporting}>
              {isExportingGif ? 'Saving GIF…' : `Save ${mode === 'levels' ? 'Levels' : 'Hazards'} GIF`}
            </ModeButton>
          </div>
        </div>

        {/* Hazard View sub-row */}
        {mode === 'hazards' && (
          <div className="flex flex-wrap items-center justify-between gap-2 p-2 bg-paper/40 border-t-[0px] transition-all">
            <div className="border-[2px] border-ink bg-paper px-2 py-1 font-mono text-[9px] font-bold uppercase tracking-[0.28em] text-ink shadow-retro-sm">
              Hazard View
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <SubModeButton active={hazardLayout === 'all'} onClick={() => setHazardLayout('all')} disabled={isAnyExporting}>
                All 4 Grid
              </SubModeButton>
              <SubModeButton active={hazardLayout === 'single' && selectedHazard === 'thunder'} onClick={() => { setHazardLayout('single'); setSelectedHazard('thunder'); }} disabled={isAnyExporting}>
                Thunderstorm
              </SubModeButton>
              <SubModeButton active={hazardLayout === 'single' && selectedHazard === 'hail'} onClick={() => { setHazardLayout('single'); setSelectedHazard('hail'); }} disabled={isAnyExporting}>
                Hail
              </SubModeButton>
              <SubModeButton active={hazardLayout === 'single' && selectedHazard === 'wind'} onClick={() => { setHazardLayout('single'); setSelectedHazard('wind'); }} disabled={isAnyExporting}>
                Damaging Wind
              </SubModeButton>
              <SubModeButton active={hazardLayout === 'single' && selectedHazard === 'tornado'} onClick={() => { setHazardLayout('single'); setSelectedHazard('tornado'); }} disabled={isAnyExporting}>
                Tornado
              </SubModeButton>
            </div>
          </div>
        )}

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
          <div className="border-t-[3px] border-ink bg-paper p-3">
            <div className="mb-2 font-mono text-[10px] font-bold uppercase tracking-[0.28em] text-ink/65">
              Animated GIF export · {mode === 'levels' ? 'Risk Levels' : 'Hazard Probabilities'}
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
              <label className="flex flex-col gap-1 font-mono text-[9px] font-bold uppercase tracking-widest text-ink/65">
                Start valid
                <select
                  value={gifStartIndex}
                  onChange={(event) => {
                    const next = Number(event.target.value);
                    setGifStartIndex(next);
                    setGifEndIndex((index) => Math.max(index, next));
                  }}
                  className="border-[2px] border-ink bg-paper px-2 py-1 font-mono text-[11px] font-bold text-ink"
                >
                  {forecastStops.map((stop, index) => (
                    <option key={`gif-start-${stop.forecastHour}-${stop.validTimeISO}`} value={index}>
                      {fmtValidSelect(stop.validTimeISO)} · F{String(stop.forecastHour).padStart(3, '0')}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 font-mono text-[9px] font-bold uppercase tracking-widest text-ink/65">
                End valid
                <select
                  value={gifEndIndex}
                  onChange={(event) => setGifEndIndex(Number(event.target.value))}
                  className="border-[2px] border-ink bg-paper px-2 py-1 font-mono text-[11px] font-bold text-ink"
                >
                  {forecastStops.map((stop, index) => (
                    <option key={`gif-end-${stop.forecastHour}-${stop.validTimeISO}`} value={index} disabled={index < gifStartIndex}>
                      {fmtValidSelect(stop.validTimeISO)} · F{String(stop.forecastHour).padStart(3, '0')}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 font-mono text-[9px] font-bold uppercase tracking-widest text-ink/65">
                Frame delay
                <select
                  value={gifDelayMs}
                  onChange={(event) => setGifDelayMs(Number(event.target.value))}
                  className="border-[2px] border-ink bg-paper px-2 py-1 font-mono text-[11px] font-bold text-ink"
                >
                  {GIF_DELAY_OPTIONS.map((delayMs) => (
                    <option key={delayMs} value={delayMs}>{delayMs} ms</option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 font-mono text-[9px] font-bold uppercase tracking-widest text-ink/65">
                Quality
                <select
                  value={gifQuality}
                  onChange={(event) => setGifQuality(event.target.value as GifQualityPreset)}
                  className="border-[2px] border-ink bg-paper px-2 py-1 font-mono text-[11px] font-bold text-ink"
                >
                  {(Object.keys(GIF_QUALITY_CONFIG) as GifQualityPreset[]).map((quality) => (
                    <option key={quality} value={quality}>{GIF_QUALITY_CONFIG[quality].label}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
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
                className="retro-button bg-signal-amber px-3 py-1.5 text-[12px] leading-none text-ink hover:bg-signal-amber"
              >
                Generate GIF
              </button>
            </div>
          </div>
        )}
        {exportError && (
          <div className="border-t-[3px] border-ink bg-paper px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-widest text-signal-red">
            {exportError}
          </div>
        )}
      </div>

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
              F{String(snapshot?.forecastHour ?? 0).padStart(3, '0')}h {mode === 'levels' ? 'Risk Levels' : 'Hazard Probabilities'}
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
          <span className="shrink-0">HRRR cycle: {fmtUTC(artifactMetadata?.cycleTimeISO)}</span>
          <span className="shrink-0">Forecast valid: {validTime}</span>
          <span className="shrink-0">Generated: {fmtUTC(artifactMetadata?.generatedAtISO)}</span>
          <span className="min-w-[220px] flex-1 text-center leading-snug text-paper/80">
            {snapshot ? focusLocationFromSnapshot(snapshot).label : 'AWAITING REGION DETECTION…'}
          </span>
          <span className="text-paper/80">{cape}</span>
          <span className="text-paper/80">{shear}</span>
          <span className="shrink-0">{snapshot ? fmtCoord(snapshot.region.centerLat, snapshot.region.centerLon) : '—'}</span>
        </div>

        {mode === 'levels' ? (
          <div className="outlook-export-stage border-[3px] border-ink bg-paper p-2">
            <GeneratedOutlookMap
              snapshot={snapshot}
              status={outlookArtifacts.status}
              artifacts={outlookArtifacts.artifacts}
              message={outlookArtifacts.message}
            />
          </div>
        ) : (
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
                    snapshot={snapshot}
                    hazard="thunder"
                    title="Thunderstorm Outlook"
                    artifacts={outlookArtifacts.artifacts}
                    status={outlookArtifacts.status}
                  />
                  <GeneratedHazardProbabilityMap
                    snapshot={snapshot}
                    hazard="hail"
                    title="Hail Outlook"
                    artifacts={outlookArtifacts.artifacts}
                    status={outlookArtifacts.status}
                  />
                  <GeneratedHazardProbabilityMap
                    snapshot={snapshot}
                    hazard="wind"
                    title="Damaging Wind Outlook"
                    artifacts={outlookArtifacts.artifacts}
                    status={outlookArtifacts.status}
                  />
                  <GeneratedHazardProbabilityMap
                    snapshot={snapshot}
                    hazard="tornado"
                    title="Tornado Outlook"
                    artifacts={outlookArtifacts.artifacts}
                    status={outlookArtifacts.status}
                  />
                </>
              ) : (
                <GeneratedHazardProbabilityMap
                  snapshot={snapshot}
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
                  artifacts={outlookArtifacts.artifacts}
                  status={outlookArtifacts.status}
                />
              )
            ) : useRuleHazardFallback ? (
              hazardLayout === 'all' ? (
                <>
                  <HazardOutlookMap snapshot={snapshot} hazard="thunder" title="Thunderstorm Outlook" sourceLabel="Rule fallback" />
                  <HazardOutlookMap snapshot={snapshot} hazard="hail" title="Hail Outlook" sourceLabel="Rule fallback" />
                  <HazardOutlookMap snapshot={snapshot} hazard="wind" title="Damaging Wind Outlook" sourceLabel="Rule fallback" />
                  <HazardOutlookMap snapshot={snapshot} hazard="tornado" title="Tornado Outlook" sourceLabel="Rule fallback" />
                </>
              ) : (
                <HazardOutlookMap
                  snapshot={snapshot}
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
                />
              )
            ) : (
              <GeneratedHazardsUnavailable message={outlookArtifacts.message} status={outlookArtifacts.status} />
            )}
          </div>
        )}

        {/* Footer strip */}
        <div className="outlook-export-footer border-[3px] border-t-0 border-ink bg-paper px-3 py-1.5 flex items-center justify-between gap-3 flex-wrap font-mono text-[10px] uppercase tracking-widest text-ink/70">
          <span>States in focus: {snapshot?.region.states.join(' · ') ?? '—'}</span>
          <span>{engineLabel}</span>
        </div>

        <div className="outlook-export-disclaimer border-[3px] border-t-0 border-ink bg-ink px-3 py-2 text-paper">
          <ForecastDisclaimer variant="export" />
        </div>
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

