export type ArtifactRiskCategory = 'NONE' | 'TSTM' | 'MRGL' | 'SLGT' | 'ENH' | 'MDT' | 'MOD' | 'HIGH';

export interface OutlookArtifactFeature {
  type: 'Feature';
  geometry: {
    type: 'Polygon' | 'MultiPolygon';
    coordinates: number[][][] | number[][][][];
  };
  properties: {
    category: ArtifactRiskCategory;
    ordinal?: number;
    forecastHour: number;
    validTimeISO: string;
    component?: number;
    cellCount?: number;
  };
}

export interface OutlookArtifactFeatureCollection {
  type: 'FeatureCollection';
  features: OutlookArtifactFeature[];
}

export interface OutlookArtifactMetadata {
  generatedAtISO: string;
  cycle: string;
  cycleTimeISO?: string;
  selectedArtifactForecastHour?: number;
  artifactForecastHour?: number;
  artifactValidTimeISO?: string;
  forecastHours?: number[];
  featureSchemaHash?: string;
  selectedHrrrTerms?: string[];
  aggregateCategoryCounts?: Record<string, number>;
  categoryCounts?: Record<string, number>;
  artifacts?: Record<string, string | null>;
  spcVerification?: {
    agreementFraction?: number | null;
    underforecastCells?: number;
    overforecastCells?: number;
    spcIssueTimeISO?: string;
    spcValidTimeISO?: string;
    spcExpireTimeISO?: string;
    leakageGuard?: string;
  } | null;
  mode?: 'full' | 'incremental';
  status?: 'running' | 'complete' | 'partial' | 'failed';
  readyForecastHours?: number[];
  failedForecastHours?: number[];
  pendingForecastHours?: number[];
}

export interface OutlookProbabilityTile {
  forecastHour: number;
  validTimeISO: string;
  stride: number;
  shape: [number, number] | number[];
  lats: number[][];
  lons: number[][];
  categoryOrdinal: number[][];
  categoryLabel: ArtifactRiskCategory[][];
  probabilities: {
    tornado: number[][];
    hail: number[][];
    wind: number[][];
  };
}

export interface OutlookProbabilityHour {
  forecastHour: number;
  validTimeISO: string;
  categoryCounts?: Record<string, number>;
  tile: OutlookProbabilityTile;
}

export interface OutlookProbabilityTiles {
  cycle: string;
  featureSchemaHash?: string;
  riskLabels?: ArtifactRiskCategory[];
  gridStride?: number;
  tileStride?: number;
  environmentalCapsApplied?: boolean;
  categoryConsistencyCapsApplied?: boolean;
  hours: OutlookProbabilityHour[];
}

export interface OutlookArtifacts {
  metadata: OutlookArtifactMetadata;
  riskPolygons: OutlookArtifactFeatureCollection;
  aggregateRiskPolygons?: OutlookArtifactFeatureCollection;
  probabilityTiles?: OutlookProbabilityTiles;
  timelineSummary?: OutlookIncrementalSummary;
  incrementalIndex?: OutlookIncrementalIndex;
  selectedArtifactForecastHour?: number;
  selectedHourStatus?: 'ready' | 'pending' | 'failed' | 'missing';
}

export interface OutlookIncrementalIndex extends OutlookArtifactMetadata {
  mode: 'incremental';
  requestedForecastHours: number[];
  readyForecastHours: number[];
  failedForecastHours: number[];
  pendingForecastHours: number[];
  latestReadyForecastHour?: number | null;
  status: 'running' | 'complete' | 'partial' | 'failed';
  failedHours?: Array<{ forecastHour: number; stage?: string; error?: string }>;
  gridStride?: number;
  tileStride?: number;
}

export interface OutlookTimelineHourSummary {
  forecastHour: number;
  validTimeISO?: string;
  category: ArtifactRiskCategory;
  mainHazard?: 'tornado' | 'hail' | 'wind' | null;
  peakHazardProbability: number;
  significantSevere: boolean;
  coverage: number;
  categoryCounts?: Record<string, number>;
  probabilityMax?: Partial<Record<'tornado' | 'hail' | 'wind', number>>;
}

export interface OutlookIncrementalSummary {
  cycle?: string;
  cycleTimeISO?: string;
  generatedAtISO?: string;
  hours: OutlookTimelineHourSummary[];
}
