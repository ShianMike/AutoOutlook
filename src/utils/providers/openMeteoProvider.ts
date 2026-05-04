// Browser-side fallback provider. Hits Open-Meteo's free GFS endpoint
// for raw fields, then derives ingredients + runs the standard engines.

import type {
  CityMarker,
  ForecastBundle,
  ForecastProvider,
  HourSnapshot,
  Ingredients,
  Region,
  SignalStrength,
  StormMode,
} from '../../types/forecast';
import { FORECAST_HOURS } from '../../types/forecast';
import { buildOutlook } from '../outlookEngine';
import { buildHazards } from '../hazardEngine';
import { buildRiskPolygons } from '../polygonBuilder';
import {
  fillIngredientComposites,
  deriveStormMode,
  deriveCapStrength,
} from '../ingredientsDerive';
import { applyLeadTimeUncertainty } from '../leadTimeUncertainty';

// Sample grid across CONUS - kept small so a single browser doesn't burn
// through Open-Meteo's free quota.
const SAMPLE_POINTS: { name: string; lat: number; lon: number; states: string[] }[] = [
  { name: 'Central Plains',     lat: 36.0,  lon: -98.0,  states: ['OK', 'KS', 'TX'] },
  { name: 'Mid-South',          lat: 35.0,  lon: -90.0,  states: ['AR', 'TN', 'MS'] },
  { name: 'Southern Plains',    lat: 32.5,  lon: -98.0,  states: ['TX', 'LA'] },
  { name: 'Midwest',            lat: 41.5,  lon: -89.0,  states: ['IL', 'IA', 'MO'] },
  { name: 'Southeast',          lat: 33.0,  lon: -85.0,  states: ['AL', 'GA', 'FL'] },
];

const ENDPOINT = 'https://api.open-meteo.com/v1/gfs';
const OPEN_METEO_CACHE_MS = 30 * 60 * 1000;
const OPEN_METEO_RATE_LIMIT_COOLDOWN_MS = 60 * 60 * 1000;

let cachedBundle: ForecastBundle | null = null;
let cacheExpiresAt = 0;
let rateLimitCooldownUntil = 0;

// Hours to request (matches FORECAST_HOURS).
const HOURS = FORECAST_HOURS;

// Open-Meteo "hourly" variables we need.
// NOTE: Open-Meteo's GFS endpoint exposes precipitable water as
// `total_column_integrated_water_vapour` (kg/m²), NOT `precipitable_water`.
const HOURLY_VARS = [
  'cape',
  'convective_inhibition',
  'dewpoint_2m',
  'total_column_integrated_water_vapour',
  'lifted_index',
  'wind_speed_10m',
  'wind_direction_10m',
  'wind_speed_500hPa',
  'wind_direction_500hPa',
  'wind_speed_850hPa',
  'wind_direction_850hPa',
  'wind_speed_250hPa',
  'wind_direction_250hPa',
];

interface PointResp {
  time: string[];
  cape: number[];
  convective_inhibition: number[];
  dewpoint_2m: number[];
  total_column_integrated_water_vapour: number[];
  lifted_index: number[];
  wind_speed_10m: number[];
  wind_direction_10m: number[];
  wind_speed_500hPa: number[];
  wind_direction_500hPa: number[];
  wind_speed_850hPa: number[];
  wind_direction_850hPa: number[];
  wind_speed_250hPa: number[];
  wind_direction_250hPa: number[];
}

interface ApiResponse {
  latitude: number;
  longitude: number;
  hourly: PointResp;
}

class OpenMeteoRateLimitError extends Error {
  constructor() {
    super('Open-Meteo HTTP 429; cooling down fallback provider');
  }
}

async function fetchPoint(
  lat: number,
  lon: number,
  signal?: AbortSignal,
): Promise<ApiResponse> {
  const url = new URL(ENDPOINT);
  url.searchParams.set('latitude', String(lat));
  url.searchParams.set('longitude', String(lon));
  url.searchParams.set('hourly', HOURLY_VARS.join(','));
  url.searchParams.set('windspeed_unit', 'kn');
  url.searchParams.set('temperature_unit', 'fahrenheit');
  url.searchParams.set('forecast_days', '3');
  const res = await fetch(url.toString(), { signal });
  if (res.status === 429) throw new OpenMeteoRateLimitError();
  if (!res.ok) throw new Error(`Open-Meteo HTTP ${res.status}`);
  return (await res.json()) as ApiResponse;
}

async function fetchSamplePoints(signal?: AbortSignal): Promise<ApiResponse[]> {
  const responses: ApiResponse[] = [];
  for (const point of SAMPLE_POINTS) {
    responses.push(await fetchPoint(point.lat, point.lon, signal));
  }
  return responses;
}

function uvFromSpdDir(spdKt: number, dirDeg: number): [number, number] {
  // Meteorological convention: dir is *from*. Vector "to" = dir + 180.
  const to = ((dirDeg + 180) % 360) * Math.PI / 180;
  return [spdKt * Math.sin(to), spdKt * Math.cos(to)];
}

function bulkShearKt(u1: number, v1: number, u2: number, v2: number): number {
  const du = u2 - u1;
  const dv = v2 - v1;
  return Math.sqrt(du * du + dv * dv);
}

function srhProxy(uSfc: number, vSfc: number, u850: number, v850: number): number {
  // Crude SRH surrogate: shear vector magnitude * mean wind component
  // perpendicular to it, using Bunkers-ish right-mover deviate.
  const du = u850 - uSfc;
  const dv = v850 - vSfc;
  const shr = Math.sqrt(du * du + dv * dv);
  // Storm motion ~ 75% of mean wind, deviated 30deg right
  const um = (uSfc + u850) / 2;
  const vm = (vSfc + v850) / 2;
  const ang = Math.atan2(vm, um) - (30 * Math.PI / 180);
  const sm = Math.sqrt(um * um + vm * vm) * 0.75;
  const usm = sm * Math.cos(ang);
  const vsm = sm * Math.sin(ang);
  // SRH ~ shear x storm-relative wind cross product magnitude (very rough).
  const cross = du * (vSfc - vsm) - dv * (uSfc - usm);
  return Math.max(0, cross * 0.6);
}

function snapshotFromPoint(
  hourIdx: number,
  hour: number,
  baseISO: string,
  resp: ApiResponse,
  region: Region,
): HourSnapshot | null {
  const h = resp.hourly;
  // Find index of the requested forecast hour in hourly time series.
  const baseDate = new Date(baseISO);
  const targetISO = new Date(baseDate.getTime() + hour * 3600_000).toISOString().slice(0, 13);
  const idx = h.time.findIndex((t) => t.startsWith(targetISO));
  if (idx < 0) return null;

  const cape = h.cape[idx] ?? 0;
  const cin = h.convective_inhibition[idx] ?? 0;
  const tdF = h.dewpoint_2m[idx] ?? 50;
  // total_column_integrated_water_vapour is in kg/m^2 (= mm of water).
  const pwatMm = h.total_column_integrated_water_vapour[idx] ?? 25;
  const pwatIn = pwatMm / 25.4;
  const li = h.lifted_index[idx] ?? 0;

  const [uSfc, vSfc] = uvFromSpdDir(h.wind_speed_10m[idx], h.wind_direction_10m[idx]);
  const [u850, v850] = uvFromSpdDir(h.wind_speed_850hPa[idx], h.wind_direction_850hPa[idx]);
  const [u500, v500] = uvFromSpdDir(h.wind_speed_500hPa[idx], h.wind_direction_500hPa[idx]);

  const shear06Kt = bulkShearKt(uSfc, vSfc, u500, v500);
  const srh01 = srhProxy(uSfc, vSfc, u850, v850);
  const srh03 = srh01 * 1.4;
  const stormRelWindKt = Math.sqrt(u500 * u500 + v500 * v500) * 0.4;

  // Approximate LCL height via Td depression (T_2m not requested, but estimate from LI bias).
  const lclM = Math.max(400, 1500 - Math.max(0, tdF - 50) * 25);

  const frontSignal: SignalStrength =
    li < -6 ? 'strong' : li < -3 ? 'moderate' : li < 0 ? 'weak' : 'none';
  const initiationConf = Math.max(0, Math.min(1, (-li) / 8 + Math.min(cape / 2500, 1) * 0.4));

  const capStrength = deriveCapStrength(cin);
  const stormMode: StormMode = deriveStormMode({ shear06Kt, srh03, frontStrength: frontSignal });

  const baseIng: Omit<Ingredients, 'stp' | 'scp' | 'ehi' | 'ship' | 'tornadoComposite'> = {
    mlcape: cape * 0.85,
    mucape: cape,
    sbcape: cape * 1.05,
    cin,
    sfcDewpointF: tdF,
    pwatIn,
    lclM,
    moistureDepthM: Math.max(800, pwatIn * 1500),
    srh01,
    srh03,
    shear06Kt,
    stormRelWindKt,
    frontSignal,
    initiationConf,
    stormMode,
    capStrength,
  };
  const ingredients = fillIngredientComposites(baseIng);
  const hazards = buildHazards(ingredients);
  const outlook = buildOutlook(ingredients, hazards);
  const validTimeISO = new Date(baseDate.getTime() + hour * 3600_000).toISOString();

  // City list: just the region center as a fallback marker.
  const cities: CityMarker[] = [
    { name: region.label.split('—')[0].trim(), lat: region.centerLat, lon: region.centerLon, risk: outlook.category },
  ];

  return applyLeadTimeUncertainty({
    forecastHour: hour,
    validTimeISO,
    region,
    ingredients,
    hazards,
    outlook,
    riskPolygons: buildRiskPolygons(region, outlook.category),
    cities,
  });
  // Note: hourIdx unused but kept for future interpolation.
  void hourIdx;
}

function scorePointAtHour(resp: ApiResponse, baseISO: string, hour: number): number {
  const h = resp.hourly;
  const baseDate = new Date(baseISO);
  const targetISO = new Date(baseDate.getTime() + hour * 3600_000).toISOString().slice(0, 13);
  const idx = h.time.findIndex((t) => t.startsWith(targetISO));
  if (idx < 0) return 0;

  const cape = h.cape[idx] ?? 0;
  const cin = h.convective_inhibition[idx] ?? 0;
  const td = h.dewpoint_2m[idx] ?? 50;
  const li = h.lifted_index[idx] ?? 0;
  const [uSfc, vSfc] = uvFromSpdDir(
    h.wind_speed_10m[idx] ?? 0,
    h.wind_direction_10m[idx] ?? 0,
  );
  const [u500, v500] = uvFromSpdDir(
    h.wind_speed_500hPa[idx] ?? 0,
    h.wind_direction_500hPa[idx] ?? 0,
  );
  const shear = bulkShearKt(uSfc, vSfc, u500, v500);
  const capPenalty = cin <= -200 ? 0.25 : cin <= -100 ? 0.55 : cin <= -50 ? 0.8 : 1;
  const init = Math.max(0.2, Math.min(1, (-li) / 8 + Math.min(cape / 2500, 1) * 0.4));
  return (cape / 2000) * (shear / 30) * (Math.max(0, td - 50) / 15) * capPenalty * init;
}

function regionFromPoint(p: typeof SAMPLE_POINTS[number]): Region {
  return {
    label: p.name,
    centerLat: p.lat,
    centerLon: p.lon,
    bbox: [p.lon - 5, p.lat - 3, p.lon + 5, p.lat + 3],
    states: p.states,
  };
}

export const openMeteoProvider: ForecastProvider = {
  id: 'openMeteo',
  label: 'Open-Meteo GFS',
  async fetchBundle(signal?: AbortSignal): Promise<ForecastBundle> {
    const nowMs = Date.now();
    if (cachedBundle && nowMs < cacheExpiresAt) return cachedBundle;
    if (nowMs < rateLimitCooldownUntil) {
      throw new Error('Open-Meteo fallback is cooling down after rate limiting');
    }

    const t0 = performance.now();
    let responses: ApiResponse[];
    try {
      responses = await fetchSamplePoints(signal);
    } catch (err) {
      if (err instanceof OpenMeteoRateLimitError) {
        rateLimitCooldownUntil = Date.now() + OPEN_METEO_RATE_LIMIT_COOLDOWN_MS;
      }
      throw err;
    }

    const baseDate = new Date();
    const baseISO = baseDate.toISOString();
    const hours: HourSnapshot[] = [];
    HOURS.forEach((h, i) => {
      const scored = responses
        .map((resp, idx) => ({ idx, score: scorePointAtHour(resp, baseISO, h) }))
        .sort((a, b) => b.score - a.score);
      const winnerIdx = scored[0]?.idx ?? 0;
      const region = regionFromPoint(SAMPLE_POINTS[winnerIdx]);
      const snap = snapshotFromPoint(i, h, baseISO, responses[winnerIdx], region);
      if (snap) hours.push(snap);
    });

    if (hours.length === 0) throw new Error('Open-Meteo returned no usable hours');

    // Cycle string: latest 6Z synoptic
    const cycleHourUTC = Math.floor(baseDate.getUTCHours() / 6) * 6;
    const issued = new Date(Date.UTC(
      baseDate.getUTCFullYear(), baseDate.getUTCMonth(), baseDate.getUTCDate(),
      cycleHourUTC, 0, 0, 0,
    ));
    const t1 = performance.now();

    const bundle: ForecastBundle = {
      cycle: `GFS ${String(cycleHourUTC).padStart(2, '0')}Z ${issued.toISOString().slice(0, 10)}`,
      issuedAtISO: issued.toISOString(),
      hours,
      source: 'live',
      providerId: 'openMeteo',
      providerNotes: 'Open-Meteo GFS — moving hourly focus region',
      latencyMs: Math.round(t1 - t0),
      fetchedAtISO: baseDate.toISOString(),
    };
    cachedBundle = bundle;
    cacheExpiresAt = Date.now() + OPEN_METEO_CACHE_MS;
    return bundle;
  },
};
