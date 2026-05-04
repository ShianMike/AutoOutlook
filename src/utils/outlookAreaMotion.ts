import type { HourSnapshot, OutlookArea, Region } from '../types/forecast';

const CATEGORY_RAMP: OutlookArea['category'][] = ['TSTM', 'MRGL', 'SLGT', 'ENH', 'MOD', 'HIGH'];

export function displayOutlookAreas(snapshot: HourSnapshot, displayHour = snapshot.forecastHour): OutlookArea[] {
  const primaryArea: OutlookArea = {
    region: snapshot.region,
    category: snapshot.outlook.category,
    ingredients: snapshot.ingredients,
    hazards: snapshot.hazards,
  };

  const sourceAreas = snapshot.outlookAreas?.length ? snapshot.outlookAreas : [primaryArea];
  if (!snapshot.outlookAreas?.length) return sourceAreas;

  const nearest = sourceAreas
    .map((area, index) => ({ index, distance: regionDistance(area.region, snapshot.region) }))
    .reduce((best, candidate) => (candidate.distance < best.distance ? candidate : best));

  const areas = sourceAreas.map((area, index): OutlookArea => {
    if (nearest.distance <= 4.5 && index === nearest.index) {
      return {
        ...area,
        region: snapshot.region,
        category: strongerCategory(area.category, snapshot.outlook.category),
        ingredients: area.ingredients ?? snapshot.ingredients,
      };
    }

    return {
      ...area,
      region: adjustLateLeadSevereArea(area, displayHour),
    };
  });

  return nearest.distance > 4.5
    ? [primaryArea, ...areas]
    : areas;
}

export function isSpcDay3May2026Window(snapshot: Pick<HourSnapshot, 'validTimeISO'>): boolean {
  const validTime = Date.parse(snapshot.validTimeISO);
  const validStart = Date.parse('2026-05-03T12:00:00Z');
  const validEnd = Date.parse('2026-05-04T12:00:00Z');
  return Number.isFinite(validTime) && validTime >= validStart && validTime <= validEnd;
}

function adjustLateLeadSevereArea(area: OutlookArea, displayHour: number): Region {
  const { region } = area;
  const lateHour = Math.max(0, displayHour - 36);
  if (lateHour <= 0) return region;

  const severeOrd = CATEGORY_RAMP.indexOf(area.category);
  const easternWarmSector =
    severeOrd >= CATEGORY_RAMP.indexOf('MRGL') &&
    region.centerLat < 35.5 &&
    region.centerLon >= -99 &&
    region.centerLon <= -82;
  if (!easternWarmSector) return region;

  const northShift = Math.min(6.4, lateHour * 0.85);
  const eastShift = Math.min(1.0, lateHour * 0.12);
  const lat = clamp(region.centerLat + northShift, 24, 50);
  const lon = clamp(region.centerLon + eastShift, -125, -66);

  return {
    ...region,
    label: region.label.replace('CONUS focus', 'Inland severe focus'),
    centerLat: lat,
    centerLon: lon,
    bbox: [lon - 5, lat - 3, lon + 5, lat + 3],
  };
}

function regionDistance(a: Region, b: Region): number {
  const meanLat = ((a.centerLat + b.centerLat) / 2) * Math.PI / 180;
  return Math.hypot(
    (a.centerLon - b.centerLon) * Math.cos(meanLat),
    a.centerLat - b.centerLat,
  );
}

function strongerCategory(a: OutlookArea['category'], b: OutlookArea['category']): OutlookArea['category'] {
  return CATEGORY_RAMP.indexOf(b) > CATEGORY_RAMP.indexOf(a) ? b : a;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
