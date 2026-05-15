import type { HazardKey, HourSnapshot, RiskCategory } from '../types/forecast';
import { HAZARD_META } from '../types/forecast';
import type { ArtifactRiskCategory, OutlookArtifacts, OutlookTimelineHourSummary } from '../types/outlookArtifacts';
import {
  type ArtifactHazardKey,
  getArtifactHazardPeak,
  getArtifactMainHazard,
  getArtifactMaxCategory,
  getArtifactRiskPolygonMaxCategory,
} from './artifactProbabilities';
import { displayRegionLabel } from './regionDisplay';

type ArtifactStatusLike = 'loading' | 'ready' | 'missing' | 'error' | 'pending' | 'failed' | string | undefined;

interface GeneratedHeadlineInput {
  snapshot: HourSnapshot;
  artifacts?: OutlookArtifacts | null;
  artifactStatus?: ArtifactStatusLike;
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
}: GeneratedHeadlineInput): GeneratedOutlookSummary {
  const usingGeneratedArtifacts = artifactStatus === 'ready';
  if (!usingGeneratedArtifacts) {
    return {
      category: snapshot.outlook.category,
      hazard: snapshot.outlook.mainHazard,
      headline: snapshot.outlook.headline,
      usingGeneratedArtifacts: false,
    };
  }

  const timelineHour = findTimelineHour(artifacts, snapshot.forecastHour);
  const artifactCategory = getBestArtifactCategory(artifacts, snapshot.forecastHour, timelineHour);
  const category = artifactCategory && artifactCategory !== 'NONE'
    ? normalizeRiskCategory(artifactCategory)
    : 'TSTM';
  const hazard = getBestArtifactHazard(artifacts, snapshot.forecastHour, timelineHour) ?? snapshot.outlook.mainHazard;
  const headline = buildArtifactHeadline(snapshot, artifacts, category, hazard, artifactCategory, timelineHour);

  return {
    category,
    hazard,
    headline,
    usingGeneratedArtifacts: true,
  };
}

function buildArtifactHeadline(
  snapshot: HourSnapshot,
  artifacts: OutlookArtifacts | null | undefined,
  category: RiskCategory,
  hazard: HazardKey,
  artifactCategory: ArtifactRiskCategory | undefined,
  timelineHour: OutlookTimelineHourSummary | undefined,
): string {
  const timeLabel = formatForecastTime(snapshot.forecastHour, snapshot.validTimeISO);
  const region = displayRegionLabel(snapshot.region.label);
  const hazards = rankedHazards(artifacts, snapshot.forecastHour, timelineHour);

  if (!artifactCategory || artifactCategory === 'NONE') {
    return `Organized severe weather remains below categorical thresholds near ${region} around ${timeLabel}.`;
  }

  if (category === 'TSTM') {
    return `General thunder near ${region} around ${timeLabel}, with organized severe weather limited.`;
  }

  const hazardPhrase = hazards.length > 0
    ? `mainly ${hazardListPhrase(hazards)}`
    : `mainly ${HAZARD_META[hazard].label.toLowerCase()}`;

  return `Severe storms possible near ${region} around ${timeLabel}, ${hazardPhrase}.`;
}

function getBestArtifactCategory(
  artifacts: OutlookArtifacts | null | undefined,
  forecastHour: number,
  timelineHour: OutlookTimelineHourSummary | undefined,
): ArtifactRiskCategory | undefined {
  return getArtifactMaxCategory(artifacts ?? null, forecastHour)
    ?? getArtifactRiskPolygonMaxCategory(artifacts ?? null, forecastHour)
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
