import type { PhilippineRegionPane } from '../types/forecast';

export interface PhilippineRegionPaneConfig {
  id: PhilippineRegionPane;
  label: string;
  description: string;
  center: [number, number];
  scale: number;
}

export const PHILIPPINE_REGION_PANES: PhilippineRegionPaneConfig[] = [
  {
    id: 'national',
    label: 'Philippines',
    description: 'Full archipelago',
    center: [121.78, 12.85],
    scale: 1700,
  },
  {
    id: 'luzon',
    label: 'Luzon',
    description: 'North and Central PH',
    center: [121.3, 16.0],
    scale: 3900,
  },
  {
    id: 'visayas',
    label: 'Visayas',
    description: 'Central islands + Palawan',
    center: [121.6, 10.5],
    scale: 4200,
  },
  {
    id: 'mindanao',
    label: 'Mindanao',
    description: 'Southern PH',
    center: [124.2, 7.4],
    scale: 5000,
  },
];

export function getPhilippineRegionPaneConfig(pane: PhilippineRegionPane): PhilippineRegionPaneConfig {
  return PHILIPPINE_REGION_PANES.find((item) => item.id === pane) ?? PHILIPPINE_REGION_PANES[0];
}
