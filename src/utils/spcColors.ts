// Official SPC outlook color palettes.
//
// These mirror the Storm Prediction Center's published categorical and
// probabilistic outlook colors so AutoOutlook's categorical and hazard maps
// render in the same colors SPC uses. Hazard palettes are indexed by threshold
// bucket (ascending probability), matching the threshold order used across the
// backend and frontend.

/** SPC categorical risk fills + darker outline strokes. */
export const SPC_CATEGORICAL: Record<
  'TSTM' | 'MRGL' | 'SLGT' | 'ENH' | 'MDT' | 'HIGH',
  { fill: string; stroke: string }
> = {
  TSTM: { fill: '#C1E9C1', stroke: '#3C8A3C' },
  MRGL: { fill: '#66A366', stroke: '#3C7A3C' },
  SLGT: { fill: '#FFE066', stroke: '#E6C200' },
  ENH:  { fill: '#FFA366', stroke: '#E07B2C' },
  MDT:  { fill: '#E06666', stroke: '#C02C2C' },
  HIGH: { fill: '#EE99EE', stroke: '#B84BB8' },
};

/**
 * SPC probabilistic hazard colors by ascending threshold bucket.
 * - tornado: 2/5/10/15/30/45/60%
 * - hail & wind: 5/15/30/45/60%
 * - thunder is not an SPC probabilistic product; it uses an SPC-green family.
 */
export const SPC_HAZARD_COLORS: Record<'tornado' | 'hail' | 'wind' | 'thunder', string[]> = {
  tornado: ['#008B00', '#8B4726', '#FFC800', '#FF0000', '#FF00FF', '#912CEE', '#104E8B'],
  hail:    ['#8B4726', '#FFC800', '#FF0000', '#FF00FF', '#912CEE'],
  wind:    ['#8B4726', '#FFC800', '#FF0000', '#FF00FF', '#912CEE'],
  thunder: ['#C1E9C1', '#66A366', '#FFE066', '#FFA366'],
};

/** Resolve the SPC color for a hazard at a given threshold bucket index. */
export function spcHazardColor(
  hazard: 'tornado' | 'hail' | 'wind' | 'thunder',
  bucket: number,
): string {
  const ramp = SPC_HAZARD_COLORS[hazard] ?? SPC_HAZARD_COLORS.wind;
  const index = Math.max(0, Math.min(ramp.length - 1, Math.trunc(bucket) || 0));
  return ramp[index];
}
