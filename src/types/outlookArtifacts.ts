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
  forecastHours?: number[];
  featureSchemaHash?: string;
  selectedHrrrTerms?: string[];
  aggregateCategoryCounts?: Record<string, number>;
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
  hours: OutlookProbabilityHour[];
}

export interface OutlookArtifacts {
  metadata: OutlookArtifactMetadata;
  riskPolygons: OutlookArtifactFeatureCollection;
  aggregateRiskPolygons?: OutlookArtifactFeatureCollection;
  probabilityTiles?: OutlookProbabilityTiles;
}
