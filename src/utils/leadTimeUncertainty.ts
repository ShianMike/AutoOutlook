import type { HazardKey, HourSnapshot } from '../types/forecast';

function leadConfidenceMultiplier(forecastHour: number): number {
  // Confidence should decay as lead time grows. The 0-48h HRRR range remains
  // useful, but later periods should visibly carry lower certainty.
  return Math.max(0.35, 1 - forecastHour / 180);
}

export function applyLeadTimeUncertainty(snapshot: HourSnapshot): HourSnapshot {
  const mult = leadConfidenceMultiplier(snapshot.forecastHour);
  const hazards = { ...snapshot.hazards };

  (Object.keys(hazards) as HazardKey[]).forEach((key) => {
    hazards[key] = {
      ...hazards[key],
      confidence: Math.max(0.1, hazards[key].confidence * mult),
    };
  });

  return {
    ...snapshot,
    hazards,
    outlook: {
      ...snapshot.outlook,
      confidence: Math.max(0.1, snapshot.outlook.confidence * mult),
    },
  };
}
