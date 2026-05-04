import type { ForecastBundle, ForecastProvider } from '../../types/forecast';
import { buildMockBundle } from '../mockForecastData';

export const mockProvider: ForecastProvider = {
  id: 'mock',
  label: 'Mock dataset',
  async fetchBundle(): Promise<ForecastBundle> {
    // Tiny artificial delay so the loading badge has a chance to flash.
    await new Promise((r) => setTimeout(r, 80));
    return buildMockBundle(new Date());
  },
};
