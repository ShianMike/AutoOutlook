// Deterministic mock forecast bundle. Models a classic late-spring
// Plains severe-weather day so the dashboard has compelling content
// even without live data.

import type {
  CityMarker,
  ForecastBundle,
  HourSnapshot,
  Ingredients,
  Region,
} from '../types/forecast';
import { FORECAST_HOURS } from '../types/forecast';
import { buildOutlook } from './outlookEngine';
import { buildHazards } from './hazardEngine';
import { buildRiskPolygons } from './polygonBuilder';

const REGION: Region = {
  label: 'Central Plains — OK / KS / TX panhandle',
  centerLat: 36.4,
  centerLon: -98.5,
  bbox: [-104, 32, -94, 41],
  states: ['OK', 'KS', 'TX', 'AR', 'MO'],
};

const CITIES: { name: string; lat: number; lon: number }[] = [
  { name: 'Norman',        lat: 35.22, lon: -97.44 },
  { name: 'Oklahoma City', lat: 35.47, lon: -97.52 },
  { name: 'Wichita',       lat: 37.69, lon: -97.34 },
  { name: 'Tulsa',         lat: 36.15, lon: -95.99 },
  { name: 'Amarillo',      lat: 35.22, lon: -101.83 },
  { name: 'Dallas',        lat: 32.78, lon: -96.80 },
  { name: 'Topeka',        lat: 39.05, lon: -95.68 },
  { name: 'Springfield',   lat: 37.21, lon: -93.30 },
  { name: 'Lubbock',       lat: 33.58, lon: -101.85 },
  { name: 'Joplin',        lat: 37.08, lon: -94.51 },
];

function regionForIndex(index: number): Region {
  const eastDrift = Math.min(index, 10) * 0.55;
  const northDrift = Math.sin(index * 0.7) * 0.45;
  const centerLon = REGION.centerLon - 1.5 + eastDrift;
  const centerLat = REGION.centerLat + northDrift;
  return {
    ...REGION,
    centerLat,
    centerLon,
    bbox: [centerLon - 5.5, centerLat - 3.2, centerLon + 5.5, centerLat + 3.2],
  };
}

// Per-stop ingredient profile. Designed to evolve from a quiet morning
// through a peak afternoon supercell window into an evening MCS.
const PROFILES: Array<Omit<Ingredients,
  'stp' | 'scp' | 'ehi' | 'ship' | 'tornadoComposite'
>> = [
  // 0h - early morning, capped, low CAPE
  { mlcape: 600,  mucape: 900,  sbcape: 500,  cin: -180,
    sfcDewpointF: 60, pwatIn: 1.05, lclM: 1500, moistureDepthM: 1800,
    srh01: 80,  srh03: 150, shear06Kt: 30, stormRelWindKt: 22,
    frontSignal: 'weak', initiationConf: 0.20, stormMode: 'multicell', capStrength: 'strong' },
  // +3h - cap maximum
  { mlcape: 800, mucape: 1100, sbcape: 700, cin: -200,
    sfcDewpointF: 62, pwatIn: 1.15, lclM: 1450, moistureDepthM: 2000,
    srh01: 90, srh03: 170, shear06Kt: 32, stormRelWindKt: 24,
    frontSignal: 'weak', initiationConf: 0.30, stormMode: 'multicell', capStrength: 'strong' },
  // +6h - heating begins, cap eroding
  { mlcape: 1500, mucape: 1900, sbcape: 1300, cin: -100,
    sfcDewpointF: 65, pwatIn: 1.30, lclM: 1300, moistureDepthM: 2400,
    srh01: 110, srh03: 200, shear06Kt: 35, stormRelWindKt: 26,
    frontSignal: 'moderate', initiationConf: 0.50, stormMode: 'mixed', capStrength: 'moderate' },
  // +9h - initiation expected
  { mlcape: 2300, mucape: 2700, sbcape: 2100, cin: -45,
    sfcDewpointF: 67, pwatIn: 1.45, lclM: 1100, moistureDepthM: 2900,
    srh01: 170, srh03: 260, shear06Kt: 40, stormRelWindKt: 30,
    frontSignal: 'strong', initiationConf: 0.75, stormMode: 'discrete', capStrength: 'weak' },
  // +12h - peak severe window
  { mlcape: 2800, mucape: 3200, sbcape: 2600, cin: -20,
    sfcDewpointF: 69, pwatIn: 1.55, lclM: 950,  moistureDepthM: 3200,
    srh01: 230, srh03: 330, shear06Kt: 45, stormRelWindKt: 34,
    frontSignal: 'strong', initiationConf: 0.88, stormMode: 'discrete', capStrength: 'none' },
  // +18h - upscale growth, transitioning to MCS
  { mlcape: 1900, mucape: 2400, sbcape: 1600, cin: -30,
    sfcDewpointF: 68, pwatIn: 1.65, lclM: 1050, moistureDepthM: 3400,
    srh01: 180, srh03: 300, shear06Kt: 38, stormRelWindKt: 32,
    frontSignal: 'moderate', initiationConf: 0.70, stormMode: 'linear', capStrength: 'none' },
  // +24h - overnight MCS winding down, flood/wind concern
  { mlcape: 900,  mucape: 1400, sbcape: 700, cin: -90,
    sfcDewpointF: 66, pwatIn: 1.60, lclM: 1200, moistureDepthM: 3300,
    srh01: 110, srh03: 220, shear06Kt: 30, stormRelWindKt: 26,
    frontSignal: 'weak', initiationConf: 0.45, stormMode: 'linear', capStrength: 'weak' },
];

import { fillIngredientComposites } from './ingredientsDerive';
import { applyLeadTimeUncertainty } from './leadTimeUncertainty';

function buildHourSnapshot(
  hour: number,
  baseISO: string,
  index: number,
): HourSnapshot {
  const ing: Ingredients = fillIngredientComposites(PROFILES[Math.min(index, PROFILES.length - 1)]);
  const hazards = buildHazards(ing);
  const outlook = buildOutlook(ing, hazards);
  const region = regionForIndex(index);
  const validTimeISO = new Date(new Date(baseISO).getTime() + hour * 3600_000).toISOString();
  const cities: CityMarker[] = CITIES.map((c) => {
    // Distance from region center -> downgrade risk away from core.
    const dLat = Math.abs(c.lat - region.centerLat);
    const dLon = Math.abs(c.lon - region.centerLon);
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
    forecastHour: hour,
    validTimeISO,
    region,
    ingredients: ing,
    hazards,
    outlook,
    riskPolygons: buildRiskPolygons(region, outlook.category),
    cities,
  });
}

export function buildMockBundle(now: Date = new Date()): ForecastBundle {
  // Snap "issued" cycle to most recent 6-hour synoptic time for realism.
  const cycleHourUTC = Math.floor(now.getUTCHours() / 6) * 6;
  const issued = new Date(Date.UTC(
    now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(),
    cycleHourUTC, 0, 0, 0,
  ));
  const issuedISO = issued.toISOString();
  const cycleStr = `HRRR ${String(cycleHourUTC).padStart(2, '0')}Z ${issuedISO.slice(0, 10)}`;
  const hours = FORECAST_HOURS.map((h, i) => buildHourSnapshot(h, issuedISO, i));
  return {
    cycle: cycleStr,
    issuedAtISO: issuedISO,
    hours,
    source: 'simulated',
    providerId: 'mock',
    providerNotes: 'Deterministic mock dataset (Central Plains spring severe day)',
    latencyMs: 12,
    fetchedAtISO: now.toISOString(),
  };
}
