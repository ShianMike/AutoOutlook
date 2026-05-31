// Core data model for AutoOutlook. Provider-agnostic: shared by mock,
// Open-Meteo, and the Python NOMADS+MetPy backend.

export type RiskCategory = 'TSTM' | 'MRGL' | 'SLGT' | 'ENH' | 'MOD' | 'HIGH';

export type HazardKey = 'tornado' | 'hail' | 'wind' | 'flood';
export type ActiveRegion = 'conus' | 'philippines';
export type PhilippineRegionPane = 'national' | 'luzon' | 'visayas' | 'mindanao';

export type StormMode = 'discrete' | 'multicell' | 'linear' | 'mixed';
export type SignalStrength = 'none' | 'weak' | 'moderate' | 'strong';

export interface Region {
  label: string;             // e.g. "Central Plains" or "OK/KS/TX panhandle"
  centerLat: number;
  centerLon: number;
  bbox: [number, number, number, number]; // [minLon, minLat, maxLon, maxLat]
  states: string[];          // 2-letter postal codes inside the focus area
}

export interface Ingredients {
  // Instability
  mlcape: number;       // J/kg
  mucape: number;       // J/kg
  sbcape: number;       // J/kg
  cin: number;          // J/kg, negative
  // Moisture
  sfcDewpointF: number;
  pwatIn: number;       // inches
  lclM: number;         // meters AGL
  moistureDepthM: number; // PWAT-derived proxy, not a sounding depth
  // Kinematics
  srh01: number;        // m^2/s^2
  srh03: number;        // m^2/s^2
  shear06Kt: number;    // knots, approximated from 10 m to 500 mb winds
  stormRelWindKt: number; // proxy from available wind fields
  // Forcing / storm mode
  frontSignal: SignalStrength;
  initiationConf: number;   // 0-1
  stormMode: StormMode;
  capStrength: SignalStrength;
  // Composite signals
  stp: number;
  scp: number;
  ehi: number;
  ship: number;
  tornadoComposite: number;
}

export interface HazardAssessment {
  level: RiskCategory;
  probability: number;   // 0-1
  confidence: number;    // 0-1
  significantSevere: boolean; // SPC-style: >=10% prob of EF2+ tor / 74mph+ wind / 2"+ hail
  source?: 'rule' | 'ml';
  supporting: string[];
  explanation: string;
}

export interface MlHazardProbabilities {
  tornado: number;
  hail: number;
  wind: number;
}

export interface MlModelMetadata {
  active?: boolean;
  reason?: string;
  version: string;
  trainedAtISO?: string;
  featureSchemaHash: string;
  artifactType?: string;
  featureSchemaVersion?: string;
  trainingRows?: number;
  datasetQuality?: {
    trainingRows?: number;
    minimumRecommendedRows?: number;
    uniqueRunDates?: number | null;
    duplicateFeatureLabelRows?: number;
    positiveCounts?: Partial<Record<'tornado' | 'hail' | 'wind', number>>;
    experimentalOnly?: boolean;
    status?: string;
  };
}

export interface Outlook {
  category: RiskCategory;
  mainHazard: HazardKey;
  confidence: number;     // 0-1
  significantSevere: boolean; // true when any hazard meets SPC SIG threshold
  headline: string;
}

export interface RiskPolygon {
  category: RiskCategory;
  // Outer ring of [lon, lat] points; first/last need not match.
  coords: [number, number][];
  // Optional inner ring (hole) so a category renders as an annulus, not
  // a filled disk - matches the SPC outlook visual convention where each
  // color band is just where THAT category is the highest applicable risk.
  hole?: [number, number][];
}

export interface OutlookArea {
  region: Region;
  category: RiskCategory;
  ingredients?: Partial<Ingredients>;
  hazards?: Partial<Record<HazardKey, HazardAssessment>>;
}

export interface CityMarker {
  name: string;
  lat: number;
  lon: number;
  risk: RiskCategory;
}

export interface UpperAirLine {
  level: '500mb';
  value: number; // geopotential height in meters
  coords: [number, number][];
}

export interface UpperAirVector {
  level: '500mb';
  lon: number;
  lat: number;
  uKt: number;
  vKt: number;
  speedKt: number;
}

export interface UpperAirOverlayMetadata {
  domain: 'CONUS';
  level: '500mb';
  fields: string[];
  gridStride: number;
  windBarbStride?: number;
  source: string;
  hasHeightContours: boolean;
  hasWindVectors: boolean;
  windVectorCount: number;
  heightContourCount: number;
  sourceCycle?: string | null;
  forecastHour: number;
  validTimeISO?: string;
  cacheHit?: boolean;
  cachePath?: string | null;
  error?: string;
}

export interface SurfaceBoundaryFocus {
  kind: 'triple-point' | 'dryline' | 'frontal';
  lat: number;
  lon: number;
  confidence: number;
}

export interface HourSnapshot {
  forecastHour: number;     // 0..48 hourly
  validTimeISO: string;
  region: Region;
  ingredients: Ingredients;
  mlHazards?: MlHazardProbabilities;
  hazards: Record<HazardKey, HazardAssessment>;
  outlook: Outlook;
  outlookAreas?: OutlookArea[];
  riskPolygons: RiskPolygon[];
  cities: CityMarker[];
  upperAirLines?: UpperAirLine[];
  upperAirVectors?: UpperAirVector[];
  upperAirOverlay?: UpperAirOverlayMetadata;
  surfaceBoundary?: SurfaceBoundaryFocus;
}

export type ForecastSource = 'live' | 'fallback' | 'simulated';

export interface ForecastBundle {
  cycle: string;             // "HRRR 12Z 2026-04-30"
  issuedAtISO: string;
  hours: HourSnapshot[];
  source: ForecastSource;
  providerId: 'backend' | 'openMeteo' | 'mock';
  providerNotes: string;
  latencyMs: number;
  fetchedAtISO: string;
  mlHazardHours?: number;
  mlModel?: MlModelMetadata;
}

export interface ProviderResult {
  bundle: ForecastBundle;
}

export interface ForecastProvider {
  id: ForecastBundle['providerId'];
  label: string;
  fetchBundle(signal?: AbortSignal, activeRegion?: ActiveRegion): Promise<ForecastBundle>;
}

// HRRR is hourly. Keep every hour through +48h available to the slider.
export const HRRR_FORECAST_HOURS: number[] = Array.from({ length: 49 }, (_, i) => i);
export const ECMWF_FORECAST_HOURS: number[] = Array.from({ length: 31 }, (_, i) => i * 3); // 0, 3, 6, ..., 90

export const FORECAST_HOURS: number[] = HRRR_FORECAST_HOURS;
export const FORECAST_HOUR_LABELS: Record<number, string> = Object.fromEntries(
  Array.from({ length: 91 }, (_, h) => [h, h === 0 ? 'Current' : `+${h}h`]),
);

// Risk ramp metadata - colors live in tailwind.config.ts.
export const RISK_META: Record<
  RiskCategory,
  { label: string; ord: number; tw: string; chipText: string }
> = {
  TSTM: { label: 'General Thunder', ord: 0, tw: 'bg-risk-tstm text-ink', chipText: 'TSTM' },
  MRGL: { label: 'Marginal',        ord: 1, tw: 'bg-risk-mrgl text-ink', chipText: 'MRGL' },
  SLGT: { label: 'Slight',          ord: 2, tw: 'bg-risk-slgt text-ink', chipText: 'SLGT' },
  ENH:  { label: 'Enhanced',        ord: 3, tw: 'bg-risk-enh text-paper', chipText: 'ENH'  },
  MOD:  { label: 'Moderate',        ord: 4, tw: 'bg-risk-mod text-paper', chipText: 'MOD'  },
  HIGH: { label: 'High',            ord: 5, tw: 'bg-risk-high text-paper', chipText: 'HIGH' },
};

export const HAZARD_META: Record<HazardKey, { label: string; glyph: string }> = {
  tornado: { label: 'Tornado',       glyph: '🌪' },
  hail:    { label: 'Hail',          glyph: '◆' },
  wind:    { label: 'Damaging Wind', glyph: '➤' },
  flood:   { label: 'Flooding',      glyph: '≋' },
};
