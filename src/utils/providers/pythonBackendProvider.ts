// Calls the local Flask backend (NOMADS via siphon + MetPy diagnostics).
// Backend supplies raw ingredients per hour; this provider runs the TS
// engines so the displayed outlook stays consistent across providers.

import type {
  CityMarker,
  ForecastBundle,
  ForecastProvider,
  HazardAssessment,
  HazardKey,
  HourSnapshot,
  Ingredients,
  MlHazardProbabilities,
  MlModelMetadata,
  Region,
  RiskCategory,
  StormMode,
  SignalStrength,
  OutlookArea,
  SurfaceBoundaryFocus,
  UpperAirOverlayMetadata,
  UpperAirLine,
  UpperAirVector,
} from '../../types/forecast';
import { buildOutlook } from '../outlookEngine';
import { buildHazards, lvlFromProb } from '../hazardEngine';
import { buildRiskPolygons } from '../polygonBuilder';
import { applyLeadTimeUncertainty } from '../leadTimeUncertainty';

const ENDPOINT = 'http://127.0.0.1:8765/api/forecast';

interface BackendHour {
  forecastHour: number;
  validTimeISO: string;
  region?: Region;
  outlookAreas?: OutlookArea[];
  ingredients: Partial<Ingredients>;
  mlHazards?: MlHazardProbabilities;
  upperAirLines?: UpperAirLine[];
  upperAirVectors?: UpperAirVector[];
  upperAirOverlay?: UpperAirOverlayMetadata;
  surfaceBoundary?: SurfaceBoundaryFocus;
}

interface BackendBundle {
  cycle: string;
  issuedAtISO: string;
  providerNotes: string;
  latencyMs: number;
  region: Region;
  cities?: { name: string; lat: number; lon: number }[];
  mlHazardHours?: number;
  mlModel?: MlModelMetadata;
  hours: BackendHour[];
}

export const pythonBackendProvider: ForecastProvider = {
  id: 'backend',
  label: 'NOMADS HRRR · siphon + MetPy',
  async fetchBundle(signal?: AbortSignal): Promise<ForecastBundle> {
    const t0 = performance.now();
    const res = await fetch(ENDPOINT, { signal });
    if (!res.ok) throw new Error(`Backend HTTP ${res.status}`);
    const raw = (await res.json()) as BackendBundle;

    const region = raw.region;
    const cities = raw.cities ?? [
      { name: region.label.split('(')[0].trim(), lat: region.centerLat, lon: region.centerLon },
    ];

    const hours: HourSnapshot[] = stabilizeOneHourAreaDropouts(raw.hours.map((h) => {
      const ing = withDefaults(h.ingredients);
      const hazards = h.mlHazards ? buildHazardsFromMl(ing, h.mlHazards) : buildHazards(ing);
      const outlook = buildOutlook(ing, hazards);
      const hourRegion = h.region ?? region;
      const backendOutlookAreas = h.mlHazards ? undefined : h.outlookAreas;
      const outlookAreas = movingOutlookAreas(
        backendOutlookAreas,
        hourRegion,
        outlook.category,
        ing,
        hazards,
        h.forecastHour,
      );
      const cityMarkers: CityMarker[] = cities.map((c) => {
        const dLat = Math.abs(c.lat - hourRegion.centerLat);
        const dLon = Math.abs(c.lon - hourRegion.centerLon);
        const dist = Math.sqrt(dLat * dLat + dLon * dLon);
        const ramp = ['TSTM', 'MRGL', 'SLGT', 'ENH', 'MOD', 'HIGH'] as const;
        const peakOrd = ramp.indexOf(outlook.category);
        let ord = peakOrd;
        if (dist > 1.5) ord = Math.max(0, ord - 1);
        if (dist > 3.0) ord = Math.max(0, ord - 1);
        if (dist > 5.0) ord = Math.max(0, ord - 1);
        return { name: c.name, lat: c.lat, lon: c.lon, risk: ramp[ord] };
      });
      return applyLeadTimeUncertainty({
        forecastHour: h.forecastHour,
        validTimeISO: h.validTimeISO,
        region: hourRegion,
        ingredients: ing,
        mlHazards: h.mlHazards,
        hazards,
        outlook,
        outlookAreas,
        riskPolygons: buildRiskPolygons(hourRegion, outlook.category),
        cities: cityMarkers,
        upperAirLines: h.upperAirLines,
        upperAirVectors: h.upperAirVectors,
        upperAirOverlay: h.upperAirOverlay,
        surfaceBoundary: h.surfaceBoundary,
      });
    }));

    const t1 = performance.now();
    return {
      cycle: raw.cycle,
      issuedAtISO: raw.issuedAtISO,
      hours,
      source: 'live',
      providerId: 'backend',
      providerNotes: raw.mlModel?.active
        ? `${raw.providerNotes} · ${raw.mlModel.artifactType === 'xgboost_joblib' ? 'XGBoost' : 'ML'} hazard model ${raw.mlModel.version}`
        : raw.providerNotes,
      latencyMs: raw.latencyMs ?? Math.round(t1 - t0),
      fetchedAtISO: new Date().toISOString(),
      mlHazardHours: raw.mlHazardHours,
      mlModel: raw.mlModel,
    };
  },
};

const ML_HAZARD_KEYS: Array<Exclude<HazardKey, 'flood'>> = ['tornado', 'hail', 'wind'];

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
}

function mlSignificantSevere(hazard: Exclude<HazardKey, 'flood'>, probability: number): boolean {
  return hazard === 'tornado' ? probability >= 0.10 : probability >= 0.30;
}

function mlExplanation(hazard: Exclude<HazardKey, 'flood'>, probability: number, level: RiskCategory): string {
  const percent = Math.round(probability * 100);
  const label = hazard === 'tornado' ? 'tornado' : hazard === 'hail' ? 'severe hail' : 'damaging wind';
  if (level === 'TSTM') {
    return `Backend ML hazard model keeps ${label} probability below categorical severe thresholds at ${percent}%.`;
  }
  return `Backend ML hazard model places ${label} probability at ${percent}%, driving a ${level} hazard assessment. Rule ingredients remain available for context.`;
}

function buildHazardsFromMl(
  ing: Ingredients,
  mlHazards: MlHazardProbabilities,
): Record<HazardKey, HazardAssessment> {
  const hazards: Record<HazardKey, HazardAssessment> = { ...buildHazards(ing) };
  ML_HAZARD_KEYS.forEach((hazard) => {
    const probability = clamp01(mlHazards[hazard]);
    const level = lvlFromProb(hazard, probability);
    const prior = hazards[hazard];
    hazards[hazard] = {
      ...prior,
      level,
      probability,
      confidence: clamp01(0.42 + probability * 0.68),
      significantSevere: mlSignificantSevere(hazard, probability),
      source: 'ml',
      supporting: [
        'Backend ML hazard model',
        ...prior.supporting.slice(0, 2),
      ],
      explanation: mlExplanation(hazard, probability, level),
    };
  });
  return hazards;
}

function stabilizeOneHourAreaDropouts(hours: HourSnapshot[]): HourSnapshot[] {
  return hours.map((hour, index) => {
    const prev = hours[index - 1];
    const next = hours[index + 1];
    if (!prev?.outlookAreas?.length || !next?.outlookAreas?.length) return hour;

    const currentAreas = hour.outlookAreas ?? [];
    const prevByKey = new Map(prev.outlookAreas.map((area) => [areaKey(area), area]));
    const currentKeys = new Set(currentAreas.map(areaKey));
    const recovered = next.outlookAreas
      .filter((area) => !currentKeys.has(areaKey(area)) && prevByKey.has(areaKey(area)))
      .map((area) => interpolateArea(prevByKey.get(areaKey(area))!, area));

    if (recovered.length === 0) return hour;

    return {
      ...hour,
      outlookAreas: [...currentAreas, ...recovered],
    };
  });
}

function areaKey(area: OutlookArea): string {
  const label = area.region.label
    .replace(/\s*\([^)]*\)/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
  return `${area.category}:${label}`;
}

function interpolateArea(before: OutlookArea, after: OutlookArea): OutlookArea {
  return {
    ...after,
    region: interpolateRegion(before.region, after.region),
    ingredients: interpolateIngredients(before.ingredients, after.ingredients),
  };
}

function interpolateRegion(before: Region, after: Region): Region {
  const lat = (before.centerLat + after.centerLat) / 2;
  const lon = (before.centerLon + after.centerLon) / 2;
  const minLon = (before.bbox[0] + after.bbox[0]) / 2;
  const minLat = (before.bbox[1] + after.bbox[1]) / 2;
  const maxLon = (before.bbox[2] + after.bbox[2]) / 2;
  const maxLat = (before.bbox[3] + after.bbox[3]) / 2;
  return {
    ...after,
    centerLat: lat,
    centerLon: lon,
    bbox: [minLon, minLat, maxLon, maxLat],
  };
}

function interpolateIngredients(
  before: Partial<Ingredients> | undefined,
  after: Partial<Ingredients> | undefined,
): Partial<Ingredients> | undefined {
  if (!before && !after) return undefined;
  if (!before) return after;
  if (!after) return before;

  const mixed: Partial<Ingredients> = { ...after };
  (Object.keys({ ...before, ...after }) as Array<keyof Ingredients>).forEach((key) => {
    const a = before[key];
    const b = after[key];
    if (typeof a === 'number' && typeof b === 'number') {
      (mixed[key] as number) = (a + b) / 2;
    }
  });
  return mixed;
}

function movingOutlookAreas(
  areas: OutlookArea[] | undefined,
  primaryRegion: Region,
  primaryCategory: OutlookArea['category'],
  ingredients: Ingredients,
  hazards: Record<HazardKey, HazardAssessment>,
  forecastHour: number,
): OutlookArea[] | undefined {
  const primaryArea: OutlookArea = {
    region: primaryRegion,
    category: primaryCategory,
    ingredients,
    hazards,
  };

  if (!areas?.length) return undefined;

  const ranked = areas.map((area, index) => ({
    area,
    index,
    distance: regionDistance(area.region, primaryRegion),
  }));
  const nearest = ranked.reduce((best, candidate) => (
    candidate.distance < best.distance ? candidate : best
  ));

  if (nearest.distance > 4.5) {
    return [primaryArea, ...areas];
  }

  return areas.map((area, index) => (
    index === nearest.index
      ? {
        ...area,
        region: primaryRegion,
        category: strongerCategory(area.category, primaryCategory),
        ingredients: area.ingredients ?? ingredients,
      }
      : {
        ...area,
        region: area.region,
      }
  ));
}

function regionDistance(a: Region, b: Region): number {
  const meanLat = ((a.centerLat + b.centerLat) / 2) * Math.PI / 180;
  return Math.hypot(
    (a.centerLon - b.centerLon) * Math.cos(meanLat),
    a.centerLat - b.centerLat,
  );
}

function strongerCategory(a: OutlookArea['category'], b: OutlookArea['category']): OutlookArea['category'] {
  const ramp: OutlookArea['category'][] = ['TSTM', 'MRGL', 'SLGT', 'ENH', 'MOD', 'HIGH'];
  return ramp.indexOf(b) > ramp.indexOf(a) ? b : a;
}

function withDefaults(p: Partial<Ingredients>): Ingredients {
  return {
    mlcape:           p.mlcape ?? 0,
    mucape:           p.mucape ?? 0,
    sbcape:           p.sbcape ?? 0,
    cin:              p.cin ?? 0,
    sfcDewpointF:     p.sfcDewpointF ?? 50,
    pwatIn:           p.pwatIn ?? 0.8,
    lclM:             p.lclM ?? 1500,
    moistureDepthM:   p.moistureDepthM ?? 1500,
    srh01:            p.srh01 ?? 0,
    srh03:            p.srh03 ?? 0,
    shear06Kt:        p.shear06Kt ?? 0,
    stormRelWindKt:   p.stormRelWindKt ?? 0,
    frontSignal:      (p.frontSignal as SignalStrength) ?? 'none',
    initiationConf:   p.initiationConf ?? 0,
    stormMode:        (p.stormMode as StormMode) ?? 'multicell',
    capStrength:      (p.capStrength as SignalStrength) ?? 'none',
    stp:              p.stp ?? 0,
    scp:              p.scp ?? 0,
    ehi:              p.ehi ?? 0,
    ship:             p.ship ?? 0,
    tornadoComposite: p.tornadoComposite ?? 0,
  };
}
