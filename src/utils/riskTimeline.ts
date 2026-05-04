// riskTimeline: bins forecast stops into sequential forecast-hour windows
// and summarizes category, coverage, dominant hazard, and key parameters.

import type {
  ForecastBundle,
  HazardKey,
  HourSnapshot,
  RiskCategory,
} from '../types/forecast';
import { RISK_META } from '../types/forecast';

export type TimeOfDay = 'morning' | 'afternoon' | 'evening' | 'overnight';
export type TimelinePeriod =
  | 'f000_f006'
  | 'f007_f012'
  | 'f013_f018'
  | 'f019_f024'
  | 'f025_f036'
  | 'f037_f048';

export interface TimelineSegment {
  period: TimelinePeriod;
  label: string;
  hours: number[];           // forecast hours that fall in this period
  startHour: number;
  endHour: number;
  coverage: number;          // 0-1 storm coverage estimate
  dominantHazard: HazardKey | null;
  peakHazardProbability: number;
  category: RiskCategory;    // peak category in this period
  confidence: number;        // peak confidence in this period
  significantSevere: boolean; // any hour in this period has SIG severe
  peakCape: number;
  peakShear: number;
  note: string;
}

const PERIOD_WINDOWS: Array<{ period: TimelinePeriod; label: string; minHour: number; maxHour: number }> = [
  { period: 'f000_f006', label: 'F000-F006', minHour: 0,  maxHour: 6  },
  { period: 'f007_f012', label: 'F007-F012', minHour: 7,  maxHour: 12 },
  { period: 'f013_f018', label: 'F013-F018', minHour: 13, maxHour: 18 },
  { period: 'f019_f024', label: 'F019-F024', minHour: 19, maxHour: 24 },
  { period: 'f025_f036', label: 'F025-F036', minHour: 25, maxHour: 36 },
  { period: 'f037_f048', label: 'F037-F048', minHour: 37, maxHour: 48 },
];

const SEVERE_HAZARDS: HazardKey[] = ['tornado', 'hail', 'wind'];

const HAZARD_WORD: Record<HazardKey, string> = {
  tornado: 'tornado',
  hail: 'hail',
  wind: 'damaging wind',
  flood: 'flooding',
};

function coverageFor(snap: HourSnapshot): number {
  // Coverage from CAPE, forcing, initiation and capping.
  const cape   = Math.sqrt(Math.min(Math.max(snap.ingredients.mlcape, snap.ingredients.mucape) / 3500, 1));
  const force  = snap.ingredients.frontSignal === 'strong' ? 1 :
                 snap.ingredients.frontSignal === 'moderate' ? 0.7 :
                 snap.ingredients.frontSignal === 'weak' ? 0.4 : 0.15;
  const init   = snap.ingredients.initiationConf;
  const moist  = Math.min(Math.max(snap.ingredients.sfcDewpointF - 50, 0) / 22, 1);
  const cap    = snap.ingredients.capStrength === 'strong' ? 0.4 :
                 snap.ingredients.capStrength === 'moderate' ? 0.7 :
                 snap.ingredients.capStrength === 'weak' ? 0.9 : 1;
  return Math.max(0, Math.min(1, 0.34 * cape + 0.24 * force + 0.27 * init + 0.15 * moist) * cap);
}

function noteFor(seg: TimelineSegment): string {
  if (seg.coverage < 0.1) return 'Quiet — minimal convective coverage expected.';
  if (seg.category === 'TSTM') return 'Scattered general thunderstorms — limited severe threat.';
  const hzWord = seg.dominantHazard ? HAZARD_WORD[seg.dominantHazard] : 'severe';
  const sigTag = seg.significantSevere ? ' SIG severe potential.' : '';
  const probTag = seg.peakHazardProbability > 0 ? ` Peak ${hzWord} ${Math.round(seg.peakHazardProbability * 100)}%.` : '';
  const envTag = ` Peak CAPE ${Math.round(seg.peakCape)} J/kg, shear ${Math.round(seg.peakShear)} kt.`;
  if (seg.category === 'HIGH') return `Outbreak conditions — widespread ${hzWord} threat.${sigTag}`;
  if (seg.category === 'MOD')  return `Significant ${hzWord} event possible — monitor closely.${sigTag}`;
  if (seg.category === 'ENH')  return `Organized severe storms with focus on ${hzWord}.${probTag}${envTag}${sigTag}`;
  if (seg.category === 'SLGT') return `Scattered severe — ${hzWord} primary concern.${probTag}${envTag}${sigTag}`;
  return `Isolated severe possible — ${hzWord} primary concern.${probTag}${envTag}${sigTag}`;
}

function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function representativePeak(snaps: HourSnapshot[]): HourSnapshot {
  return snaps.reduce((best, current) => {
    const currentOrd = RISK_META[current.outlook.category].ord;
    const bestOrd = RISK_META[best.outlook.category].ord;
    if (currentOrd !== bestOrd) return currentOrd > bestOrd ? current : best;
    const currentCoverage = coverageFor(current);
    const bestCoverage = coverageFor(best);
    if (currentCoverage !== bestCoverage) return currentCoverage > bestCoverage ? current : best;
    return current.outlook.confidence > best.outlook.confidence ? current : best;
  });
}

function dominantSevereHazard(snaps: HourSnapshot[]): { hazard: HazardKey | null; probability: number } {
  let bestHazard: HazardKey | null = null;
  let bestOrd = -1;
  let bestProb = 0;

  SEVERE_HAZARDS.forEach((hazard) => {
    const hazardOrd = Math.max(...snaps.map((snap) => RISK_META[snap.hazards[hazard].level].ord));
    const hazardProb = Math.max(...snaps.map((snap) => snap.hazards[hazard].probability));
    if (hazardOrd > bestOrd || (hazardOrd === bestOrd && hazardProb > bestProb)) {
      bestHazard = hazard;
      bestOrd = hazardOrd;
      bestProb = hazardProb;
    }
  });

  return { hazard: bestHazard, probability: bestProb };
}

export function buildRiskTimeline(bundle: ForecastBundle): TimelineSegment[] {
  const segs: TimelineSegment[] = PERIOD_WINDOWS.map((window) => {
    const snaps = bundle.hours.filter((snap) =>
      snap.forecastHour >= window.minHour && snap.forecastHour <= window.maxHour
    );
    if (snaps.length === 0) {
      const empty: TimelineSegment = {
        period: window.period,
        label: window.label,
        hours: [],
        startHour: window.minHour,
        endHour: window.maxHour,
        coverage: 0,
        dominantHazard: null,
        peakHazardProbability: 0,
        category: 'TSTM',
        confidence: 0.3,
        significantSevere: false,
        peakCape: 0,
        peakShear: 0,
        note: 'No forecast points in this period.',
      };
      return empty;
    }

    const peak = representativePeak(snaps);
    const coverages = snaps.map(coverageFor);
    const dominant = dominantSevereHazard(snaps);
    const significantSevere = snaps.some((s) => SEVERE_HAZARDS.some((hazard) => s.hazards[hazard].significantSevere));
    const seg: TimelineSegment = {
      period: window.period,
      label: window.label,
      hours: snaps.map((s) => s.forecastHour),
      startHour: snaps[0].forecastHour,
      endHour: snaps[snaps.length - 1].forecastHour,
      coverage: average(coverages),
      dominantHazard: dominant.hazard,
      peakHazardProbability: dominant.probability,
      category: peak.outlook.category,
      confidence: average(snaps.map((s) => s.outlook.confidence)),
      significantSevere,
      peakCape: Math.max(...snaps.map((s) => Math.max(s.ingredients.mlcape, s.ingredients.mucape))),
      peakShear: Math.max(...snaps.map((s) => s.ingredients.shear06Kt)),
      note: '',
    };
    seg.note = noteFor(seg);
    return seg;
  });

  return segs;
}
