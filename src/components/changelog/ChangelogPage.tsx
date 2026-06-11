import { useEffect, useMemo, useState, type CSSProperties } from 'react';

import RetroBadge from '../retro/RetroBadge';
import { viewLinkHandler } from '../../utils/navigateView';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ChangeKind = 'NEW' | 'FIX' | 'IMPROVE' | 'REMOVE' | 'DOCS';
type ReleaseStatus = 'CURRENT' | 'STABLE' | 'INITIAL';

interface ChangeEntry {
  kind: ChangeKind;
  title: string;
  body: string;
}

interface VersionRelease {
  version: string;       // 'v0.2'
  codename: string;      // short title
  date: string;          // ISO date
  status: ReleaseStatus;
  summary: string;
  highlights: string[];
  changes: ChangeEntry[];
}

// ---------------------------------------------------------------------------
// Style maps
// ---------------------------------------------------------------------------

type ToneName = 'lime' | 'amber' | 'cyan' | 'red' | 'paper';

const KIND_TONE: Record<ChangeKind, ToneName> = {
  NEW:     'lime',
  FIX:     'amber',
  IMPROVE: 'cyan',
  REMOVE:  'red',
  DOCS:    'paper',
};

const KIND_GLYPH: Record<ChangeKind, string> = {
  NEW:     '+',
  FIX:     '✕',
  IMPROVE: '↑',
  REMOVE:  '−',
  DOCS:    '✎',
};

const STATUS_TONE: Record<ReleaseStatus, 'lime' | 'amber' | 'cyan'> = {
  CURRENT: 'lime',
  STABLE:  'cyan',
  INITIAL: 'amber',
};

// Tailwind JIT cannot interpolate class names at runtime, so we route every
// tone-dependent class through explicit string maps it can statically see.
const TONE_BG: Record<ToneName, string> = {
  lime:  'bg-signal-lime',
  amber: 'bg-signal-amber',
  cyan:  'bg-signal-cyan',
  red:   'bg-signal-red',
  paper: 'bg-paper',
};
const TONE_BORDER: Record<ToneName, string> = {
  lime:  'border-signal-lime',
  amber: 'border-signal-amber',
  cyan:  'border-signal-cyan',
  red:   'border-signal-red',
  paper: 'border-paper',
};
const TONE_TEXT: Record<ToneName, string> = {
  lime:  'text-ink',
  amber: 'text-ink',
  cyan:  'text-ink',
  red:   'text-paper',
  paper: 'text-ink',
};

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

const RELEASES: VersionRelease[] = [
  {
    version: 'v1.2',
    codename: 'Retrained Hazard Models & Feature Schema v5',
    date: '2026-06-11',
    status: 'CURRENT',
    summary:
      'This release retrains the AutoOutlook hazard system on 849,720 HRRR samples, expands the model from three severe-hazard heads to four trained hazards including general thunder, adds location, reflectivity, temperature, height, and time-season inputs, improves CIG intensity and merged-outlook rendering, and removes obsolete non-HRRR paths.',
    highlights: [
      'Four trained hazard heads: Tornado, hail, wind, and general thunder now use calibrated XGBoost models trained on 849,720 archive rows.',
      'Feature schema v5: Expanded to 37 model inputs, adding latitude, longitude, surface temperature, composite reflectivity, 500-mb height, valid-hour cycles, month cycles, and day-of-year cycles.',
      'Larger calibrated models: Training now uses 600 trees, depth 5, time-based holdout validation, and isotonic probability calibration.',
      'Held-out ROC-AUC: Tornado 0.940, hail 0.956, wind 0.980, and thunder 0.983. These are model validation scores, not guaranteed forecast accuracy.',
      'Trained TSTM risk: The Risk Levels TSTM outline now follows the trained general-thunder probability field instead of a separate rule-based thunder shape.',
      'TSTM shape recovery: Thunder-specific probability caps preserve the trained 10% / 40% / 70% bands, and generated Thunder maps no longer fall back to category-derived geometry.',
      'Retrained CIG intensity: Hail and wind intensity models use the expanded feature schema, while small CIG fragments are pruned and displayed as one labeled two-line corridor.',
      'Merged Outlook upgrade: Merged mode is the default view, hourly CIG areas are joined into cleaner corridors, and CIG is kept on hazard maps instead of cluttering the categorical risk map.',
      'Retrained 2026 archive: Every hardcoded historical case is rebuilt with the v1.2 hazard models across the full 12Z-to-12Z Day 1 window.',
      'Archive date cleanup: April 18, 2026 was removed from the Risk Archive selector, leaving 21 curated 2026 verification events.',
      'HRRR-only cleanup: Removed obsolete ECMWF and Philippines-specific runtime paths and standardized the artifact timeline to f00-f48.',
      'The June 10 00Z HRRR dev artifact was force-regenerated across f00-f48 for the local dev server.',
    ],
    changes: [
      {
        kind: 'NEW',
        title: 'Fourth trained model for general thunder',
        body: 'Added a calibrated thunder classifier and a thunder model artifact. TSTM can now come from learned thunder probability instead of only environmental rules.',
      },
      {
        kind: 'NEW',
        title: 'Feature schema v5 with 37 inputs',
        body: 'Added location, surface temperature, composite reflectivity, 500-mb height, valid-hour, monthly, and day-of-year signals alongside the existing CAPE, CIN, moisture, shear, and composite parameters.',
      },
      {
        kind: 'IMPROVE',
        title: 'Tornado, hail, and wind models retrained',
        body: 'Retrained the severe-hazard classifiers on 849,720 archive rows using 600 trees, depth 5, time-based validation, and isotonic calibration. Held-out ROC-AUC increased to 0.940 tornado, 0.956 hail, and 0.980 wind.',
      },
      {
        kind: 'FIX',
        title: 'TSTM risk uses trained thunder support',
        body: 'The categorical TSTM polygon and Thunder hazard map now use the trained thunder probability grid directly. Thunder-specific category caps keep TSTM cells above the 10% display threshold, and missing thunder source grids no longer create category-derived TSTM shapes.',
      },
      {
        kind: 'IMPROVE',
        title: 'CIG intensity models and merged corridors',
        body: 'Retrained hail and wind intensity models with the expanded schema, constrained CIG to active hazard support, removed tiny components, and joined hourly areas into smoother merged-outlook corridors.',
      },
      {
        kind: 'IMPROVE',
        title: 'Cleaner operator map defaults',
        body: 'The dashboard now opens on Merged Outlook, hides the hourly scrubber in merged mode, removes CIG from the categorical risk map, and uses one labeled CIG overlay on the hazard maps.',
      },
      {
        kind: 'IMPROVE',
        title: 'Historical archive regenerated with v1.2',
        body: 'Rebuilt each hardcoded 2026 verification event with the trained tornado, hail, wind, and thunder models from event-day HRRR f12-f36. Archive generation now rejects stale model metadata instead of reusing older rule-based artifacts.',
      },
      {
        kind: 'REMOVE',
        title: 'April 18 removed from Risk Archive',
        body: 'Removed Apr 18, 2026 from the hardcoded archive catalog and generated archive dropdown. The Risk Archive now exposes 21 curated 2026 verification events while neighboring 12Z-to-12Z windows remain intact.',
      },
      {
        kind: 'REMOVE',
        title: 'Legacy ECMWF and Philippines paths removed',
        body: 'Removed the obsolete ECMWF selector, 90-hour timeline branches, Philippines boundary asset, and PAGASA request document so the runtime and static export follow one HRRR f00-f48 path.',
      },
      {
        kind: 'DOCS',
        title: 'Reference descriptions aligned with v1.2',
        body: 'Updated System Overview, Risk Level Codex, SPC QC Console, Predictability Window, Hazard Probability Bands, Ingredients Glossary, and landing-page copy so the in-app text matches the trained thunder model, CIG cleanup, SPC QC flow, and 12Z-to-12Z archive behavior.',
      },
      {
        kind: 'DOCS',
        title: 'Version surfaces updated to v1.2',
        body: 'Updated package metadata, app footers, transition screens, landing copy, documentation, and the in-app changelog for the full v1.2 model release. The June 10 00Z HRRR dev artifact was also regenerated across f00-f48.',
      },
    ],
  },
  {
    version: 'v1.1',
    codename: 'Merged D1 00Z Lock & Animated Release Console',
    date: '2026-06-08',
    status: 'STABLE',
    summary:
      'This release fixes the Merged Day 1 outlook so it displays the selected day\'s 00Z run, limits the date picker to the latest two available days, preserves the daily 00Z merged cache when later cycles arrive, and adds polished motion to the landing page and in-app changelog.',
    highlights: [
      'Merged D1 now uses the selected day\'s 00Z run: choosing March 25 points the merged outlook at March 25 00Z instead of a later cycle.',
      'Date picker is capped to two available days so stale archive dates do not appear in the live Merged D1 control.',
      '00Z cache is preserved: 06Z, 12Z, and 18Z refreshes no longer overwrite the Merged D1 source bundle for that day.',
      'Landing page animations: Added atmospheric drift, staggered hero reveal, animated telemetry, hover glints, and reduced-motion support.',
      'In-app changelog animations: Added release-tape motion, sticky filter treatment, animated release cards, and row-level change reveals.',
    ],
    changes: [
      {
        kind: 'FIX',
        title: 'Merged D1 uses the selected day 00Z run',
        body: 'The Merged D1 outlook now anchors to the selected calendar day at 00Z, so the visible forecast cycle matches the chosen date.',
      },
      {
        kind: 'FIX',
        title: 'Later cycles do not overwrite merged D1',
        body: 'The daily 00Z merged cache is stored separately, allowing 06Z, 12Z, and 18Z refreshes to update normal artifacts without replacing the Merged D1 source.',
      },
      {
        kind: 'IMPROVE',
        title: 'Merged D1 date list capped to two days',
        body: 'The Merged D1 date dropdown now exposes only the latest two available dates to keep the live control focused on current guidance.',
      },
      {
        kind: 'IMPROVE',
        title: 'Animated landing page',
        body: 'Added page-load sequencing, moving forecast texture, telemetry sweep effects, animated probability tiles, hover glints, and reduced-motion handling.',
      },
      {
        kind: 'IMPROVE',
        title: 'Animated in-app changelog',
        body: 'Added release-tape atmosphere, diff panel sweeps, sticky animated filters, staged release cards, and change-row reveals.',
      },
      {
        kind: 'DOCS',
        title: 'Version surfaces updated to v1.1',
        body: 'Updated package metadata, app footers, docs badges, landing copy, transition copy, and the in-app changelog current release to v1.1.',
      },
    ],
  },
  {
    version: 'v1.0',
    codename: '2026 Risk Archive, SPC Hazard Layers & OpenFetch Sponsor',
    date: '2026-06-06',
    status: 'STABLE',
    summary:
      'This release promotes the historical ENH+ verification workflow into a dedicated 2026 Risk Archive, adds official SPC Day 1 hazard probability layers for tornado, hail, and wind comparison, fixes malformed hazard polygons, and updates the landing page with direct archive access plus an OpenFetch repository sponsor strip.',
    highlights: [
      '2026 Risk Archive: Added a direct landing-page path into the in-app documentation archive for March through May ENH+ verification cases.',
      'Official SPC hazard probability layers: Hazard maps can compare AutoOutlook-only, SPC Day 1 only, or overlay mode for tornado, hail, and wind outlooks.',
      'Storm report verification: Historical tornado, hail, and wind reports remain visible on the same dashboard-style map controls.',
      'Polygon rendering fix: Tiny malformed rings are filtered before GeoJSON serialization, preventing full-screen hazard fills and straight-line artifacts.',
      'OpenFetch sponsor strip: The landing page footer area now links out to the OpenFetch repository.',
    ],
    changes: [
      {
        kind: 'NEW',
        title: '2026 Risk Archive landing route',
        body: 'Added prominent landing-page buttons and navigation links that open the 2026 historical risk archive directly inside the docs tab.',
      },
      {
        kind: 'NEW',
        title: 'SPC hazard outlook comparison',
        body: 'Hardcoded historical archive data now includes official SPC tornado, hail, and wind probability shapes for SPC-only and overlay comparison modes.',
      },
      {
        kind: 'FIX',
        title: 'Hazard polygon rendering cleanup',
        body: 'Dropped sub-threshold serialized rings from generated GeoJSON so hail, wind, tornado, and thunderstorm layers do not paint full-screen artifacts.',
      },
      {
        kind: 'DOCS',
        title: 'OpenFetch repository sponsor',
        body: 'Added a bottom landing-page sponsor panel linking to the OpenFetch GitHub repository.',
      },
      {
        kind: 'DOCS',
        title: 'Version surfaces updated to v1.0',
        body: 'Updated package metadata, app footers, docs badges, landing copy, and the in-app changelog current release to v1.0.',
      },
    ],
  },
  {
    version: 'v0.9',
    codename: 'Multi-Cycle Merge, Verification Archives & Storm Reports',
    date: '2026-06-03',
    status: 'STABLE',
    summary:
      'This release introduces dynamic multi-cycle merging for SPC Day 1 comparison, automatic NOAA/SPC verification archive fetching for historical dates, daily SPC storm reports (Tornado, Hail, Wind) overlay on all outlook and hazard maps, singleflight concurrency locking, and optimized static bundle exports.',
    highlights: [
      'Multi-cycle merge date selection: Added a retro-themed Date picker dropdown to compare merged forecast runs with historical SPC records.',
      'NOAA/SPC verification archive retriever: Automatically fetches and parses official GeoJSON outlooks on-the-fly for historical dates when no local cycle cache exists.',
      'SPC daily storm reports overlay: Renders crimson triangles (Tornado), forest green circles (Hail), and steel blue squares (Wind) with detailed tooltips and legends on all Outlook and Hazard Map panels.',
      'Singleflight concurrency locks: Prevents duplicate/concurrent forecast merges and NOAA download requests in local development.',
      'Static export size optimization: Stripped heavy grid coordinates from static probability tiles, saving 98% of space (reducing files from 28MB to 3.6MB) for fast Cloudflare builds.',
    ],
    changes: [
      {
        kind: 'NEW',
        title: 'SPC daily storm reports overlay',
        body: 'Added Tornado, Hail, and Wind report overlays to the main Outlook and individual/grid Hazard Probability maps with custom tooltip details and retro legends.',
      },
      {
        kind: 'NEW',
        title: 'Multi-cycle merge date selection',
        body: 'Allows operators to choose from available historical dates on the Merged D1 Outlook to dynamically fetch, compile, and run the verification comparison.',
      },
      {
        kind: 'IMPROVE',
        title: 'NOAA verification archives fetcher',
        body: 'Automatically downloads and extracts historical SPC Day 1 GeoJSONs from NOAA archives when no local cycle cache exists.',
      },
      {
        kind: 'IMPROVE',
        title: 'Singleflight lock & double freshness checks',
        body: 'Synchronizes merge executions to prevent concurrent disk writes and duplicate NOAA network calls.',
      },
      {
        kind: 'IMPROVE',
        title: 'Static archive API exporter',
        body: 'Statically exports merged verification and hazard data for Pages static hosting, stripping heavy grids to keep builds lightweight.',
      },
    ],
  },
  {
    version: 'v0.8',
    codename: 'Florida Land Mask Fix, Grid Stride 2 & Dashboard Alignment',
    date: '2026-06-02',
    status: 'STABLE',
    summary:
      'This release expands the CONUS land mask to Southern Florida and the Keys, prevents strict ocean offshore suppressions on land grid cells, aligns dashboard widget eyebrows to their sidebar navigation index, and defaults the gridded risk pipeline to a denser gridStride of 2 with raw GRIB caching to keep dev fetches fast.',
    highlights: [
      'South Florida and Keys land mask correction: Expanded conus_ring bounds and updated offshore suppressions to prevent severe convective risks from being incorrectly cut off in Naples, Everglades, Cape Sable, Miami, and the Florida Keys.',
      'Denser gridStride=2 default: Lowered the default pipeline grid stride from 4 to 2 to significantly reduce sharp corners and step edges in generated risk polygons.',
      'Raw GRIB caching: Implemented local raw GRIB cache files (*.grib2) to bypass NOAA S3 byte-range downloads on repeated runs, keeping local development iteration extremely fast.',
      'Unified dashboard index alignment: Corrected the numbered eyebrows on all primary dashboard widgets (01 through 08) to correspond exactly with the sidebar navigation button numbers.',
      'Aligned test suites: Shifted test verification coordinate grids to keep them purely offshore, resolving land mask overlap collisions, and updated assertions dynamically.',
    ],
    changes: [
      {
        kind: 'FIX',
        title: 'Enclosed South Florida and Keys in CONUS land mask',
        body: 'Expanded the coarse land polygon boundary down to 24.5 N and updated the strict ocean offshore masks to subtract the land mask, preventing unconditional suppression of land coordinates.',
      },
      {
        kind: 'FIX',
        title: 'Aligned dashboard component numbers with sidebar navigation',
        body: 'Updated section eyebrows on the outlook map, hazard board, ingredients grid, risk timeline, discussion, verification, and system status widgets to match their respective nav buttons.',
      },
      {
        kind: 'IMPROVE',
        title: 'Calibrated pipeline grid stride to 2 with raw GRIB caching',
        body: 'Decreased default grid stride from 4 to 2 across configuration systems, and introduced a raw GRIB2 caching layer to accelerate local dev execution times.',
      },
      {
        kind: 'IMPROVE',
        title: 'Offshore testing coordinates and assertions',
        body: 'Updated backend test coordinates for Gulf and Florida Gulf testing boxes to remain strictly offshore and made features shape validations dynamic.',
      },
      {
        kind: 'DOCS',
        title: 'Bumped metadata and documentation versions to v0.8',
        body: 'Updated package, lockfile, app footers, transit screens, and changelog deck to reflect version 0.8.',
      },
    ],
  },
  {
    version: 'v0.7.1',
    codename: 'Regional Calibration & Enhanced UI Tactility',
    date: '2026-06-01',
    status: 'STABLE',
    summary:
      'This release introduces comprehensive regional logic calibration for mesoscale convective outlooks across CONUS, and optimizes operator dashboard transitions with heavier, more tactile loading intervals.',
    highlights: [
      'Regional logic verified: Evaluated CAD wedge interactions, high-based dryness, dryline bounds, and terrain-forced cold pool transitions',
      'Tactile UI transition overlays: Increased dashboard view-switch loading sequence durations to provide a robust, brutalist operator feel',
      'Unified risk calibrations: Fully calibrated gridded hazard margins matching live operator counts for Slight and Marginal zones',
    ],
    changes: [
      {
        kind: 'FIX',
        title: 'Calibrated regional dryline and frontal gradients',
        body: 'Verified that Plains convective boundary setups (dryline, triple point, and warm fronts) properly evaluate horizontal dewpoint gradients under robust shear profiles.',
      },
      {
        kind: 'IMPROVE',
        title: 'Tactile dashboard loading screens',
        body: 'Prolonged the transition loading animation durations in the operator control center, shifting from short screen flashes to robust, deliberate load states.',
      },
      {
        kind: 'DOCS',
        title: 'Updated release log for v0.7.1',
        body: 'Added v0.7.1 release notes to the interactive operator changelog, downgrading v0.7 to stable.',
      },
    ],
  },
  {
    version: 'v0.7',
    codename: 'Better Risk Categories & Reliable Refresh',
    date: '2026-06-01',
    status: 'STABLE',
    summary:
      'This release makes the forecast categories behave more like a normal forecaster would expect. AutoOutlook should show fewer broad Marginal risk areas when the setup is weak, while still allowing real Slight risk areas to appear when the storm environment supports them. It also improves scheduled refresh reliability and updates the project release details for v0.7.0.',
    highlights: [
      'Fewer false Marginal areas: Weak storm setups are less likely to paint a broad MRGL risk area across the map',
      'Better Slight risk detection: Compact but real SLGT areas can now survive the filters instead of getting downgraded',
      'More balanced storm ingredients: Strong shear can now help a setup reach SLGT even when instability is only moderate',
      'Regression tested: The new calibration path is covered by tests and the full 146-test deployable outlook suite passed',
      'Backtest improved: A historical HRRR 06Z run raised SLGT coverage from about 92 cells to 718 cells after calibration',
      'Refresh backup added: GitHub Actions now has a backup schedule if the normal refresh trigger is missed',
      'Release cleanup: Code of Conduct, MIT license, ignore rules, package version, and GHCR defaults are updated for v0.7.0',
    ],
    changes: [
      {
        kind: 'FIX',
        title: 'Marginal risk is less likely to be overdrawn',
        body: 'AutoOutlook now only holds hail and wind probabilities down at the Marginal tier when both instability and wind shear are weak. If one ingredient is strong enough, the forecast can keep climbing toward Slight risk instead of being capped too early.',
      },
      {
        kind: 'IMPROVE',
        title: 'Very weak storm setups still stay low',
        body: 'The lowest thunderstorm cap remains conservative. If instability is very weak or shear is extremely weak, the system keeps the risk near baseline thunderstorm level instead of creating unnecessary Marginal alerts.',
      },
      {
        kind: 'IMPROVE',
        title: 'Moderate shear can now support Slight risk',
        body: 'The severe-weather shear check was relaxed from 35 kt to 30 kt. In plain terms, a setup with decent wind support no longer gets stuck at Marginal just because it misses the old stricter cutoff.',
      },
      {
        kind: 'FIX',
        title: 'Small but real Slight risk areas are kept',
        body: 'The system used to require a larger Slight area before keeping it. That minimum size was lowered, so compact but meaningful severe-weather zones can now stay visible on the map.',
      },
      {
        kind: 'NEW',
        title: 'New test protects the calibration change',
        body: 'Added a regression test for the exact problem: moderate instability plus strong shear should be allowed to reach Slight risk for wind and hail, while the existing regional safety rules still behave the same.',
      },
      {
        kind: 'IMPROVE',
        title: 'Verified with tests and a historical backtest',
        body: 'The deployable outlook test suite passed all 146 tests. A historical HRRR 06Z 20260530 backtest showed the calibration working: Slight risk coverage increased from about 92 cells to 718 cells, while broad Marginal overforecasting was reduced.',
      },
      {
        kind: 'NEW',
        title: 'Backup refresh schedule',
        body: 'Added a second GitHub Actions schedule as a backup. If the normal refresh is missed, the backup run gives the 00Z, 06Z, 12Z, and 18Z cycles another chance to publish fresh artifacts.',
      },
      {
        kind: 'IMPROVE',
        title: 'Refresh work stays split into smaller jobs',
        body: 'The artifact refresh remains split into seven forecast-hour jobs before the final deploy step. That keeps one job from doing all the work and helps the refresh finish more reliably.',
      },
      {
        kind: 'FIX',
        title: 'Missed 06Z schedule recovery',
        body: 'Added the recovery note for the missed 06Z run: start the refresh manually, regenerate the latest HRRR artifacts, and confirm the deployed API is serving the fresh bundle.',
      },
      {
        kind: 'DOCS',
        title: 'Community standards added',
        body: 'Added the project Code of Conduct, MIT License, and package license metadata so the GitHub community checklist is covered.',
      },
      {
        kind: 'REMOVE',
        title: 'Local operator files removed from tracking',
        body: 'Moved local notes, environment examples, setup scripts, deployment instructions, and one-off helper scripts into the ignore list so they stay local and do not appear in release commits.',
      },
      {
        kind: 'IMPROVE',
        title: 'Release metadata bumped to v0.7.0',
        body: 'Updated package metadata, lockfile metadata, GHCR publish defaults, transition copy, app footer text, docs header badge, and in-app changelog surfaces for v0.7.',
      },
    ],
  },
  {
    version: 'v0.6',
    codename: 'SPC QC Console & Overlay Compare',
    date: '2026-05-31',
    status: 'STABLE',
    summary:
      'Promotes SPC Day 1 verification into a dedicated operator QC workflow. Adds AutoOutlook/SPC/overlay map comparison, rebuilds the verification panel around agreement, displacement, and category-ledger cards, and removes sidebar clutter from watch readiness, model telemetry, and previous-cycle trend experiments.',
    highlights: [
      'SPC QC Console: Agreement, displacement ratio, category ledger, leakage guard, metadata ticker, and diagnostic logs in one panel',
      'SPC Overlay Compare: AutoOutlook only, SPC Day 1 only, and overlay comparison modes on the main outlook map',
      'Bounded QC Hatches: Underforecast and overforecast hatches now mark actual comparison regions instead of flooding the whole map',
      'Full Risk Ledger: NONE, TSTM, MRGL, SLGT, ENH, MDT, and HIGH stay visible even when their counts are zero',
      'Focused Sidebar: Removed time scrubber, watch readiness, model integrity, and previous-cycle trend navigation clutter',
      'v0.6 Documentation: Landing page, reference manual, and changelog now describe the SPC QC workflow',
    ],
    changes: [
      {
        kind: 'NEW',
        title: 'SPC overlay comparison modes',
        body: 'Added map controls for AutoOutlook contours only, official SPC Day 1 contours only, and overlay compare mode. Overlay mode keeps AutoOutlook and SPC boundaries visible together for direct visual QA.',
      },
      {
        kind: 'NEW',
        title: 'System Calibration / SPC QC panel',
        body: 'Introduced a dedicated verification panel with SPC agreement, evaluated/aligned cells, displacement ratio, SPC forecaster metadata, valid/expiration timestamps, leakage guard status, and diagnostic logs.',
      },
      {
        kind: 'IMPROVE',
        title: 'Category ledger shows every risk tier',
        body: 'The category ledger now renders every risk category from NONE through HIGH, including zero-count rows, with better right-side padding so values no longer crowd the bar border.',
      },
      {
        kind: 'FIX',
        title: 'Overlay QC hatches are bounded',
        body: 'Fixed the underforecast and overforecast hatch regions so they no longer cover the entire map. Hatches now stay tied to the actual SPC-vs-AutoOutlook comparison areas.',
      },
      {
        kind: 'IMPROVE',
        title: 'Responsive verification-card layout',
        body: 'Reworked SPC Agreement, Displacement Ratio, and Category Ledger into equal-height cards with compact headers and hover/focus descriptions for QC terms.',
      },
      {
        kind: 'REMOVE',
        title: 'Removed nonessential operator navigation',
        body: 'Removed sidebar buttons for the time scrubber, watch readiness, model integrity ledger, and previous-cycle trend panel so the dashboard navigation stays focused on active review tasks.',
      },
      {
        kind: 'DOCS',
        title: 'v0.6 public copy and docs refresh',
        body: 'Updated the landing page, reference manual, transition copy, and in-app changelog to explain the SPC QC console, overlay comparison modes, and streamlined dashboard navigation.',
      },
    ],
  },
  {
    version: 'v0.5',
    codename: 'Brutalist UI Refactor & Hazard Diagnostics',
    date: '2026-05-31',
    status: 'STABLE',
    summary:
      'Refactors operator dashboard layout, integrates unified control dropdowns, enhances the Hazard Probability Board with comprehensive hover diagnostics, replaces all emojis with custom SVGs, and refines flood risk formulation to incorporate multi-variable soil and drainage profiles.',
    highlights: [
      'Operator Control Row: Unified active region, hazard view, and export options into a clean, drop-down single-row interface',
      'Hazard Probability Board: Redesigned diagnostic cards with custom parameter-specific description tooltips',
      'SVG Asset Integration: Replaced all legacy text/unicode emojis with beautiful, themed vector graphics for all weather symbols',
      'Advanced Flood Hazard Engine: Expanded flood hazard formula to balance PWAT with soil saturation, runoff factors, and local terrain slope',
      'Tactile Close Controls: Added manual dismiss actions to the export overlay for improved operator control',
    ],
    changes: [
      {
        kind: 'IMPROVE',
        title: 'Unified single-row dropdown controls',
        body: 'Reorganized the active region, forecast type, hazard view, and export actions into a single compact control row. The control layout adapts dynamically and places native drop-down arrows perfectly inline.',
      },
      {
        kind: 'IMPROVE',
        title: 'Dashboard layout reorganization',
        body: 'Moved the convective type row below the primary forecast map to establish a logical top-to-bottom spatial hierarchy.',
      },
      {
        kind: 'NEW',
        title: 'Completely emoji-free brutalist SVG system',
        body: 'Scrubbed all legacy unicode emojis from UI modules, replacing them with high-fidelity custom SVG indicators for winds, hail, tornadoes, and general thunderstorms.',
      },
      {
        kind: 'NEW',
        title: 'Refactored flood risk formulation',
        body: 'Upgraded the flood hazard logic from a simple PWAT-only threshold to an advanced multi-variable formulation incorporating soil moisture saturation, precipitation runoff factors, and topographic slopes.',
      },
      {
        kind: 'IMPROVE',
        title: 'Enhanced Hazard Probability Board & tooltips',
        body: 'Re-engineered the hazard cards to present precise probability categories with detailed contextual explanations accessible via touch and hover tooltips.',
      },
      {
        kind: 'NEW',
        title: 'Export overlay dismiss action',
        body: 'Equipped the animated GIF export panel with a robust, styled close button to let operators easily return to the dashboard.',
      },
    ],
  },
  {
    version: 'v0.4',
    codename: 'US regional severe logic expansion',
    date: '2026-05-30',
    status: 'STABLE',
    summary:
      'Implements comprehensive regional severe convective risk logic across the United States. Adds tailored forecasting engines for the Southeast (Dixie Alley, Florida, Gulf Coast), Midwest/Corn Belt, High Plains/Front Range, Northern Plains, Northeast/Appalachians, Desert Southwest, Intermountain West, Pacific Northwest, Great Basin, and California. Refines convective environment assessments with terrain wedge features, stabilization penalties, custom landspout shear/vorticity scales, and low-CAPE/high-shear configurations.',
    highlights: [
      'Southeast & Dixie Alley: Tuned high-shear/low-CAPE nocturnal tornado logic, discrete vs. QLCS warm sector transitions, and sea-breeze waterspout/pulse modes',
      'Midwest & Corn Belt: Integrated prior-convection stabilization penalties and warm-front boundary-enhanced tornado boosts',
      'High Plains & Front Range: Developed high-based dryness checks for microbursts/hail and a specialized landspout vorticity mode',
      'Northeast & Appalachians: Engineered cold-air damming wedge stability checks and terrain-locked fast low-level shear adjustments',
      'Desert Southwest & Monsoon: Created dry microburst wind modes and heavy monsoon rain/flash-flood risk filters',
      'West Coast & Northwest: Designed low-topped cold-core convective setups and low-CAPE winter terrain organization logic',
    ],
    changes: [
      {
        kind: 'NEW',
        title: 'Southeast, Florida, and Dixie Alley convective physics',
        body: 'Added specific atmospheric calculations for Dixie Alley and the Gulf Coast, lifting nocturnal tornado penalties and adjusting low-level jet/SRH weights for discrete cells ahead of a squall line. Optimized Florida sea-breeze and outflow boundaries to favor pulse wind and waterspouts unless deep-layer shear overrides.',
      },
      {
        kind: 'NEW',
        title: 'Midwest and High Plains regional convective engines',
        body: 'Implemented morning prior-convection stabilization penalties to suppress false-positive upgrades. Enhanced warm-front and boundary-based tornado probabilities under low LCLs and intact surface-based inflow. Added high-based subcloud dryness hail/wind overrides and a landspout index scaling weak deep-layer shear setups.',
      },
      {
        kind: 'NEW',
        title: 'Northeast wedge fronts and terrain-forced fast flow logic',
        body: 'Modeled Cold-Air Damming (CAD) stability overrides to penalize surfaced-based risk under a locked wedge, while boosting fast shear flow overrides in high-shear, low-CAPE Appalachian environments.',
      },
      {
        kind: 'NEW',
        title: 'Desert Southwest monsoon and dry microburst filters',
        body: 'Engineered specialized dry microburst and dust/outflow wind modes for high sub-cloud dryness. Set up monsoon rain stabilizers that cap convective severity unless moisture profiles are matched by strong deep-layer dynamic shear.',
      },
      {
        kind: 'NEW',
        title: 'West Coast, Intermountain West, and Great Basin cold-core setups',
        body: 'Constructed terrain-forced and low-topped cold-core low-CAPE/high-shear hazard modes, defaulting Pacific Northwest and California coastal systems to general thunderstorm (TSTM) or marginal (MRGL) risks in the absence of robust dynamic organization.',
      },
      {
        kind: 'IMPROVE',
        title: 'Region-specific thermodynamic and kinematic weighting',
        body: 'Re-routed the central forecast evaluation pipelines to map geographic latitude/longitude coordinates to specific sub-regional risk profiles, providing precise multi-mode hazard outputs tailored to regional climatology.',
      },
    ],
  },
  {
    version: 'v0.3',
    codename: 'Unified controls and map zoom',
    date: '2026-05-30',
    status: 'STABLE',
    summary:
      'Consolidates dashboard operator controls into a unified container and introduces nested sub-selectors with single-pane toggles. Standardizes hazard maps to the exact scale, aspect ratio, and zoom level of the primary risk map. Refactors the Risk Timeline and Environmental Ingredients with tactile navigators, LED VU meters, glowing green LCDs, and hover tooltips.',
    highlights: [
      'Unified dashboard control cards into a single cohesive container',
      'Single-pane hazard view toggle to isolate wind, hail, tornado, or thunder',
      'Synchronized map dimensions and canvas scale across risk and hazard layers',
      'Tactile period jumps and 3D lift physics in the Risk Timeline cards',
      'Analog LED VU meters and green-phosphor LCD displays for parameters',
      'Descriptive hover tooltips for all 21 environmental ingredients and signals',
      'Tuned layout readability with glowing amber borders on all dark modules',
      'Local development server workflow documented for easy offline runs',
    ],
    changes: [
      {
        kind: 'NEW',
        title: 'Single-pane toggle for hazard outlook maps',
        body: 'Introduced layout mode selectors allowing operators to swap between the default 4-grid multi-pane overview and a focused single-pane view showing only the selected hazard map.',
      },
      {
        kind: 'NEW',
        title: 'Hierarchical sub-selectors for hazard navigation',
        body: 'Added nested sub-mode option buttons within the control deck, utilizing high-contrast retro active states to navigate specific hazard details cleanly.',
      },
      {
        kind: 'IMPROVE',
        title: 'Unified dashboard controller container',
        body: 'Merged separate control boxes and exporter configurations into a single, clean border-separated dashboard deck, reducing vertical footprint.',
      },
      {
        kind: 'IMPROVE',
        title: 'Unified map zoom, dimensions, and scales',
        body: 'Rescaled the hazard probability maps from 760 to 1000 scale and expanded layout dimensions to 900x520 to perfectly align aspect ratios and zoom boundaries with the primary risk levels map.',
      },
      {
        kind: 'IMPROVE',
        title: 'Risk Timeline mechanical jumps and lift physics',
        body: 'Enabled period cards to trigger fast-travel clicks that navigate directly to peak storm hours. Configured 3D brutalist lifts on hover alongside custom sliding deck transition footers.',
      },
      {
        kind: 'NEW',
        title: 'Segmented LED VU meters and green-phosphor LCDs',
        body: 'Installed 12-segment physical LED indicator strips showing colored gradients and dim unlit steps for coverage and ingredients. Upgraded stat metrics to pitch-black green-phosphor LCD readouts.',
      },
      {
        kind: 'NEW',
        title: 'Contextual hover tooltips for all ingredients',
        body: 'Embedded 21 detailed descriptions based on SPC glossary entries into tooltips styled with glowing amber borders and neon-lime text.',
      },
      {
        kind: 'IMPROVE',
        title: 'Tuned dashboard readability and yellow outline borders',
        body: 'Boosted group titles to glowing amber and sub-labels to high-intensity neon-lime. Upgraded black card frames to yellow-amber borders on all bg-ink modules to separate shadows.',
      },
      {
        kind: 'DOCS',
        title: 'Local development server workflow',
        body: 'Added instructions and requirements for initializing, running, and configuring the Flask backend server and Vite frontend server in local offline development environments.',
      },
    ],
  },
  {
    version: 'v0.2',
    codename: 'Cleaner band rendering',
    date: '2026-05-22',
    status: 'STABLE',
    summary:
      'Risk-band rendering pass and a product-first landing rewrite. Closes the inter-tier gap with a colored separator, kills the SLGT glow, and stops tiny high-tier features from rendering as halo artifacts.',
    highlights: [
      '5 km separator boundary now visibly colored',
      'No more green glow inside SLGT',
      'Tiny ENH/MDT specks demote one tier down',
      'Landing page reads like a product, not a stack trace',
    ],
    changes: [
      {
        kind: 'FIX',
        title: 'Removed inner separator stroke that tinted SLGT green',
        body: 'The boundary stroke between bands was rendered half-inside, half-outside each polygon. The inside half showed through the 0.48-opacity fill and tinted SLGT yellow toward green. The stroke is gone; bands now read in their true color.',
      },
      {
        kind: 'NEW',
        title: 'Per-band separator stroke to color the 5 km boundary',
        body: 'Each higher band gets a separate separator stroke rendered in the lower band\'s color. The 5 km lower-owned boundary now reads as an intentional band edge instead of a paper-white seam.',
      },
      {
        kind: 'FIX',
        title: 'Auto-demote tiny higher-tier specks',
        body: 'ENH, MDT, MOD, and HIGH features below 0.04 deg² are walked one tier down per pass until they pass threshold (or hit TSTM). Stops a stray single-cell ENH dot from rendering as a meaningless speck inside a wider SLGT region.',
      },
      {
        kind: 'FIX',
        title: 'No more concentric halo around small features',
        body: 'Tiny higher-tier features below 0.04 deg² are demoted before rendering so the separator pass does not create concentric halo artifacts around single-cell specks.',
      },
      {
        kind: 'IMPROVE',
        title: 'Polygon ring orientation normalized for hole rendering',
        body: 'Annulus geometry is built as a donut (expanded outer + original outer as inner hole). Ring orientation is normalized after construction so d3-geo paints the gap, not the inverse of the gap.',
      },
      {
        kind: 'REMOVE',
        title: 'Landing page no longer mentions backend processes or cloud providers',
        body: 'Scrubbed every reference to HRRR, XGBoost, NOMADS, MetPy, cfgrib, GRIB, Flask, Python, Cloud Run, Cloud Scheduler, and GCS. Pipeline section now reads as ingest → derive → infer → publish → verify in product-level language only.',
      },
      {
        kind: 'DOCS',
        title: 'Patch notes page',
        body: 'This page. Versioned changelog with kind-tagged entries (NEW / FIX / IMPROVE / REMOVE / DOCS), reverse-chronological, with filter chips so you can scope to just the fixes or just the new features.',
      },
    ],
  },
  {
    version: 'v0.1',
    codename: 'Initial release',
    date: '2026-05-01',
    status: 'INITIAL',
    summary:
      'First public AutoOutlook cut. Hands-off pipeline, SPC-style outlook, and an opinionated retro console UI for forecast hours f00–f48.',
    highlights: [
      'Categorical outlook map (TSTM → HIGH)',
      'Hazard probability boards for tor / hail / wind / flood',
      '3-tier provider chain with mock guard rail',
      'SPC Day 1 cross-check on a 40 km grid',
    ],
    changes: [
      {
        kind: 'NEW',
        title: 'Categorical outlook map',
        body: 'Stepped risk polygons rendered in the SPC convention. TSTM through HIGH bands as concentric annuli — never solid disks. Auto-zoomed to the region of greatest convective interest.',
      },
      {
        kind: 'NEW',
        title: 'Hazard probability boards',
        body: 'Tornado, hail, damaging wind, and excessive rainfall probability surfaces resolved per forecast hour. SIG-severe overlays activate once probabilities clear the 10% EF2+ / 2"+ / 74 mph thresholds.',
      },
      {
        kind: 'NEW',
        title: 'Forecast time scrubber · f00 – f48',
        body: 'Hourly resolution across the full extended cycle window. Play / pause animation, keyboard nav, and a verified-bundle status indicator on every hour.',
      },
      {
        kind: 'NEW',
        title: '3-tier provider chain',
        body: 'Live forecast feed → public-model fallback → deterministic mock. The chain fails downward never upward, and the source badge tells you which tier won.',
      },
      {
        kind: 'NEW',
        title: 'SPC verification',
        body: 'Forecast bundles cross-checked against the official SPC Day 1 outlook on a 40 km grid. Agreement %, underforecast cells, and overforecast cells exposed as first-class telemetry.',
      },
      {
        kind: 'NEW',
        title: 'Auto-generated forecast discussion',
        body: 'Narrative paragraph blending ingredients, composites, and storm-mode signals into operator-readable forecast prose. No LLM, no hallucinations — pure rules.',
      },
      {
        kind: 'NEW',
        title: 'Operator panels',
        body: 'Watch readiness, system status, environmental ingredients grid, risk timeline, and model audit panel. Everything you need to trust or distrust the run on sight.',
      },
      {
        kind: 'DOCS',
        title: 'Documentation set',
        body: 'SPC outlook conventions, hazard probability bands, and retro UI design language documented under the docs view.',
      },
    ],
  },
];

const CHANGE_KINDS: ChangeKind[] = ['NEW', 'FIX', 'IMPROVE', 'REMOVE', 'DOCS'];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const go = viewLinkHandler;

function useUtcClock() {
  const [now, setNow] = useState<Date>(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return useMemo(() => {
    const hh = String(now.getUTCHours()).padStart(2, '0');
    const mm = String(now.getUTCMinutes()).padStart(2, '0');
    const ss = String(now.getUTCSeconds()).padStart(2, '0');
    return `${hh}:${mm}:${ss}Z`;
  }, [now]);
}

function formatDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return iso;
  const month = date.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' }).toUpperCase();
  const day = String(date.getUTCDate()).padStart(2, '0');
  const year = date.getUTCFullYear();
  return `${month} ${day} · ${year}`;
}

function countByKind(release: VersionRelease, kind: ChangeKind): number {
  return release.changes.filter((c) => c.kind === kind).length;
}

function useChangelogReveal() {
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;

    const targets = Array.from(document.querySelectorAll<HTMLElement>('[data-changelog-reveal]'));
    if (!targets.length) return undefined;

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (prefersReducedMotion || !('IntersectionObserver' in window)) {
      targets.forEach((target) => {
        target.dataset.changelogVisible = 'true';
      });
      return undefined;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const target = entry.target as HTMLElement;
          target.dataset.changelogVisible = 'true';
          observer.unobserve(target);
        });
      },
      {
        rootMargin: '0px 0px -10% 0px',
        threshold: 0.12,
      },
    );

    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, []);
}

function revealDelay(ms: number): CSSProperties {
  return { '--changelog-reveal-delay': `${ms}ms` } as CSSProperties;
}

function rowDelay(index: number): CSSProperties {
  return { '--changelog-row-delay': `${Math.min(index, 6) * 55}ms` } as CSSProperties;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ChangelogPage() {
  useChangelogReveal();

  useEffect(() => {
    if (typeof window !== 'undefined') {
      window.scrollTo({ top: 0 });
    }
  }, []);

  const [activeFilters, setActiveFilters] = useState<Set<ChangeKind>>(new Set(CHANGE_KINDS));
  const toggleFilter = (kind: ChangeKind) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      // Never let everything be filtered out.
      if (next.size === 0) return new Set(CHANGE_KINDS);
      return next;
    });
  };

  return (
    <div className="changelog-page min-h-screen bg-paper text-ink">
      <ChangelogNav />
      <main>
        <ChangelogHero />
        <FilterStrip activeFilters={activeFilters} onToggle={toggleFilter} />
        <Timeline activeFilters={activeFilters} />
        <BackLinks />
      </main>
      <ChangelogFooter />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top nav
// ---------------------------------------------------------------------------

function ChangelogNav() {
  const time = useUtcClock();
  const current = RELEASES[0];
  return (
    <header className="changelog-nav sticky top-0 z-40 border-b-[3px] border-ink bg-paper">
      <div className="mx-auto flex max-w-[1400px] items-center gap-4 px-4 py-2.5 sm:px-6">
        <a href="#" onClick={go('')} className="flex items-center gap-3">
          <div className="border-[3px] border-ink bg-ink px-2 py-1 font-mono text-[10px] font-bold tracking-[0.3em] text-paper">
            AO/01
          </div>
          <div className="hidden flex-col leading-none sm:flex">
            <span className="font-display text-lg font-extrabold uppercase tracking-tight">
              Auto<span className="text-signal-amber">Outlook</span>
            </span>
            <span className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.25em] text-ink/60">
              Patch Notes · {current.version}
            </span>
          </div>
        </a>

        <div className="hidden flex-1 items-center justify-center gap-6 md:flex">
          <a href="#" onClick={go('')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Home
          </a>
          <a href="#docs" onClick={go('#docs')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Docs
          </a>
          <a href="#dashboard" onClick={go('#dashboard')} className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink/70 hover:text-ink">
            Dashboard
          </a>
          <span className="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-ink">
            Changelog
          </span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <div className="hidden items-center gap-2 border-[2px] border-ink bg-paper px-2 py-1 font-mono text-[10px] uppercase tracking-[0.25em] text-ink shadow-retro-sm sm:flex">
            <span className="inline-block h-2 w-2 animate-pulse-dot rounded-full bg-signal-lime" aria-hidden />
            <span>UTC {time}</span>
          </div>
          <a
            href="#dashboard"
            onClick={go('#dashboard')}
            className="retro-button retro-button-primary whitespace-nowrap text-[11px]"
          >
            Launch Dashboard ▸
          </a>
        </div>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Hero
// ---------------------------------------------------------------------------

function ChangelogHero() {
  const current = RELEASES[0];
  const previous = RELEASES[1];
  const anchorForVersion = (version: string) => `#release-${version.replace(/\./g, '-')}`;
  return (
    <section className="changelog-atmosphere relative overflow-hidden border-b-[3px] border-ink bg-paper">
      <div className="pointer-events-none absolute inset-0 retro-grid-bg opacity-60" aria-hidden />
      <div className="changelog-drift-field" aria-hidden />
      <div className="changelog-release-beam changelog-release-beam-a" aria-hidden />
      <div className="changelog-release-beam changelog-release-beam-b" aria-hidden />

      <div className="relative mx-auto grid max-w-[1400px] grid-cols-1 gap-8 px-4 py-12 sm:px-6 lg:grid-cols-[1.3fr_1fr] lg:gap-10 lg:py-20">
        <div className="flex flex-col gap-6">
          <div className="changelog-hero-item flex flex-wrap items-center gap-2" style={revealDelay(60)}>
            <RetroBadge tone="ink">[ PATCH NOTES / 00 ]</RetroBadge>
            <RetroBadge tone="lime" pulse>CURRENT · {current.version}</RetroBadge>
            <RetroBadge tone="paper">Updated {formatDate(current.date)}</RetroBadge>
          </div>

          <h1
            className="changelog-hero-item changelog-title font-display font-extrabold uppercase leading-[0.85] tracking-[-0.04em] text-ink"
            style={{ ...revealDelay(135), fontSize: 'clamp(3rem, 9vw, 7.5rem)' }}
          >
            Patch<span className="text-signal-amber">Notes</span>
          </h1>

          <p className="changelog-hero-item max-w-[640px] font-display text-xl font-bold uppercase leading-tight tracking-tight text-ink/80 sm:text-2xl lg:text-3xl" style={revealDelay(215)}>
            What shipped, what broke, what got fixed.
            <br />
            <span className="text-ink/55">{current.version} · {current.codename}.</span>
          </p>

          <p className="changelog-hero-item max-w-[640px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg" style={revealDelay(295)}>
            {current.summary}
          </p>

          <div className="changelog-hero-item flex flex-wrap items-center gap-3 pt-2" style={revealDelay(375)}>
            <a
              href={anchorForVersion(current.version)}
              className="retro-button retro-button-primary changelog-action-button !px-5 !py-3 text-base"
            >
              ▾ Read {current.version} in full
            </a>
            <a
              href={anchorForVersion(previous.version)}
              className="retro-button changelog-action-button !px-5 !py-3 text-base"
            >
              See {previous.version}
            </a>
          </div>

          <dl className="changelog-hero-item mt-6 grid grid-cols-2 gap-px border-[3px] border-ink bg-ink sm:grid-cols-4" style={revealDelay(455)}>
            <HeroStat label="CURRENT" value={current.version} sub={current.codename} />
            <HeroStat label="PREVIOUS" value={previous.version} sub={previous.codename} />
            <HeroStat label="RELEASES" value={String(RELEASES.length)} sub="versions shipped" />
            <HeroStat label="CHANGES" value={String(RELEASES.reduce((sum, r) => sum + r.changes.length, 0))} sub="across all versions" />
          </dl>
        </div>

        {/* Right: version diff panel */}
        <div className="changelog-hero-panel relative">
          <div className="retro-card-lg retro-scanline changelog-diff-panel relative overflow-hidden bg-ink p-0 text-paper">
            <div className="changelog-panel-glow" aria-hidden />
            <div className="changelog-sweep-line" aria-hidden />
            <CornerMarks />
            <div className="flex items-center justify-between border-b-[3px] border-paper/15 px-4 py-2">
              <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
                ◢ DIFF · {previous.version} → {current.version}
              </span>
              <div className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 animate-pulse-dot rounded-full bg-signal-lime" aria-hidden />
                <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/80">LIVE</span>
              </div>
            </div>

            <div className="px-4 py-3">
              <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/50">
                {current.version} highlights
              </span>
              <ul className="mt-3 flex flex-col gap-2">
                {current.highlights.map((h, idx) => (
                  <li key={h} className="changelog-highlight-row flex items-start gap-3" style={rowDelay(idx)}>
                    <span className="mt-1 inline-block h-2 w-2 shrink-0 bg-signal-amber" aria-hidden />
                    <span className="font-sans text-sm leading-snug text-paper/90">{h}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="border-t-[3px] border-paper/15 px-4 py-3">
              <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/50">
                change breakdown · {current.version}
              </span>
              <div className="mt-3 grid grid-cols-5 gap-px border-[2px] border-paper/30 bg-paper/20">
                {CHANGE_KINDS.map((kind) => {
                  const count = countByKind(current, kind);
                  return (
                    <div key={kind} className="changelog-matrix-cell bg-ink p-2 text-center">
                      <div className="font-mono text-[9px] uppercase tracking-[0.25em] text-paper/55">
                        {kind}
                      </div>
                      <div className="mt-1 font-display text-xl font-extrabold leading-none tracking-tight text-paper">
                        {count}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="border-t-[3px] border-paper/15 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
              ▸ READING ORDER · NEWEST FIRST
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function CornerMarks() {
  const cls = 'absolute h-3 w-3 border-paper/70';
  return (
    <>
      <span aria-hidden className={`${cls} left-1.5 top-1.5 border-l-2 border-t-2`} />
      <span aria-hidden className={`${cls} right-1.5 top-1.5 border-r-2 border-t-2`} />
      <span aria-hidden className={`${cls} bottom-1.5 left-1.5 border-b-2 border-l-2`} />
      <span aria-hidden className={`${cls} bottom-1.5 right-1.5 border-b-2 border-r-2`} />
    </>
  );
}

function HeroStat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="changelog-stat bg-paper p-3">
      <div className="font-mono text-[9px] uppercase tracking-[0.3em] text-ink/50">{label}</div>
      <div className="mt-1 font-display text-xl font-extrabold uppercase tracking-tight text-ink">{value}</div>
      {sub && <div className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.2em] text-ink/50">{sub}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter strip
// ---------------------------------------------------------------------------

function FilterStrip({
  activeFilters,
  onToggle,
}: {
  activeFilters: Set<ChangeKind>;
  onToggle: (kind: ChangeKind) => void;
}) {
  return (
    <section className="changelog-filter-bar border-b-[3px] border-ink bg-ink text-paper">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/55">
          ▸ FILTER ·
        </span>
        {CHANGE_KINDS.map((kind) => {
          const active = activeFilters.has(kind);
          const tone = KIND_TONE[kind];
          const baseClass =
            'changelog-filter-button inline-flex items-center gap-2 border-[2px] px-3 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.25em] transition-all';
          const onClass = `${TONE_BORDER[tone]} ${TONE_BG[tone]} ${TONE_TEXT[tone]} shadow-retro-sm`;
          const offClass = 'border-paper/30 bg-transparent text-paper/55 hover:border-paper/60 hover:text-paper';
          return (
            <button
              key={kind}
              type="button"
              onClick={() => onToggle(kind)}
              aria-pressed={active}
              className={`${baseClass} ${active ? onClass : offClass}`}
            >
              <span className="text-[12px] leading-none">{KIND_GLYPH[kind]}</span>
              <span>{kind}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

function Timeline({ activeFilters }: { activeFilters: Set<ChangeKind> }) {
  return (
    <section className="border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-14 sm:px-6 lg:py-20">
        <div className="changelog-reveal" data-changelog-reveal="true">
          <SectionHead tag="RELEASES / 01" title="Versions, newest first." />
          <p className="mt-4 max-w-[760px] font-sans text-base leading-relaxed text-ink/70 sm:text-lg">
            Every release ships as a single bundle — the dashboard, the docs, and the static
            outlook artifacts all move together. Major themes get codenames; everything else lands
            in NEW / FIX / IMPROVE / REMOVE / DOCS.
          </p>
        </div>

        <ol className="mt-12 flex flex-col gap-12">
          {RELEASES.map((release, idx) => (
            <ReleaseCard key={release.version} release={release} index={idx} activeFilters={activeFilters} />
          ))}
        </ol>
      </div>
    </section>
  );
}

function ReleaseCard({
  release,
  index,
  activeFilters,
}: {
  release: VersionRelease;
  index: number;
  activeFilters: Set<ChangeKind>;
}) {
  const visibleChanges = release.changes.filter((c) => activeFilters.has(c.kind));
  const anchor = `release-${release.version.replace(/\./g, '-')}`;
  const statusTone = STATUS_TONE[release.status];

  return (
    <li
      id={anchor}
      className="changelog-release-card changelog-reveal scroll-mt-24 relative grid grid-cols-1 gap-px border-[3px] border-ink bg-ink lg:grid-cols-[280px_1fr]"
      data-changelog-reveal="true"
      style={revealDelay(90 + Math.min(index, 5) * 55)}
    >
      {/* Left rail */}
      <div className="changelog-release-rail flex flex-col gap-4 bg-paper p-5">
        <div className="flex items-center gap-2">
          <RetroBadge tone={statusTone}>{release.status}</RetroBadge>
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/55">
            REL / {String(RELEASES.length - index).padStart(2, '0')}
          </span>
        </div>

        <div
          className="font-display font-extrabold uppercase leading-none tracking-[-0.03em] text-ink"
          style={{ fontSize: 'clamp(2.75rem, 5vw, 4rem)' }}
        >
          {release.version}
        </div>

        <div className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">CODENAME</span>
          <span className="font-display text-lg font-extrabold uppercase leading-tight tracking-tight text-ink">
            {release.codename}
          </span>
        </div>

        <div className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50">SHIPPED</span>
          <span className="font-mono text-sm font-bold uppercase tracking-[0.1em] text-ink">
            {formatDate(release.date)}
          </span>
        </div>

        <div className="mt-1 flex flex-wrap gap-1.5">
          {CHANGE_KINDS.map((kind) => {
            const count = countByKind(release, kind);
            if (count === 0) return null;
            const tone = KIND_TONE[kind];
            return (
              <span
                key={kind}
                className={`changelog-count-chip inline-flex items-center gap-1 border-[2px] border-ink ${TONE_BG[tone]} ${TONE_TEXT[tone]} px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.2em] shadow-retro-sm`}
              >
                <span className="text-[11px] leading-none">{KIND_GLYPH[kind]}</span>
                <span>{kind}</span>
                <span className="ml-0.5">×{count}</span>
              </span>
            );
          })}
        </div>
      </div>

      {/* Right column: summary + change entries */}
      <div className="flex flex-col gap-5 bg-paper p-5 sm:p-6">
        <p className="font-sans text-base leading-relaxed text-ink/75 sm:text-lg">
          {release.summary}
        </p>

        {visibleChanges.length === 0 ? (
          <div className="border-[2px] border-dashed border-ink/30 bg-ink/[0.02] p-4 text-center font-mono text-[10px] uppercase tracking-[0.3em] text-ink/45">
            No entries match the current filter for {release.version}.
          </div>
        ) : (
          <ul className="flex flex-col gap-px border-[3px] border-ink bg-ink">
            {visibleChanges.map((change, i) => (
              <li
                key={`${change.kind}-${i}`}
                className="changelog-change-row grid grid-cols-1 gap-px bg-ink sm:grid-cols-[96px_1fr]"
                style={rowDelay(i)}
              >
                <div className={`flex items-start justify-center ${TONE_BG[KIND_TONE[change.kind]]} px-3 py-3 sm:py-4`}>
                  <span className={`inline-flex items-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.25em] ${TONE_TEXT[KIND_TONE[change.kind]]}`}>
                    <span className="text-[12px] leading-none">{KIND_GLYPH[change.kind]}</span>
                    <span>{change.kind}</span>
                  </span>
                </div>
                <div className="bg-paper px-4 py-3 sm:px-5 sm:py-4">
                  <h3 className="font-display text-base font-extrabold uppercase leading-tight tracking-tight text-ink sm:text-lg">
                    {change.title}
                  </h3>
                  <p className="mt-2 font-sans text-sm leading-relaxed text-ink/70 sm:text-base">
                    {change.body}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Back links
// ---------------------------------------------------------------------------

function BackLinks() {
  return (
    <section className="border-b-[3px] border-ink bg-paper">
      <div className="mx-auto max-w-[1400px] px-4 py-12 sm:px-6 lg:py-16">
        <div className="changelog-reveal grid grid-cols-1 gap-px border-[3px] border-ink bg-ink md:grid-cols-3" data-changelog-reveal="true">
          <BackTile
            href="#"
            onClick={go('')}
            kicker="HOME · 00"
            title="Back to overview"
            body="Capabilities, pipeline, hazards, and stack on the landing page."
          />
          <BackTile
            href="#dashboard"
            onClick={go('#dashboard')}
            kicker="CONSOLE · 01"
            title="Launch dashboard"
            body="The actual outlook map, timelines, and hazard boards for the latest cycle."
          />
          <BackTile
            href="#docs"
            onClick={go('#docs')}
            kicker="DOCS · 02"
            title="Read the documentation"
            body="SPC outlook conventions, hazard probability formulas, and UI language."
          />
        </div>
      </div>
    </section>
  );
}

function BackTile({
  href,
  onClick,
  kicker,
  title,
  body,
}: {
  href: string;
  onClick: (e: { preventDefault: () => void }) => void;
  kicker: string;
  title: string;
  body: string;
}) {
  return (
    <a
      href={href}
      onClick={onClick}
      className="changelog-back-tile group relative flex flex-col gap-3 bg-paper p-5 transition-all hover:bg-ink hover:text-paper"
    >
      <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50 group-hover:text-paper/55">
        ◢ {kicker}
      </span>
      <span className="font-display text-2xl font-extrabold uppercase leading-tight tracking-tight">
        {title}
      </span>
      <span className="font-sans text-sm leading-relaxed text-ink/70 group-hover:text-paper/75">
        {body}
      </span>
      <span className="mt-auto font-mono text-[10px] uppercase tracking-[0.3em] text-ink/50 group-hover:text-signal-amber">
        ▸ OPEN
      </span>
    </a>
  );
}

// ---------------------------------------------------------------------------
// Footer
// ---------------------------------------------------------------------------

function ChangelogFooter() {
  return (
    <footer className="border-t-[3px] border-ink bg-ink text-paper">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
          AutoOutlook · Automated Convective Risk Intelligence · v1.2
        </span>
        <div className="flex flex-wrap items-center gap-4 font-mono text-[10px] uppercase tracking-[0.3em] text-paper/40">
          <a href="#" onClick={go('')} className="hover:text-paper">Home</a>
          <a href="#dashboard" onClick={go('#dashboard')} className="hover:text-paper">Dashboard</a>
          <a href="#docs" onClick={go('#docs')} className="hover:text-paper">Docs</a>
          <span>LIVE → FALLBACK → MOCK</span>
        </div>
      </div>
    </footer>
  );
}

// ---------------------------------------------------------------------------
// Shared: section heading
// ---------------------------------------------------------------------------

function SectionHead({ tag, title }: { tag: string; title: string }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.35em] text-ink/55">
        <span className="inline-block h-2 w-2 bg-ink" aria-hidden />
        <span>[ {tag} ]</span>
        <span className="h-px flex-1 bg-ink/15" />
      </div>
      <h2
        className="font-display font-extrabold uppercase leading-[0.95] tracking-[-0.03em] text-ink"
        style={{ fontSize: 'clamp(2rem, 5vw, 4rem)' }}
      >
        {title}
      </h2>
    </div>
  );
}
