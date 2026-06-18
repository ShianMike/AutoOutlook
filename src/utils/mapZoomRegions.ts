// Region zoom presets for the outlook maps.
//
// All maps use a `geoAlbers` projection with `rotate: [96, 0, 0]`. In that
// rotated frame the projection `center[0]` (longitude) equals the geographic
// longitude plus 96 (geographic -96° maps to rotated 0°). `center[1]` is just
// the geographic latitude. We store human-friendly geographic centers here and
// convert to the projection frame in `zoomRegionProjectionConfig`.

export type MapZoomRegion =
  | 'conus'
  | 'northeast'
  | 'southeast'
  | 'midwest'
  | 'southernPlains'
  | 'northernPlains'
  | 'southwest'
  | 'northwest'
  | 'west';

export interface ZoomRegionDef {
  label: string;
  /** Geographic center [lon, lat]. */
  center: [number, number];
  /** geoAlbers scale (larger = more zoomed-in). */
  scale: number;
}

// Base meridian baked into the maps' `rotate: [96, 0, 0]`.
const ROTATE_LON = 96;

// CONUS keeps the original "zoomed a bit" framing; regional presets zoom in ~2x.
export const MAP_ZOOM_REGIONS: Record<MapZoomRegion, ZoomRegionDef> = {
  conus: { label: 'CONUS (Full)', center: [-96, 38], scale: 1150 },
  northeast: { label: 'Northeast', center: [-75, 42.5], scale: 2800 },
  southeast: { label: 'Southeast', center: [-83, 32.5], scale: 2500 },
  midwest: { label: 'Midwest / Ohio Valley', center: [-88, 40], scale: 2500 },
  southernPlains: { label: 'Southern Plains', center: [-98, 33.5], scale: 2400 },
  northernPlains: { label: 'Northern Plains', center: [-100, 46], scale: 2400 },
  southwest: { label: 'Southwest', center: [-108, 34], scale: 2400 },
  northwest: { label: 'Northwest', center: [-118, 45], scale: 2400 },
  west: { label: 'California / West', center: [-120, 38], scale: 2400 },
};

export const MAP_ZOOM_REGION_ORDER: MapZoomRegion[] = [
  'conus',
  'northeast',
  'southeast',
  'midwest',
  'southernPlains',
  'northernPlains',
  'southwest',
  'northwest',
  'west',
];

export interface MapProjectionConfig {
  rotate: [number, number, number];
  center: [number, number];
  parallels: [number, number];
  scale: number;
}

/**
 * Build the `projectionConfig` for the outlook maps for a given zoom region.
 * Falls back to the CONUS framing when the region is unknown.
 */
export function zoomRegionProjectionConfig(region: MapZoomRegion = 'conus'): MapProjectionConfig {
  const def = MAP_ZOOM_REGIONS[region] ?? MAP_ZOOM_REGIONS.conus;
  const [lon, lat] = def.center;
  return {
    rotate: [ROTATE_LON, 0, 0],
    center: [lon + ROTATE_LON, lat],
    parallels: [29.5, 45.5],
    scale: def.scale,
  };
}
