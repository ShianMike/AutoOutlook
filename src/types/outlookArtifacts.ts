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
    sourceCellCount?: number;
    cumulativeCellCount?: number;
    componentCount?: number;
    vectorization?: Record<string, unknown>;
  };
}

export interface OutlookArtifactFeatureCollection {
  type: 'FeatureCollection';
  features: OutlookArtifactFeature[];
}

export interface OutlookProbabilityShapeFeature {
  type: 'Feature';
  geometry: {
    type: 'Polygon' | 'MultiPolygon';
    coordinates: number[][][] | number[][][][];
  };
  properties: {
    hazard: 'tornado' | 'hail' | 'wind' | 'thunder' | 'thunderstorm';
    hazardLabel?: string;
    probability: number;
    threshold?: number;
    thresholdPercent?: number;
    bucket: number;
    label: string;
    color: string;
    forecastHour?: number;
    validTimeISO?: string;
    cellCount?: number;
    sourceCellCount?: number;
    componentCount?: number;
    vectorization?: Record<string, unknown>;
  };
}

export interface OutlookProbabilityShapeFeatureCollection {
  type: 'FeatureCollection';
  features: OutlookProbabilityShapeFeature[];
}

export interface OutlookCycleCheck {
  runDate?: string;
  runCycle?: number;
  cycleTimeISO?: string;
  label?: string;
  complete?: boolean;
  hours?: Array<{
    forecastHour?: number;
    idxAvailable?: boolean;
    requiredFieldsPresent?: boolean;
    statusCode?: number;
    error?: string;
  }>;
}

export interface OutlookCyclePolicy {
  name?: 'complete-requested' | 'complete-48' | 'latest-startable' | string;
  model?: string;
  allowedRunCyclesUTC?: number[];
  requestedForecastHours?: number[];
  requiredForecastHourForCycle?: number;
  requiredForecastHoursChecked?: number[];
  requireCompleteHourOverride?: number | null;
  description?: string;
}

export interface OutlookArtifactMetadata {
  generatedAtISO: string;
  cycle: string;
  cycleTimeISO?: string;
  cycleMetadata?: OutlookCycleCheck | null;
  latestExtendedCandidate?: OutlookCycleCheck | null;
  selectedCycleWasFallback?: boolean;
  fallbackReason?: string | null;
  requiredForecastHourForCycle?: number;
  requiredForecastHoursChecked?: number[];
  requestedForecastHours?: number[];
  checkedCycles?: OutlookCycleCheck[];
  cyclePolicy?: OutlookCyclePolicy;
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
  riskShapes?: OutlookArtifactFeatureCollection;
  hazardProbabilityShapes?: OutlookProbabilityShapeFeatureCollection;
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
