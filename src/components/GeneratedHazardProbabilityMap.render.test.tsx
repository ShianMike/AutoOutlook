// @vitest-environment jsdom
//
// Example-based render tests for the SPC hazard presentation
// (Task 7.3). These validate the rendering behavior of
// GeneratedHazardProbabilityMap when supplied with SPC hazard shapes via the
// `spcHazardProbabilityShapes` prop and the `comparisonMode` toggle:
//
//   - Requirement 1.2: per-hazard filtering (a map for a given hazard only
//     renders that hazard's SPC shapes).
//   - Requirement 1.3: the SPC hazard map uses the same map projection and
//     base geographic layers as the categorical map (GeneratedOutlookMap).
//   - Requirement 1.4: a significant-severe area renders as a hatched region
//     distinct from the probability-threshold fills.
//   - Requirement 1.5: overlay toggle behavior via `comparisonMode`
//     (auto/spc/overlay) shows/hides the SPC layer concurrently with the
//     generated outlook.
//   - Requirement 1.7: the legend lists each present probability threshold and
//     includes a distinct SIG entry when a significant-severe region is shown.
//
// react-simple-maps fetches a topojson base map over the network and relies on
// SVG projection math that does not run cleanly under jsdom, so it is mocked
// with light-weight stubs that expose the props under test (projection, the
// base geography URL, and per-feature fill/stroke styling) as DOM attributes.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import type {
  OutlookArtifacts,
  OutlookProbabilityShapeFeatureCollection,
} from '../types/outlookArtifacts';
import type { HourSnapshot } from '../types/forecast';

// --- react-simple-maps mock -------------------------------------------------
// ComposableMap -> <svg data-projection> so we can assert the projection.
// Geographies  -> <g data-geo-url> for string URL base layers, and expands a
//                 GeoJSON FeatureCollection into `geographies` for data layers.
// Geography    -> <path data-geo-cell data-fill data-stroke> exposing the
//                 resolved default style so fills / hatch / outline are visible.
vi.mock('react-simple-maps', () => {
  return {
    ComposableMap: ({ projection, children }: any) => (
      <svg data-testid="composable-map" data-projection={projection}>
        {children}
      </svg>
    ),
    Geographies: ({ geography, children }: any) => {
      const isUrl = typeof geography === 'string';
      const geographies =
        !isUrl && geography && Array.isArray(geography.features)
          ? geography.features.map((feature: any, index: number) => ({
              rsmKey: `geo-${index}`,
              properties: feature.properties ?? {},
              geometry: feature.geometry,
            }))
          : [];
      return (
        <g data-geographies="true" data-geo-url={isUrl ? geography : undefined}>
          {typeof children === 'function' ? children({ geographies }) : children}
        </g>
      );
    },
    Geography: ({ style }: any) => {
      const resolved = (style && style.default) || {};
      return (
        <path
          data-geo-cell="true"
          data-fill={resolved.fill != null ? String(resolved.fill) : undefined}
          data-stroke={resolved.stroke != null ? String(resolved.stroke) : undefined}
        />
      );
    },
    Marker: ({ children }: any) => <g data-marker="true">{children}</g>,
  };
});

// Imported after the mock is declared (vi.mock is hoisted).
import GeneratedHazardProbabilityMap from './GeneratedHazardProbabilityMap';
import GeneratedOutlookMap from './GeneratedOutlookMap';
import { spcHazardColor } from '../utils/spcColors';

const GEN_COLOR = spcHazardColor('tornado', 2);
const SPC_COLOR_5 = spcHazardColor('tornado', 1);
const SPC_COLOR_10 = spcHazardColor('tornado', 2);

const SQUARE: number[][][] = [
  [
    [-100, 35],
    [-99, 35],
    [-99, 36],
    [-100, 36],
    [-100, 35],
  ],
];

interface SpcFeatureInit {
  hazard: 'tornado' | 'hail' | 'wind';
  probability: number;
  bucket: number;
  label: string;
  color: string;
  significantSevere?: boolean;
}

function spcCollection(features: SpcFeatureInit[]): OutlookProbabilityShapeFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: features.map((f) => ({
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: SQUARE },
      properties: {
        hazard: f.hazard,
        probability: f.probability,
        bucket: f.bucket,
        label: f.label,
        color: f.color,
        significantSevere: Boolean(f.significantSevere),
      },
    })),
  } as unknown as OutlookProbabilityShapeFeatureCollection;
}

// Minimal artifacts carrying a single generated tornado probability band at
// forecast hour 12 so the generated (auto) layer renders a fill we can assert
// concurrently with the SPC overlay.
function generatedTornadoArtifacts(): OutlookArtifacts {
  return {
    metadata: { generatedAtISO: '2026-06-10T12:00:00Z', cycle: '2026-06-10T12' },
    riskPolygons: { type: 'FeatureCollection', features: [] },
    probabilityTiles: {
      cycle: '2026-06-10T12',
      hours: [
        {
          forecastHour: 12,
          validTimeISO: '2026-06-11T00:00:00Z',
          tile: {
            forecastHour: 12,
            validTimeISO: '2026-06-11T00:00:00Z',
            stride: 1,
            shape: [1, 1],
            lats: [[35]],
            lons: [[-100]],
            categoryOrdinal: [[0]],
            categoryLabel: [['NONE']],
            probabilities: { tornado: [[0]], hail: [[0]], wind: [[0]] },
            hazardProbabilityShapes: {
              type: 'FeatureCollection',
              features: [
                {
                  type: 'Feature',
                  geometry: { type: 'Polygon', coordinates: SQUARE },
                  properties: {
                    hazard: 'tornado',
                    probability: 0.1,
                    bucket: 2,
                    label: '10%',
                    color: GEN_COLOR,
                  },
                },
              ],
            },
          },
        },
      ],
    },
  } as unknown as OutlookArtifacts;
}

const snapshotAtHour12 = { forecastHour: 12 } as unknown as HourSnapshot;

function geoCells(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll('path[data-geo-cell]')) as HTMLElement[];
}

function fillsOf(container: HTMLElement): string[] {
  return geoCells(container)
    .map((cell) => cell.getAttribute('data-fill'))
    .filter((value): value is string => value != null);
}

beforeEach(() => {
  // Defensive: the categorical map fetches its SPC layer unless an override is
  // provided; keep any accidental network call from touching the real network.
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new Error('network disabled in render tests'))),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('GeneratedHazardProbabilityMap SPC hazard presentation', () => {
  it('renders with the same projection and base geographic layers as the categorical map (Req 1.3)', () => {
    const hazardRender = render(
      <GeneratedHazardProbabilityMap
        snapshot={null}
        hazard="tornado"
        title="Tornado Outlook"
        artifacts={null}
        status="ready"
        comparisonMode="auto"
        spcHazardProbabilityShapes={spcCollection([
          { hazard: 'tornado', probability: 0.05, bucket: 1, label: '5%', color: SPC_COLOR_5 },
        ])}
      />,
    );

    const hazardMap = hazardRender.container.querySelector('[data-projection]');
    const hazardProjection = hazardMap?.getAttribute('data-projection');
    const hazardBaseUrls = Array.from(
      hazardRender.container.querySelectorAll('g[data-geo-url]'),
    ).map((node) => node.getAttribute('data-geo-url'));

    cleanup();

    const categoricalRender = render(
      <GeneratedOutlookMap
        snapshot={null}
        status="ready"
        artifacts={null}
        message={null}
        comparisonMode="auto"
        spcDay1Override={{ type: 'FeatureCollection', features: [] }}
      />,
    );

    const categoricalMap = categoricalRender.container.querySelector('[data-projection]');
    const categoricalProjection = categoricalMap?.getAttribute('data-projection');
    const categoricalBaseUrls = Array.from(
      categoricalRender.container.querySelectorAll('g[data-geo-url]'),
    ).map((node) => node.getAttribute('data-geo-url'));

    // Same projection, and it is the shared geoAlbers projection.
    expect(hazardProjection).toBe('geoAlbers');
    expect(hazardProjection).toBe(categoricalProjection);

    // Same base geographic layer source (the shared US states topojson).
    expect(hazardBaseUrls).toContain('/us-states-10m.json');
    expect(categoricalBaseUrls).toContain('/us-states-10m.json');
    expect(hazardBaseUrls).toContain(categoricalBaseUrls[0]);
  });

  it('renders only the selected hazard\'s SPC shapes (Req 1.2)', () => {
    const { container } = render(
      <GeneratedHazardProbabilityMap
        snapshot={null}
        hazard="tornado"
        title="Tornado Outlook"
        artifacts={null}
        status="ready"
        comparisonMode="spc"
        spcHazardProbabilityShapes={spcCollection([
          { hazard: 'tornado', probability: 0.05, bucket: 1, label: '5%', color: SPC_COLOR_5 },
          { hazard: 'hail', probability: 0.15, bucket: 2, label: '15%', color: '#1b9e77' },
        ])}
      />,
    );

    const fills = fillsOf(container);
    // Only the tornado SPC fill should render; the hail shape is filtered out.
    expect(fills).toContain(SPC_COLOR_5);
    expect(fills).not.toContain('#1b9e77');
    expect(fills.filter((color) => color === SPC_COLOR_5)).toHaveLength(1);
  });

  it('renders the significant-severe area as a hatched region distinct from probability fills (Req 1.4)', () => {
    const { container } = render(
      <GeneratedHazardProbabilityMap
        snapshot={null}
        hazard="tornado"
        title="Tornado Outlook"
        artifacts={null}
        status="ready"
        comparisonMode="spc"
        spcHazardProbabilityShapes={spcCollection([
          { hazard: 'tornado', probability: 0.05, bucket: 1, label: '5%', color: SPC_COLOR_5 },
          { hazard: 'tornado', probability: 0.1, bucket: 2, label: '10%', color: SPC_COLOR_10 },
          {
            hazard: 'tornado',
            probability: 0.1,
            bucket: 2,
            label: 'SIG',
            color: SPC_COLOR_10,
            significantSevere: true,
          },
        ])}
      />,
    );

    // The significant-severe pattern is defined and used as a hatched fill.
    expect(container.querySelector('#spc-hazard-sig')).not.toBeNull();

    const fills = fillsOf(container);
    // The hatched significant-severe region uses the pattern fill...
    expect(fills).toContain('url(#spc-hazard-sig)');
    // ...and the probability-threshold fills use solid colors, never the hatch.
    const probabilityFills = fills.filter((color) => color !== 'url(#spc-hazard-sig)');
    expect(probabilityFills).toContain(SPC_COLOR_5);
    expect(probabilityFills).toContain(SPC_COLOR_10);
    expect(probabilityFills).not.toContain('url(#spc-hazard-sig)');
  });

  it('lists each present probability threshold plus a distinct SIG entry in the legend (Req 1.7)', () => {
    const { container, getAllByText } = render(
      <GeneratedHazardProbabilityMap
        snapshot={null}
        hazard="tornado"
        title="Tornado Outlook"
        artifacts={null}
        status="ready"
        comparisonMode="spc"
        spcHazardProbabilityShapes={spcCollection([
          { hazard: 'tornado', probability: 0.05, bucket: 1, label: '5%', color: SPC_COLOR_5 },
          { hazard: 'tornado', probability: 0.1, bucket: 2, label: '10%', color: SPC_COLOR_10 },
          {
            hazard: 'tornado',
            probability: 0.1,
            bucket: 2,
            label: 'SIG',
            color: SPC_COLOR_10,
            significantSevere: true,
          },
        ])}
      />,
    );

    // Locate the dedicated SPC legend section (header text is exactly "SPC").
    const spcHeader = Array.from(container.querySelectorAll('div')).find(
      (node) => node.textContent === 'SPC',
    );
    expect(spcHeader).toBeDefined();
    const spcLegend = spcHeader?.parentElement as HTMLElement;

    // Each present probability threshold has a legend entry.
    expect(spcLegend.textContent).toContain('5%');
    expect(spcLegend.textContent).toContain('10%');
    // A distinct SIG entry appears when the significant-severe region is shown.
    expect(spcLegend.textContent).toContain('SIG');
    // SIG is only present because a significant-severe region is rendered.
    expect(getAllByText('SIG').length).toBeGreaterThan(0);
  });

  it('does not render a SIG legend entry when no significant-severe region is present (Req 1.7)', () => {
    const { container } = render(
      <GeneratedHazardProbabilityMap
        snapshot={null}
        hazard="tornado"
        title="Tornado Outlook"
        artifacts={null}
        status="ready"
        comparisonMode="spc"
        spcHazardProbabilityShapes={spcCollection([
          { hazard: 'tornado', probability: 0.05, bucket: 1, label: '5%', color: SPC_COLOR_5 },
        ])}
      />,
    );

    const spcHeader = Array.from(container.querySelectorAll('div')).find(
      (node) => node.textContent === 'SPC',
    );
    const spcLegend = spcHeader?.parentElement as HTMLElement;
    expect(spcLegend.textContent).toContain('5%');
    expect(spcLegend.textContent).not.toContain('SIG');
  });

  it('toggles the SPC layer via comparisonMode, showing it concurrently with the generated outlook (Req 1.5)', () => {
    const artifacts = generatedTornadoArtifacts();
    const spc = spcCollection([
      { hazard: 'tornado', probability: 0.05, bucket: 1, label: '5%', color: SPC_COLOR_5 },
    ]);

    // auto: SPC overlay OFF, generated outlook still shown.
    const autoRender = render(
      <GeneratedHazardProbabilityMap
        snapshot={snapshotAtHour12}
        hazard="tornado"
        title="Tornado Outlook"
        artifacts={artifacts}
        status="ready"
        comparisonMode="auto"
        spcHazardProbabilityShapes={spc}
      />,
    );
    const autoFills = fillsOf(autoRender.container);
    const autoStrokes = geoCells(autoRender.container).map((c) => c.getAttribute('data-stroke'));
    expect(autoFills).toContain(GEN_COLOR); // generated outlook present
    expect(autoFills).not.toContain(SPC_COLOR_5); // SPC fill hidden
    expect(autoStrokes).not.toContain(SPC_COLOR_5); // SPC outline hidden
    cleanup();

    // overlay: SPC overlay ON (as an outline) concurrent with the generated fill.
    const overlayRender = render(
      <GeneratedHazardProbabilityMap
        snapshot={snapshotAtHour12}
        hazard="tornado"
        title="Tornado Outlook"
        artifacts={artifacts}
        status="ready"
        comparisonMode="overlay"
        spcHazardProbabilityShapes={spc}
      />,
    );
    const overlayFills = fillsOf(overlayRender.container);
    const overlayStrokes = geoCells(overlayRender.container).map((c) => c.getAttribute('data-stroke'));
    expect(overlayFills).toContain(GEN_COLOR); // generated outlook still shown
    expect(overlayStrokes).toContain(SPC_COLOR_5); // SPC overlay drawn concurrently
    cleanup();

    // spc: only the SPC fill layer is shown (generated auto layer hidden).
    const spcRender = render(
      <GeneratedHazardProbabilityMap
        snapshot={snapshotAtHour12}
        hazard="tornado"
        title="Tornado Outlook"
        artifacts={artifacts}
        status="ready"
        comparisonMode="spc"
        spcHazardProbabilityShapes={spc}
      />,
    );
    const spcFills = fillsOf(spcRender.container);
    expect(spcFills).toContain(SPC_COLOR_5); // SPC fill shown
    expect(spcFills).not.toContain(GEN_COLOR); // generated auto layer hidden
  });
});
