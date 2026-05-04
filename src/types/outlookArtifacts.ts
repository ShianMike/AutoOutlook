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

export interface OutlookArtifacts {
  metadata: OutlookArtifactMetadata;
  riskPolygons: OutlookArtifactFeatureCollection;
  aggregateRiskPolygons?: OutlookArtifactFeatureCollection;
}
