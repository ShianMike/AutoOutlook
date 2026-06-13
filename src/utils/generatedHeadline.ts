import type { HazardKey, HourSnapshot, RiskCategory } from '../types/forecast';
import { HAZARD_META } from '../types/forecast';
import type { ArtifactRiskCategory, OutlookArtifacts, OutlookTimelineHourSummary } from '../types/outlookArtifacts';
import {
  type ArtifactHazardKey,
  getArtifactHazardPeak,
  getArtifactMainHazard,
  getArtifactMaxCategory,
} from './artifactProbabilities';
import { focusLocationFromSnapshot } from './focusLocation';

type ArtifactStatusLike = 'loading' | 'ready' | 'missing' | 'error' | 'pending' | 'failed' | string | undefined;

interface GeneratedHeadlineInput {
  snapshot: HourSnapshot;
  artifacts?: OutlookArtifacts | null;
  artifactStatus?: ArtifactStatusLike;
  isMerged?: boolean;
  focusLabelOverride?: string;
}

export interface GeneratedOutlookSummary {
  category: RiskCategory;
  hazard: HazardKey;
  headline: string;
  usingGeneratedArtifacts: boolean;
}

const HAZARD_KEYS: ArtifactHazardKey[] = ['tornado', 'hail', 'wind'];
const CATEGORY_ORDER: ArtifactRiskCategory[] = ['NONE', 'TSTM', 'MRGL', 'SLGT', 'ENH', 'MDT', 'HIGH'];

export function buildGeneratedOutlookSummary({
  snapshot,
  artifacts,
  artifactStatus,
  isMerged = false,
  focusLabelOverride,
}: GeneratedHeadlineInput): GeneratedOutlookSummary {
  const usingGeneratedArtifacts = artifactStatus === 'ready';
  if (!usingGeneratedArtifacts) {
    const fallbackHeadline = focusLabelOverride
      ? replaceRegionLabel(snapshot.outlook.headline, focusLocationFromSnapshot(snapshot).label, focusLabelOverride)
      : snapshot.outlook.headline;
    return {
      category: snapshot.outlook.category,
      hazard: snapshot.outlook.mainHazard,
      headline: isMerged ? applyMergedFraming(fallbackHeadline) : fallbackHeadline,
      usingGeneratedArtifacts: false,
    };
  }

  const timelineHour = findTimelineHour(artifacts, snapshot.forecastHour);
  const artifactCategory = getBestArtifactCategory(artifacts, snapshot.forecastHour, timelineHour);
  const category = artifactCategory && artifactCategory !== 'NONE'
    ? normalizeRiskCategory(artifactCategory)
    : 'TSTM';
  const hazard = getBestArtifactHazard(artifacts, snapshot.forecastHour, timelineHour) ?? snapshot.outlook.mainHazard;
  const headline = buildArtifactHeadline(snapshot, artifacts, category, hazard, artifactCategory, timelineHour, isMerged, focusLabelOverride);

  return {
    category,
    hazard,
    headline,
    usingGeneratedArtifacts: true,
  };
}

// Swap a region label inside a pre-built headline (used for merged mode so the
// fallback rule-engine headline names the merged peak region, not the hour's).
function replaceRegionLabel(headline: string, from: string, to: string): string {
  if (!from || from === to) return headline;
  return headline.split(from).join(to);
}

// In merged (multi-cycle Day 1) mode the banner describes the whole outlook
// period rather than a single forecast hour, so strip any hour-specific time
// phrase ("around 10Z") and replace it with day-period framing.
function applyMergedFraming(headline: string): string {
  return headline
    .replace(/\b(?:around|at|by|near)\s+\d{1,2}Z\b/gi, 'through the Day 1 period')
    .replace(/\s+/g, ' ')
    .trim();
}

function buildArtifactHeadline(
  snapshot: HourSnapshot,
  artifacts: OutlookArtifacts | null | undefined,
  category: RiskCategory,
  hazard: HazardKey,
  artifactCategory: ArtifactRiskCategory | undefined,
  timelineHour: OutlookTimelineHourSummary | undefined,
  isMerged = false,
  focusLabelOverride?: string,
): string {
  const timeLabel = formatForecastTime(snapshot.forecastHour, snapshot.validTimeISO);
  const whenPhrase = isMerged ? 'through the Day 1 period' : `around ${timeLabel}`;
  const region = focusLabelOverride ?? focusLocationFromSnapshot(snapshot).label;
  const hazards = rankedHazards(artifacts, snapshot.forecastHour, timelineHour);

  if (!artifactCategory || artifactCategory === 'NONE') {
    return `Organized severe weather remains below categorical thresholds near ${region} ${whenPhrase}.`;
  }

  if (category === 'TSTM') {
    return `General thunder near ${region} ${whenPhrase}, with organized severe weather limited.`;
  }

  const hazardPhrase = hazards.length > 0
    ? `mainly ${hazardListPhrase(hazards)}`
    : `mainly ${HAZARD_META[hazard].label.toLowerCase()}`;

  return `Severe storms possible near ${region} ${whenPhrase}, ${hazardPhrase}.`;
}

function getBestArtifactCategory(
  artifacts: OutlookArtifacts | null | undefined,
  forecastHour: number,
  timelineHour: OutlookTimelineHourSummary | undefined,
): ArtifactRiskCategory | undefined {
  // Final category math must come from the capped probability tile/counts,
  // not the broader non-strict visual contour polygons.
  return getArtifactMaxCategory(artifacts ?? null, forecastHour)
    ?? timelineHour?.category
    ?? maxCategoryFromCounts(categoryCountsForHour(artifacts, forecastHour));
}

function getBestArtifactHazard(
  artifacts: OutlookArtifacts | null | undefined,
  forecastHour: number,
  timelineHour: OutlookTimelineHourSummary | undefined,
): ArtifactHazardKey | undefined {
  return getArtifactMainHazard(artifacts ?? null, forecastHour)
    ?? timelineHour?.mainHazard
    ?? rankedHazards(artifacts, forecastHour, timelineHour)[0]?.hazard;
}

function rankedHazards(
  artifacts: OutlookArtifacts | null | undefined,
  forecastHour: number,
  timelineHour: OutlookTimelineHourSummary | undefined,
): Array<{ hazard: ArtifactHazardKey; probability: number }> {
  return HAZARD_KEYS
    .map((hazard) => ({
      hazard,
      probability: getArtifactHazardPeak(artifacts ?? null, forecastHour, hazard)
        ?? timelineHour?.probabilityMax?.[hazard]
        ?? (timelineHour?.mainHazard === hazard ? timelineHour.peakHazardProbability : 0)
        ?? 0,
    }))
    .filter((item) => Number.isFinite(item.probability) && item.probability > 0)
    .sort((a, b) => b.probability - a.probability)
    .slice(0, 2);
}

function hazardListPhrase(hazards: Array<{ hazard: ArtifactHazardKey; probability: number }>): string {
  const parts = hazards.map((item) => HAZARD_META[item.hazard].label.toLowerCase());
  return parts.length === 1 ? parts[0] : `${parts[0]} with ${parts[1]}`;
}

function categoryCountsForHour(
  artifacts: OutlookArtifacts | null | undefined,
  forecastHour: number,
): Record<string, number> | undefined {
  const probabilityHour = artifacts?.probabilityTiles?.hours.find((hour) => hour.forecastHour === forecastHour);
  return probabilityHour?.categoryCounts
    ?? artifacts?.metadata.categoryCounts
    ?? artifacts?.metadata.aggregateCategoryCounts
    ?? undefined;
}

function maxCategoryFromCounts(counts: Record<string, number> | undefined): ArtifactRiskCategory | undefined {
  if (!counts) return undefined;
  let best: ArtifactRiskCategory | undefined;
  CATEGORY_ORDER.forEach((category) => {
    if (category !== 'NONE' && normalizedCount(counts, category) <= 0) return;
    if (!best || artifactCategoryOrdinal(category) > artifactCategoryOrdinal(best)) best = category;
  });
  return best;
}

function normalizedCount(counts: Record<string, number>, category: ArtifactRiskCategory | RiskCategory): number {
  if (category === 'MOD') return Number(counts.MOD ?? counts.MDT ?? 0);
  if (category === 'MDT') return Number(counts.MDT ?? counts.MOD ?? 0);
  return Number(counts[category] ?? 0);
}

function normalizeRiskCategory(category: ArtifactRiskCategory): RiskCategory {
  return category === 'MDT' ? 'MOD' : category as RiskCategory;
}

function artifactCategoryOrdinal(category: ArtifactRiskCategory): number {
  return CATEGORY_ORDER.indexOf(category === 'MOD' ? 'MDT' : category);
}

function findTimelineHour(
  artifacts: OutlookArtifacts | null | undefined,
  forecastHour: number,
): OutlookTimelineHourSummary | undefined {
  return artifacts?.timelineSummary?.hours.find((hour) => hour.forecastHour === forecastHour);
}

function formatForecastTime(forecastHour: number, validTimeISO: string): string {
  const date = new Date(validTimeISO);
  if (!Number.isFinite(date.getTime())) return `F${String(forecastHour).padStart(2, '0')}`;
  return `${String(date.getUTCHours()).padStart(2, '0')}Z`;
}
