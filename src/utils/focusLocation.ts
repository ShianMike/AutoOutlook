import type { HourSnapshot, Region } from '../types/forecast';
import { displayRegionLabel } from './regionDisplay';

export interface FocusLocation {
  label: string;
  coord: string;
  states: string;
  usesCoordinateLabel: boolean;
}

export function formatFocusCoord(lat: number | undefined, lon: number | undefined): string {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return 'coords pending';
  const safeLat = Number(lat);
  const safeLon = Number(lon);
  const ns = safeLat >= 0 ? 'N' : 'S';
  const ew = safeLon >= 0 ? 'E' : 'W';
  return `${Math.abs(safeLat).toFixed(1)}${ns} ${Math.abs(safeLon).toFixed(1)}${ew}`;
}

export function focusLocationFromRegion(region: Region | null | undefined): FocusLocation {
  if (!region) {
    return {
      label: 'Location pending',
      coord: 'coords pending',
      states: '',
      usesCoordinateLabel: false,
    };
  }

  const coord = formatFocusCoord(region.centerLat, region.centerLon);
  const displayLabel = displayRegionLabel(region.label, 'Highlighted corridor');
  const usesCoordinateLabel = displayLabel.toLowerCase() === 'highlighted corridor';
  const states = region.states.length > 0 ? region.states.join(' / ') : '';
  return {
    label: usesCoordinateLabel ? coord : displayLabel,
    coord,
    states,
    usesCoordinateLabel,
  };
}

export function focusLocationFromSnapshot(snapshot: HourSnapshot | null | undefined): FocusLocation {
  return focusLocationFromRegion(snapshot?.region);
}
