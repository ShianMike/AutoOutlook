// discussionGenerator: composes an SPC-style forecast discussion for a
// given hourly snapshot.  Structure mirrors the real SPC Day-1 text
// narrative: summary → synopsis → mesoscale → discussion → hazards →
// uncertainty.  Each builder uses data-driven branching so the output
// reads differently across parameter-space.

import type { HazardKey, HourSnapshot, Ingredients, RiskCategory, SignalStrength, StormMode } from '../types/forecast';

// ── Helpers ──────────────────────────────────────────────────────────
const r = Math.round;

const HAZARD_TAG: Record<HazardKey, string> = {
  tornado: 'tornadoes',
  hail:    'large hail',
  wind:    'damaging wind gusts',
  flood:   'flash flooding',
};

const HAZARD_ADJ: Record<HazardKey, string> = {
  tornado: 'tornado',
  hail:    'hail',
  wind:    'wind',
  flood:   'flood',
};

type Period = 'morning' | 'afternoon' | 'evening' | 'overnight';

function periodFromISO(iso: string): Period {
  const utcHour = new Date(iso).getUTCHours();
  if (utcHour >= 6 && utcHour < 12)  return 'morning';
  if (utcHour >= 12 && utcHour < 18) return 'afternoon';
  if (utcHour >= 18 && utcHour < 24) return 'evening';
  return 'overnight';  // 00-05 UTC
}

function periodLabel(p: Period): string {
  return p === 'morning' ? 'this morning' : p === 'afternoon' ? 'this afternoon' : p === 'evening' ? 'this evening' : 'overnight';
}

// ── 1. Summary (SPC header line) ─────────────────────────────────────
function summaryLine(snap: HourSnapshot): string {
  const out = snap.outlook;
  const region = snap.region.label;
  const states = snap.region.states.length > 0 ? ` (${snap.region.states.join('/')})` : '';

  if (out.category === 'TSTM') {
    return `...${region}${states}...\nGeneral thunderstorms expected with minimal severe potential. Isolated stronger cells cannot be ruled out where instability is greatest.`;
  }

  const active = (Object.keys(snap.hazards) as HazardKey[])
    .filter((k) => snap.hazards[k].level !== 'TSTM')
    .sort((a, b) => snap.hazards[b].probability - snap.hazards[a].probability);
  const threats = active.length > 0
    ? active.map((k) => HAZARD_TAG[k]).join(', ')
    : HAZARD_TAG[out.mainHazard];

  const sigTag = out.significantSevere ? '\nSignificant severe weather is possible.' : '';
  return `...${region}${states}...\n${out.headline}${sigTag}\nPrimary threats: ${threats}.`;
}

// ── 2. Synopsis – data-driven synoptic narrative ─────────────────────
function buildSynopsis(snap: HourSnapshot): string {
  const ing = snap.ingredients;
  const period = periodFromISO(snap.validTimeISO);
  const parts: string[] = [];

  // Upper-level pattern
  if (ing.shear06Kt >= 40) {
    parts.push('An amplified shortwave trough is translating across the region with strong mid-level flow and diffluent upper jet.');
  } else if (ing.shear06Kt >= 25) {
    parts.push('A progressive shortwave trough provides modest upper-level forcing across the focus area.');
  } else {
    parts.push('Upper-level flow is relatively weak with only subtle height perturbations noted in the short-range models.');
  }

  // Surface boundary / forcing
  if (snap.surfaceBoundary) {
    const b = snap.surfaceBoundary;
    if (b.kind === 'triple-point') {
      parts.push('A surface triple point marks the intersection of the dryline, warm front, and cold front — a preferred corridor for storm initiation and mesocyclone development.');
    } else if (b.kind === 'dryline') {
      parts.push('A dryline sharpens through the afternoon across the southern Plains, focusing convergence and storm initiation along the boundary.');
    } else {
      parts.push('A frontal boundary provides a primary focus for storm initiation as differential heating enhances convergence along the zone.');
    }
  } else if (ing.frontSignal === 'strong') {
    parts.push('Strong large-scale forcing via an approaching front will help erode the capping inversion and trigger convection.');
  } else if (ing.frontSignal === 'moderate') {
    parts.push('Moderate forcing is expected from synoptic-scale lift and local convergence features.');
  } else if (ing.frontSignal === 'weak') {
    parts.push('Forcing is marginal — convection will be dependent on terrain interactions and local boundary-layer convergence.');
  } else {
    parts.push('No significant forcing mechanism is identified; convection will be primarily surface-driven and diurnal.');
  }

  // Time-of-day evolution
  if (period === 'morning') {
    parts.push('Morning convection may be elevated as the boundary layer is not yet fully mixed.');
  } else if (period === 'afternoon') {
    parts.push('Peak heating through the afternoon will destabilize the boundary layer and promote surface-based convection.');
  } else if (period === 'evening') {
    parts.push('Ongoing storms will continue to interact with the deepening low-level jet as the surface boundary layer stabilizes.');
  } else {
    parts.push('The nocturnal low-level jet will strengthen moisture transport and may sustain or reinitiate convection along the outflow boundary.');
  }

  return `...SYNOPSIS...\n${parts.join(' ')}`;
}

// ── 3. Mesoscale analysis ────────────────────────────────────────────
function buildMesoscale(snap: HourSnapshot): string {
  const ing = snap.ingredients;
  const parts: string[] = [];

  // Low-level moisture / convergence
  if (ing.sfcDewpointF >= 70 && ing.pwatIn >= 1.6) {
    parts.push(`A very moist boundary layer is in place with surface dewpoints of ${r(ing.sfcDewpointF)}°F and precipitable water values of ${ing.pwatIn.toFixed(2)}″, well above climatological norms. The PWAT-derived moisture-depth proxy is near ${r(ing.moistureDepthM)}m, supporting heavy rainfall potential.`);
  } else if (ing.sfcDewpointF >= 65 && ing.pwatIn >= 1.3) {
    parts.push(`Adequate low-level moisture (Td ${r(ing.sfcDewpointF)}°F, PWAT ${ing.pwatIn.toFixed(2)}″) is pooling across the focus region, with the PWAT-derived moisture proxy near ${r(ing.moistureDepthM)}m.`);
  } else if (ing.sfcDewpointF >= 55) {
    parts.push(`Marginal moisture (Td ${r(ing.sfcDewpointF)}°F, PWAT ${ing.pwatIn.toFixed(2)}″) limits the moisture-depth proxy to ${r(ing.moistureDepthM)}m. Storm coverage will be limited by the marginal moisture return.`);
  } else {
    parts.push(`A dry boundary layer (Td ${r(ing.sfcDewpointF)}°F) inhibits surface-based convection. Any storms that develop will likely be elevated above the frontal surface.`);
  }

  // LCL heights — tornado / storm base implications
  if (ing.lclM <= 800) {
    parts.push(`Very low LCL heights (${r(ing.lclM)}m AGL) indicate a saturated sub-cloud layer favorable for tornado development and reduced entrainment of dry air into storm updrafts.`);
  } else if (ing.lclM <= 1200) {
    parts.push(`LCL heights near ${r(ing.lclM)}m AGL are in the favorable range for tornado potential, with relatively short cloud-base-to-ground distances.`);
  } else if (ing.lclM <= 1800) {
    parts.push(`Moderate LCL heights (${r(ing.lclM)}m AGL) reduce tornado probability but do not preclude it entirely; hail and wind remain the more likely hazards.`);
  } else {
    parts.push(`Elevated LCLs (${r(ing.lclM)}m AGL) are unfavorable for tornadoes. The elevated cloud bases support high-based storms with strong downdraft potential and gusty outflow winds.`);
  }

  // Initiation confidence
  if (ing.initiationConf >= 0.8) {
    parts.push(`Initiation confidence is high — storms are expected to develop ${periodLabel(periodFromISO(snap.validTimeISO))}.`);
  } else if (ing.initiationConf >= 0.5) {
    parts.push(`Initiation appears probable but conditional on boundary interactions and the progression of surface heating.`);
  } else {
    parts.push(`Initiation confidence is low and this outlook is highly conditional. Bust potential is significant if forcing fails to overcome the cap.`);
  }

  return `...MESOSCALE...\n${parts.join(' ')}`;
}

// ── 4. Technical discussion ──────────────────────────────────────────
function buildDiscussion(snap: HourSnapshot): string {
  const ing = snap.ingredients;
  const out = snap.outlook;
  const parts: string[] = [];

  // ── Instability ──
  if (ing.mlcape >= 4000) {
    parts.push(`Extreme instability dominates the thermodynamic environment with MLCAPE of ${r(ing.mlcape)} J/kg (MUCAPE ${r(ing.mucape)}, SBCAPE ${r(ing.sbcape)}). Updraft speeds will support very large hail and intense rotation. Storms developing in this environment will be explosive.`);
  } else if (ing.mlcape >= 3000) {
    parts.push(`Robust instability (MLCAPE ${r(ing.mlcape)} J/kg, MUCAPE ${r(ing.mucape)} J/kg) will support vigorous updrafts capable of producing significant hail and sustaining supercell structures.`);
  } else if (ing.mlcape >= 2000) {
    parts.push(`Strong instability is in place (MLCAPE ${r(ing.mlcape)} J/kg) supporting organized deep convection. MUCAPE of ${r(ing.mucape)} J/kg suggests potential for elevated supercell activity.`);
  } else if (ing.mlcape >= 1000) {
    parts.push(`Moderate instability (MLCAPE ${r(ing.mlcape)} J/kg, SBCAPE ${r(ing.sbcape)} J/kg) is sufficient to sustain organized convection, though updraft intensity will be limited compared to higher-CAPE environments.`);
  } else if (ing.mlcape >= 500) {
    parts.push(`Weak to moderate instability (MLCAPE ${r(ing.mlcape)} J/kg) limits overall updraft strength. Severe potential hinges on strong shear compensating for the modest thermodynamic environment.`);
  } else {
    parts.push(`Marginal instability (MLCAPE ${r(ing.mlcape)} J/kg) constrains the severe weather potential to isolated stronger cells where localized enhancements in forcing or moisture occur.`);
  }

  // ── Kinematic environment ──
  if (ing.shear06Kt >= 50 && ing.srh01 >= 200) {
    parts.push(`An exceptionally favorable kinematic environment exists with surface-to-500 mb shear of ${r(ing.shear06Kt)} kt and 0-1 km SRH of ${r(ing.srh01)} m²/s². The 0-3 km SRH of ${r(ing.srh03)} m²/s² further supports significant mesocyclone development. The storm-relative wind proxy is ${r(ing.stormRelWindKt)} kt.`);
  } else if (ing.shear06Kt >= 50) {
    parts.push(`Very strong surface-to-500 mb shear (${r(ing.shear06Kt)} kt) favors organized storms. The 0-1 km SRH (${r(ing.srh01)} m²/s²) and storm-relative wind proxy (${r(ing.stormRelWindKt)} kt) support organized storm structures with potential for severe weather.`);
  } else if (ing.shear06Kt >= 40 && ing.srh01 >= 150) {
    parts.push(`Strong surface-to-500 mb shear (${r(ing.shear06Kt)} kt) and enhanced low-level SRH (${r(ing.srh01)} m²/s²) favor supercell development with persistent mesocyclones. The 0-3 km SRH (${r(ing.srh03)} m²/s²) further supports rotating updrafts.`);
  } else if (ing.shear06Kt >= 35) {
    parts.push(`Strong surface-to-500 mb shear (${r(ing.shear06Kt)} kt) supports organized storm structures. SRH values of ${r(ing.srh01)} m²/s² (0-1 km) and ${r(ing.srh03)} m²/s² (0-3 km) suggest potential for rotating updrafts.`);
  } else if (ing.shear06Kt >= 25) {
    parts.push(`Moderate shear (${r(ing.shear06Kt)} kt) supports multicell to mixed-mode convection. Low-level SRH of ${r(ing.srh01)} m²/s² provides some potential for transient rotation, though sustained supercells are unlikely.`);
  } else {
    parts.push(`Weak shear (${r(ing.shear06Kt)} kt) and low SRH (${r(ing.srh01)} m²/s²) favor disorganized pulse-type storms. Severe potential is primarily limited to microbursts and brief gusty winds from collapsing cells.`);
  }

  // ── Capping inversion ──
  if (ing.capStrength === 'strong') {
    parts.push(`A robust capping inversion (CIN ${r(ing.cin)} J/kg) will substantially limit storm coverage. This outlook is conditional on cap removal; significant bust potential exists if mesoscale forcing fails to breach the inversion. However, storms that do break through will likely be explosive given the potential energy stored beneath the cap.`);
  } else if (ing.capStrength === 'moderate') {
    parts.push(`A moderate cap (CIN ${r(ing.cin)} J/kg) will limit early initiation but should erode through the period as forcing increases. Once storms develop, the loaded-gun profile beneath the cap will support rapid intensification.`);
  } else if (ing.capStrength === 'weak') {
    parts.push(`A weak capping inversion (CIN ${r(ing.cin)} J/kg) will be easily overcome during peak heating, allowing widespread convective initiation where moisture is sufficient.`);
  } else {
    parts.push(`Negligible capping (CIN ${r(ing.cin)} J/kg) allows unrestricted convective development. Storms may initiate early and be numerous, though this may limit individual storm intensity through competition for inflow.`);
  }

  // ── Composite parameters ──
  const composites: string[] = [];
  if (ing.stp >= 3) {
    composites.push(`STP of ${ing.stp.toFixed(1)} is well above the significant tornado threshold`);
  } else if (ing.stp >= 1) {
    composites.push(`STP of ${ing.stp.toFixed(1)} exceeds the tornado parameter threshold`);
  }
  if (ing.scp >= 4) {
    composites.push(`SCP of ${ing.scp.toFixed(1)} strongly favors supercell development`);
  } else if (ing.scp >= 1) {
    composites.push(`SCP of ${ing.scp.toFixed(1)} supports supercell potential`);
  }
  if (ing.ship >= 1.5) {
    composites.push(`SHIP index of ${ing.ship.toFixed(1)} suggests significant hail risk`);
  }
  if (ing.ehi >= 2) {
    composites.push(`EHI of ${ing.ehi.toFixed(1)} indicates favorable conditions for significant tornadoes`);
  } else if (ing.ehi >= 1) {
    composites.push(`EHI of ${ing.ehi.toFixed(1)} indicates tornado potential`);
  }
  if (composites.length > 0) {
    parts.push(`Composite parameters reinforce the hazard assessment: ${composites.join('; ')}.`);
  }

  // ── Storm mode & evolution ──
  parts.push(stormModeDiscussion(ing.stormMode, ing, snap));

  return `...DISCUSSION...\n${parts.join(' ')}`;
}

function stormModeDiscussion(mode: StormMode, ing: Ingredients, snap: HourSnapshot): string {
  const period = periodFromISO(snap.validTimeISO);
  const mainHz = HAZARD_ADJ[snap.outlook.mainHazard];

  if (mode === 'discrete') {
    if (ing.shear06Kt >= 40) {
      return `Storm mode is expected to be discrete supercells, the most dangerous configuration for ${mainHz} production. ${period === 'afternoon' ? 'Initial storms along the dryline or boundary should remain isolated and intense through the peak heating hours.' : period === 'evening' ? 'Supercells may persist into the evening hours before eventual merger into a larger MCS.' : 'Discrete storms are expected to dominate the convective mode during this period.'}`;
    }
    return `Discrete cells are expected, supporting the primary ${mainHz} threat. Some storm mergers may occur later in the period, leading to a transition toward multicell structures.`;
  }
  if (mode === 'linear') {
    return `A linear convective mode (QLCS) is expected, favoring widespread damaging wind gusts along the leading gust front. Embedded LEWP/bow-echo structures may produce localized wind swaths exceeding 75 mph. Brief spin-up tornadoes are possible along the leading edge, particularly where low-level SRH is enhanced (${r(ing.srh01)} m²/s²).`;
  }
  if (mode === 'multicell') {
    return `A multicell cluster mode is anticipated with individual updrafts competing for available instability. The primary severe threat is large hail from stronger updraft cores within the cluster, with damaging outflow winds possible as cells merge. Organized rotation is less likely in this mode, though brief tornadoes cannot be ruled out.`;
  }
  // mixed
  return `A mixed storm mode is expected, with initial discrete cells possible along the boundary before upscale growth into multicell clusters or a broken line. The mixed mode complicates the hazard profile — early discrete storms may favor ${mainHz}, while later upscale growth shifts the primary threat toward damaging wind. Forecast confidence is reduced by the mode ambiguity.`;
}

// ── 5. Per-hazard detailed callouts ──────────────────────────────────
function buildHazardSection(snap: HourSnapshot): string {
  const keys: HazardKey[] = ['tornado', 'hail', 'wind', 'flood'];
  const parts: string[] = [];
  const ing = snap.ingredients;

  for (const k of keys) {
    const h = snap.hazards[k];
    if (h.level === 'TSTM' && h.probability < 0.02) continue;

    const pct = r(h.probability * 100);
    const sigNote = h.significantSevere ? ' (SIGNIFICANT — 10%+ for EF2+/74mph+/2″+)' : '';

    if (k === 'tornado') {
      if (pct >= 15) {
        parts.push(`TORNADO: ${pct}% probability within 25 mi of a point${sigNote}. The combination of STP ${ing.stp.toFixed(1)}, 0-1 km SRH ${r(ing.srh01)} m²/s², and low LCLs (${r(ing.lclM)}m) supports a notable tornado threat including the potential for strong (EF2+) tornadoes.`);
      } else if (pct >= 5) {
        parts.push(`TORNADO: ${pct}% probability within 25 mi${sigNote}. Low-level shear (SRH ${r(ing.srh01)} m²/s²) and LCL heights of ${r(ing.lclM)}m support a conditional tornado risk, primarily with discrete supercells.`);
      } else if (pct >= 2) {
        parts.push(`TORNADO: ${pct}% probability within 25 mi. Marginal tornado potential exists with brief spin-up vortices possible along boundaries or within QLCS segments.`);
      }
    } else if (k === 'hail') {
      if (pct >= 30) {
        parts.push(`HAIL: ${pct}% probability within 25 mi${sigNote}. Strong updrafts supported by ${r(ing.mucape)} J/kg MUCAPE and ${r(ing.shear06Kt)} kt surface-to-500 mb shear can sustain large hail growth zones. SHIP index of ${ing.ship.toFixed(1)} confirms significant hail potential.`);
      } else if (pct >= 15) {
        parts.push(`HAIL: ${pct}% probability within 25 mi${sigNote}. Hail up to golf-ball size is possible with the strongest updraft cores. The storm-relative wind proxy is ${r(ing.stormRelWindKt)} kt.`);
      } else if (pct >= 5) {
        parts.push(`HAIL: ${pct}% probability within 25 mi. Marginal to quarter-size hail is possible with stronger storm cells.`);
      }
    } else if (k === 'wind') {
      if (pct >= 30) {
        parts.push(`WIND: ${pct}% probability within 25 mi${sigNote}. ${ing.stormMode === 'linear' ? 'The QLCS structure will support a broad swath of damaging winds with embedded bow-echo segments capable of producing 75+ mph gusts.' : 'Strong storm-relative winds and steep mid-level lapse rates support potent downdraft acceleration and widespread damaging gusts.'}`);
      } else if (pct >= 15) {
        parts.push(`WIND: ${pct}% probability within 25 mi${sigNote}. Damaging outflow gusts of 60-75 mph are expected from collapsing storms and organized downdraft channels.`);
      } else if (pct >= 5) {
        parts.push(`WIND: ${pct}% probability within 25 mi. Localized damaging gusts near 60 mph are possible with stronger convective cells.`);
      }
    } else if (k === 'flood') {
      if (pct >= 15) {
        parts.push(`FLOOD: ${pct}% probability within 25 mi. PWAT values of ${ing.pwatIn.toFixed(2)}″ combined with ${ing.stormMode === 'linear' ? 'training cells along the boundary' : 'slow storm motion'} support heavy rainfall rates exceeding 2″/hr. Flash flooding is the primary concern in urbanized areas and low-water crossings.`);
      } else if (pct >= 5) {
        parts.push(`FLOOD: ${pct}% probability within 25 mi. Locally heavy rainfall is possible with PWAT of ${ing.pwatIn.toFixed(2)}″. Slow-moving storms may produce rainfall accumulations sufficient for localized flash flooding.`);
      }
    }
  }

  if (parts.length === 0) return '';
  return `...HAZARD PROBABILITIES...\n${parts.join('\n')}`;
}

// ── 6. Confidence / uncertainty ──────────────────────────────────────
function buildUncertainty(snap: HourSnapshot): string {
  const out = snap.outlook;
  const ing = snap.ingredients;
  const confPct = r(out.confidence * 100);
  const parts: string[] = [];

  // Overall confidence statement
  if (out.category === 'TSTM') {
    parts.push(`Overall forecast confidence is ${confPct}%. Severe potential is low, but isolated stronger cells cannot be ruled out where localized enhancements in shear or instability occur.`);
  } else if (out.confidence >= 0.80) {
    parts.push(`Forecast confidence is high (${confPct}%) given excellent alignment of instability, shear, moisture, and forcing. The main uncertainty is coverage and exact storm timing.`);
  } else if (out.confidence >= 0.65) {
    parts.push(`Forecast confidence is moderate to high (${confPct}%). The overall severe threat is well-supported, though storm mode and exact hazard distribution carry some uncertainty.`);
  } else if (out.confidence >= 0.50) {
    parts.push(`Forecast confidence is moderate (${confPct}%). Storm mode, coverage, and timing remain the main forecast uncertainties.`);
  } else if (out.confidence >= 0.35) {
    parts.push(`Forecast confidence is low to moderate (${confPct}%). Conflicting signals between environmental parameters introduce notable uncertainty; the outlook is scenario-dependent.`);
  } else {
    parts.push(`Forecast confidence is low (${confPct}%). The severe weather potential is highly conditional and significant bust potential exists.`);
  }

  // Specific uncertainty drivers
  const drivers: string[] = [];
  if (ing.capStrength === 'strong' && ing.frontSignal !== 'strong') {
    drivers.push('cap removal with insufficient forcing');
  }
  if (ing.sfcDewpointF < 55) {
    drivers.push('insufficient boundary-layer moisture');
  }
  if (ing.initiationConf < 0.5) {
    drivers.push('uncertain convective initiation');
  }
  if (ing.stormMode === 'mixed') {
    drivers.push('ambiguous storm mode');
  }
  if (ing.capStrength === 'none' && ing.mlcape >= 2000) {
    drivers.push('uncapped environment may lead to early widespread initiation reducing individual storm intensity');
  }

  if (drivers.length > 0) {
    parts.push(`Key uncertainty drivers: ${drivers.join('; ')}.`);
  }

  // Upside / downside scenarios
  if (out.category !== 'TSTM' && out.confidence < 0.70) {
    const upside = ing.mlcape >= 2000 && ing.shear06Kt >= 35
      ? 'If storms develop in the most favorable instability/shear overlap, an upgrade in the categorical outlook may be warranted.'
      : 'A narrower but more intense storm corridor than depicted is possible if mesoscale features focus initiation.';
    const downside = ing.capStrength === 'strong'
      ? 'Significant bust potential exists if the cap fails to erode — a null severe event is within the range of outcomes.'
      : 'A downgrade is possible if storms congeal into an amorphous rain mass and fail to maintain organized severe-producing structures.';
    parts.push(`UPSIDE SCENARIO: ${upside}`);
    parts.push(`DOWNSIDE SCENARIO: ${downside}`);
  }

  return `...UNCERTAINTY...\n${parts.join(' ')}`;
}

// ── Public entry point ──────────────────────────────────────────────
export function generateDiscussion(snap: HourSnapshot): string {
  const sections = [
    summaryLine(snap),
    buildSynopsis(snap),
    buildMesoscale(snap),
    buildDiscussion(snap),
    buildHazardSection(snap),
    buildUncertainty(snap),
  ].filter(Boolean);

  return sections.join('\n\n');
}
