// discussionGenerator: composes an SPC-style forecast discussion for a
// given hourly snapshot.  Structure mirrors the real SPC Day-1 text
// narrative: summary → synopsis → mesoscale → discussion → hazards →
// uncertainty.  Each builder uses data-driven branching so the output
// reads differently across parameter-space.

import type { HazardKey, HourSnapshot, Ingredients, RiskCategory, SignalStrength, StormMode } from '../types/forecast';
import { focusLocationFromSnapshot } from './focusLocation';

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

function discussionFocus(snap: HourSnapshot): string {
  const focus = focusLocationFromSnapshot(snap);
  const location = focus.usesCoordinateLabel ? focus.label : `${focus.label} (${focus.coord})`;
  return focus.states ? `${location} (${focus.states})` : location;
}

function dominantHazard(snap: HourSnapshot): HazardKey {
  return (Object.keys(snap.hazards) as HazardKey[])
    .sort((a, b) => snap.hazards[b].probability - snap.hazards[a].probability)[0] ?? snap.outlook.mainHazard;
}

function categoryContext(snap: HourSnapshot): string {
  const hazard = HAZARD_TAG[dominantHazard(snap)];
  const categoryText: Record<RiskCategory, string> = {
    TSTM: 'The severe signal is low. Any stronger storm should be brief and local.',
    MRGL: `This is a low-coverage severe setup, with isolated ${hazard} possible if storms can briefly organize.`,
    SLGT: `A few organized severe storms are possible, but they should not cover the whole area. ${hazard} is the main concern.`,
    ENH: `A more focused area of severe storms is showing up near the target region, with ${hazard} likely the main concern.`,
    MOD: `A higher-end severe setup is possible, with many severe storms or a few intense storms if details line up.`,
    HIGH: `A major severe weather setup is showing up, with high confidence in many intense storms if the forecast holds.`,
  };
  return categoryText[snap.outlook.category];
}

function boundaryScenario(snap: HourSnapshot): string {
  const ing = snap.ingredients;
  if (snap.surfaceBoundary?.kind === 'triple-point') {
    return 'The boundary intersection raises concern for stronger turning winds and a narrow area of higher tornado potential.';
  }
  if (snap.surfaceBoundary?.kind === 'dryline') {
    return ing.capStrength === 'strong'
      ? 'Storm development along the dryline is still uncertain because warm air aloft may block storms until forcing becomes stronger.'
      : 'The dryline should focus storm development, with early storms more likely to stay separate if they do not merge quickly.';
  }
  if (snap.surfaceBoundary?.kind === 'frontal') {
    return ing.stormMode === 'linear'
      ? 'The front favors storms growing into a line, which would make damaging wind more important.'
      : 'The front gives storms a broad place to form, but smaller boundary overlaps could still support stronger rotating storms.';
  }
  if (ing.frontSignal === 'strong' && ing.capStrength !== 'strong') {
    return 'Lift looks strong enough to break the cap, so storms are less likely to stay completely suppressed.';
  }
  if (ing.frontSignal === 'none' && ing.initiationConf < 0.45) {
    return 'The main missing piece is lift. Without a boundary or old outflow, storms may not fully develop.';
  }
  return 'Small-scale boundaries will help decide exactly where the strongest storms form.';
}

function thermodynamicScenario(snap: HourSnapshot): string {
  const ing = snap.ingredients;
  const period = periodFromISO(snap.validTimeISO);
  const elevatedPotential = ing.mucape - ing.sbcape >= 600 || (period === 'morning' && ing.sbcape < ing.mucape * 0.65);
  if (elevatedPotential && ing.shear06Kt >= 35) {
    return `Instability above the ground is stronger than near the surface by about ${r(ing.mucape - ing.sbcape)} J/kg, so hail storms could continue even if surface air is not fully involved.`;
  }
  if (ing.mlcape >= 2500 && ing.shear06Kt < 25) {
    return 'Instability is stronger than the wind support. Storms could become intense for a short time, but they may struggle to stay organized.';
  }
  if (ing.mlcape < 1000 && ing.shear06Kt >= 40) {
    return 'This is a low-CAPE, high-shear setup. There may not be many storms, but any storm that lasts could organize quickly.';
  }
  if (ing.sfcDewpointF >= 68 && ing.lclM <= 1000 && ing.srh01 >= 125) {
    return 'Moist air, low cloud bases, and turning winds overlap, so any separate storm would have higher tornado concern.';
  }
  if (ing.sfcDewpointF < 55 && ing.lclM >= 1800) {
    return 'Dry low-level air and high cloud bases favor strong outflow winds more than tornadoes.';
  }
  return 'The setup supports the main hazard where instability, moisture, and lift overlap best.';
}

function hazardScenario(snap: HourSnapshot): string {
  const ing = snap.ingredients;
  const hazard = dominantHazard(snap);
  if (hazard === 'tornado') {
    if (ing.stp >= 2 && ing.lclM <= 1000 && ing.srh01 >= 150) {
      return 'The tornado risk depends on storms staying separate while they move through the best low-level wind area. Storm mergers would lower the threat but not remove it.';
    }
    return 'Tornado potential is more conditional, tied to local wind shifts, boundary interaction, or brief spin-ups in a line of storms.';
  }
  if (hazard === 'hail') {
    if (ing.mucape >= 2000 && ing.shear06Kt >= 35) {
      return 'The hail risk is supported by strong rising air and enough wind shear to keep storm updrafts going.';
    }
    return 'Hail risk depends on short bursts of stronger storm growth if instability or shear is only borderline.';
  }
  if (hazard === 'wind') {
    if (ing.stormMode === 'linear' || (ing.pwatIn >= 1.4 && ing.shear06Kt >= 35)) {
      return 'The wind risk increases if storms merge into a forward-moving line with stronger rain-cooled air pushing ahead.';
    }
    return 'Damaging wind risk looks more local, mainly from heavy rain cores collapsing and pushing strong gusts outward.';
  }
  if (ing.pwatIn >= 1.6) {
    return 'Flood risk is supported by deep moisture and heavy rain, especially if storms move over the same places repeatedly.';
  }
  return 'Flooding is a lower concern unless storms repeat over the same area.';
}

function evolutionScenario(snap: HourSnapshot): string {
  const ing = snap.ingredients;
  const period = periodFromISO(snap.validTimeISO);
  if (period === 'evening' && ing.srh01 >= 120 && ing.sfcDewpointF >= 62) {
    return 'Stronger evening low-level winds may keep warm, moist air feeding storms after sunset.';
  }
  if (period === 'overnight' && ing.mucape >= 1000 && ing.shear06Kt >= 35) {
    return 'Overnight severe risk is more likely to come from storms above the surface or from a storm complex, with hail and wind favored.';
  }
  if (ing.capStrength === 'none' && ing.mlcape >= 1500) {
    return 'Storms may start early and become numerous, which could limit how intense any one storm becomes.';
  }
  if (ing.capStrength === 'strong' && ing.initiationConf >= 0.55) {
    return 'If lift breaks the cap, storms may be fewer but stronger.';
  }
  return 'The forecast depends on whether storms stay separate or quickly merge into clusters.';
}

function watchScenario(snap: HourSnapshot): string {
  const ing = snap.ingredients;
  const out = snap.outlook;
  const strongestHazard = dominantHazard(snap);
  if (out.category === 'HIGH' || out.category === 'MOD') {
    return `A higher-end watch message would focus on ${HAZARD_TAG[strongestHazard]} and the expected number of organized severe storms.`;
  }
  if (out.category === 'ENH' && out.confidence >= 0.7) {
    return `A watch would be more likely if storms become sustained in the max-risk area, especially for ${HAZARD_TAG[strongestHazard]}.`;
  }
  if (out.category === 'SLGT' && ing.initiationConf >= 0.65 && ing.shear06Kt >= 35) {
    return 'A severe thunderstorm watch is possible if storms start and stay organized.';
  }
  if (out.category === 'MRGL' || out.confidence < 0.5) {
    return 'Watch confidence is limited. Radar, satellite, and boundary trends would need to show stronger storm organization.';
  }
  return 'Short-term trends will decide whether the threat stays local or becomes organized enough for a watch.';
}

// ── 1. Summary (SPC header line) ─────────────────────────────────────
function summaryLine(snap: HourSnapshot): string {
  const out = snap.outlook;
  const region = discussionFocus(snap);

  if (out.category === 'TSTM') {
    return `...${region}...\nGeneral thunderstorms expected with minimal severe potential. Isolated stronger cells cannot be ruled out where instability is greatest.`;
  }

  const active = (Object.keys(snap.hazards) as HazardKey[])
    .filter((k) => snap.hazards[k].level !== 'TSTM')
    .sort((a, b) => snap.hazards[b].probability - snap.hazards[a].probability);
  const threats = active.length > 0
    ? active.map((k) => HAZARD_TAG[k]).join(', ')
    : HAZARD_TAG[out.mainHazard];

  const sigTag = out.significantSevere ? '\nSignificant severe weather is possible.' : '';
  return `...${region}...\n${out.headline}${sigTag}\nPrimary threats: ${threats}.`;
}

// ── 2. Synopsis – data-driven synoptic narrative ─────────────────────
function buildSynopsis(snap: HourSnapshot): string {
  const ing = snap.ingredients;
  const period = periodFromISO(snap.validTimeISO);
  const focus = discussionFocus(snap);
  const parts: string[] = [];

  parts.push(`The main forecast focus is near ${focus}, where the strongest risk signal is showing for this hour.`);
  parts.push(categoryContext(snap));

  // Upper-level pattern
  if (ing.shear06Kt >= 40) {
    parts.push('A stronger upper-level disturbance is moving across the region with strong winds aloft.');
  } else if (ing.shear06Kt >= 25) {
    parts.push('A weaker upper-level disturbance should provide some lift near the focus area.');
  } else {
    parts.push('Upper-level support is weak, so storms will need help from local boundaries or daytime heating.');
  }

  // Surface boundary / forcing
  if (snap.surfaceBoundary) {
    const b = snap.surfaceBoundary;
    if (b.kind === 'triple-point') {
      parts.push('A boundary intersection may focus storm development and locally increase tornado concern.');
    } else if (b.kind === 'dryline') {
      parts.push('A dryline should sharpen through the day and help focus storm development.');
    } else {
      parts.push('A front should provide the main focus for storm development.');
    }
  } else if (ing.frontSignal === 'strong') {
    parts.push('Strong lift from an approaching front should help break the cap and start storms.');
  } else if (ing.frontSignal === 'moderate') {
    parts.push('Moderate lift is expected from the larger weather pattern and local boundaries.');
  } else if (ing.frontSignal === 'weak') {
    parts.push('Lift is weak, so storms will depend on local boundaries or terrain.');
  } else {
    parts.push('No clear trigger is showing, so storms would mainly depend on daytime heating.');
  }
  parts.push(boundaryScenario(snap));

  // Time-of-day evolution
  if (period === 'morning') {
    parts.push('Morning storms may be rooted above the ground before surface heating improves.');
  } else if (period === 'afternoon') {
    parts.push('Afternoon heating should make the air near the ground more unstable and help storms form.');
  } else if (period === 'evening') {
    parts.push('Evening storms may keep going as low-level winds strengthen, even while the surface starts to cool.');
  } else {
    parts.push('Overnight low-level winds may bring in more moisture and keep storms going near old outflow boundaries.');
  }

  return `...SYNOPSIS...\n${parts.join(' ')}`;
}

// ── 3. Mesoscale analysis ────────────────────────────────────────────
function buildMesoscale(snap: HourSnapshot): string {
  const ing = snap.ingredients;
  const focus = discussionFocus(snap);
  const parts: string[] = [];

  parts.push(`These values describe the max-risk focus near ${focus}, not the whole surrounding risk area.`);
  parts.push(thermodynamicScenario(snap));

  // Low-level moisture / convergence
  if (ing.sfcDewpointF >= 70 && ing.pwatIn >= 1.6) {
    parts.push(`Very moist air is in place, with surface dewpoints near ${r(ing.sfcDewpointF)}°F and PWAT near ${ing.pwatIn.toFixed(2)}″. This supports heavy rain in stronger storms.`);
  } else if (ing.sfcDewpointF >= 65 && ing.pwatIn >= 1.3) {
    parts.push(`Moisture is adequate near the focus area, with dewpoints near ${r(ing.sfcDewpointF)}°F and PWAT near ${ing.pwatIn.toFixed(2)}″.`);
  } else if (ing.sfcDewpointF >= 55) {
    parts.push(`Moisture is only marginal, with dewpoints near ${r(ing.sfcDewpointF)}°F and PWAT near ${ing.pwatIn.toFixed(2)}″. This should limit storm coverage.`);
  } else {
    parts.push(`Low-level air is dry, with dewpoints near ${r(ing.sfcDewpointF)}°F. Any storms may form above the surface rather than from ground-based air.`);
  }

  // LCL heights — tornado / storm base implications
  if (ing.lclM <= 800) {
    parts.push(`Cloud bases are very low near ${r(ing.lclM)}m AGL, which is more favorable for tornadoes if storms rotate.`);
  } else if (ing.lclM <= 1200) {
    parts.push(`Cloud bases near ${r(ing.lclM)}m AGL are low enough to support some tornado potential.`);
  } else if (ing.lclM <= 1800) {
    parts.push(`Cloud bases near ${r(ing.lclM)}m AGL lower the tornado threat somewhat. Hail and wind are more likely.`);
  } else {
    parts.push(`High cloud bases near ${r(ing.lclM)}m AGL are less favorable for tornadoes and more supportive of strong gusty winds.`);
  }

  // Initiation confidence
  if (ing.initiationConf >= 0.8) {
    parts.push(`Confidence is high that storms will develop ${periodLabel(periodFromISO(snap.validTimeISO))}.`);
  } else if (ing.initiationConf >= 0.5) {
    parts.push('Storm development appears possible, but it still depends on boundaries and heating.');
  } else {
    parts.push('Confidence in storm development is low. The forecast could miss if lift cannot break the cap.');
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
    parts.push(`Extreme instability is present, with MLCAPE near ${r(ing.mlcape)} J/kg. Storms that form could grow very quickly and produce very large hail or strong rotation.`);
  } else if (ing.mlcape >= 3000) {
    parts.push(`Strong instability is present, with MLCAPE near ${r(ing.mlcape)} J/kg and MUCAPE near ${r(ing.mucape)} J/kg. This can support strong updrafts and large hail.`);
  } else if (ing.mlcape >= 2000) {
    parts.push(`Instability is strong enough for organized storms, with MLCAPE near ${r(ing.mlcape)} J/kg.`);
  } else if (ing.mlcape >= 1000) {
    parts.push(`Instability is moderate, with MLCAPE near ${r(ing.mlcape)} J/kg. This can support severe storms, but updraft strength is not extreme.`);
  } else if (ing.mlcape >= 500) {
    parts.push(`Instability is limited, with MLCAPE near ${r(ing.mlcape)} J/kg. Severe potential depends on wind shear making up for weaker storm fuel.`);
  } else {
    parts.push(`Instability is weak, with MLCAPE near ${r(ing.mlcape)} J/kg. Severe weather should be limited unless a local boundary helps storms strengthen.`);
  }

  // ── Kinematic environment ──
  if (ing.shear06Kt >= 50 && ing.srh01 >= 200) {
    parts.push(`Wind support is very strong, with shear near ${r(ing.shear06Kt)} kt and low-level spin near ${r(ing.srh01)} m²/s². Rotating storms would be a concern.`);
  } else if (ing.shear06Kt >= 50) {
    parts.push(`Deep-layer shear is very strong near ${r(ing.shear06Kt)} kt, which favors organized severe storms.`);
  } else if (ing.shear06Kt >= 40 && ing.srh01 >= 150) {
    parts.push(`Shear near ${r(ing.shear06Kt)} kt and low-level spin near ${r(ing.srh01)} m²/s² support rotating storms.`);
  } else if (ing.shear06Kt >= 35) {
    parts.push(`Shear near ${r(ing.shear06Kt)} kt is enough for organized storms, with some chance for rotation.`);
  } else if (ing.shear06Kt >= 25) {
    parts.push(`Shear near ${r(ing.shear06Kt)} kt supports clusters or mixed storm modes. Long-lived supercells are less likely.`);
  } else {
    parts.push(`Shear is weak near ${r(ing.shear06Kt)} kt, so storms should be less organized. Brief strong wind gusts are the main concern.`);
  }

  // ── Capping inversion ──
  if (ing.capStrength === 'strong') {
    parts.push(`A strong cap is present, with CIN near ${r(ing.cin)} J/kg. Storms may be limited, but any storm that breaks through could become strong quickly.`);
  } else if (ing.capStrength === 'moderate') {
    parts.push(`A moderate cap is present, with CIN near ${r(ing.cin)} J/kg. Early storm development may be delayed, but storms could strengthen after the cap weakens.`);
  } else if (ing.capStrength === 'weak') {
    parts.push(`The cap is weak, with CIN near ${r(ing.cin)} J/kg. Storms should be easier to start where moisture is sufficient.`);
  } else {
    parts.push(`There is little cap, with CIN near ${r(ing.cin)} J/kg. Storms may start early and become numerous.`);
  }

  // ── Composite parameters ──
  const composites: string[] = [];
  if (ing.stp >= 3) {
    composites.push(`STP ${ing.stp.toFixed(1)} supports a stronger tornado signal`);
  } else if (ing.stp >= 1) {
    composites.push(`STP ${ing.stp.toFixed(1)} supports some tornado potential`);
  }
  if (ing.scp >= 4) {
    composites.push(`SCP ${ing.scp.toFixed(1)} supports stronger supercells`);
  } else if (ing.scp >= 1) {
    composites.push(`SCP ${ing.scp.toFixed(1)} supports some supercell potential`);
  }
  if (ing.ship >= 1.5) {
    composites.push(`SHIP ${ing.ship.toFixed(1)} supports larger hail`);
  }
  if (ing.ehi >= 2) {
    composites.push(`EHI ${ing.ehi.toFixed(1)} supports stronger tornado potential`);
  } else if (ing.ehi >= 1) {
    composites.push(`EHI ${ing.ehi.toFixed(1)} supports some tornado potential`);
  }
  if (composites.length > 0) {
    parts.push(`Composite signals support the forecast: ${composites.join('; ')}.`);
  }

  parts.push(hazardScenario(snap));

  // ── Storm mode & evolution ──
  parts.push(stormModeDiscussion(ing.stormMode, ing, snap));
  parts.push(evolutionScenario(snap));

  return `...DISCUSSION...\n${parts.join(' ')}`;
}

function stormModeDiscussion(mode: StormMode, ing: Ingredients, snap: HourSnapshot): string {
  const period = periodFromISO(snap.validTimeISO);
  const mainHz = HAZARD_ADJ[snap.outlook.mainHazard];

  if (mode === 'discrete') {
    if (ing.shear06Kt >= 40) {
      return `Storms may stay separate at first, which is the more concerning setup for ${mainHz}. ${period === 'afternoon' ? 'Early storms along the boundary could be intense during peak heating.' : period === 'evening' ? 'Some rotating storms may last into the evening before merging into a larger storm complex.' : 'Separate storms are expected to be the main storm mode during this period.'}`;
    }
    return `Separate storms are possible, supporting the main ${mainHz} threat. Some storms may merge later into clusters.`;
  }
  if (mode === 'linear') {
    return `Storms are expected to form a line, which favors damaging wind gusts. Brief tornadoes are possible along the line where low-level spin is stronger.`;
  }
  if (mode === 'multicell') {
    return `Storm clusters are expected. The main threat is hail from stronger cells, with damaging wind possible as storms merge. Long-lived rotation is less likely.`;
  }
  // mixed
  return `A mixed storm mode is expected. Early separate storms may favor ${mainHz}, while later clusters or a broken line would shift the threat more toward damaging wind.`;
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
        parts.push(`TORNADO: ${pct}% chance within 25 miles${sigNote}. STP ${ing.stp.toFixed(1)}, low-level spin near ${r(ing.srh01)} m²/s², and cloud bases near ${r(ing.lclM)}m support a notable tornado threat.`);
      } else if (pct >= 5) {
        parts.push(`TORNADO: ${pct}% chance within 25 miles${sigNote}. Low-level spin near ${r(ing.srh01)} m²/s² and cloud bases near ${r(ing.lclM)}m support a conditional tornado risk, mainly with separate rotating storms.`);
      } else if (pct >= 2) {
        parts.push(`TORNADO: ${pct}% chance within 25 miles. A brief tornado is possible near boundaries or within a storm line.`);
      }
    } else if (k === 'hail') {
      if (pct >= 30) {
        parts.push(`HAIL: ${pct}% chance within 25 miles${sigNote}. Strong rising air with MUCAPE near ${r(ing.mucape)} J/kg and shear near ${r(ing.shear06Kt)} kt can support large hail.`);
      } else if (pct >= 15) {
        parts.push(`HAIL: ${pct}% chance within 25 miles${sigNote}. Golf-ball-size hail is possible in the strongest storm cores.`);
      } else if (pct >= 5) {
        parts.push(`HAIL: ${pct}% chance within 25 miles. Quarter-size hail is possible with stronger storms.`);
      }
    } else if (k === 'wind') {
      if (pct >= 30) {
        parts.push(`WIND: ${pct}% chance within 25 miles${sigNote}. ${ing.stormMode === 'linear' ? 'A storm line could produce a wider swath of damaging winds.' : 'Strong downdrafts could produce widespread damaging gusts.'}`);
      } else if (pct >= 15) {
        parts.push(`WIND: ${pct}% chance within 25 miles${sigNote}. Damaging gusts of 60-75 mph are possible from stronger storms.`);
      } else if (pct >= 5) {
        parts.push(`WIND: ${pct}% chance within 25 miles. Local damaging gusts near 60 mph are possible with stronger storms.`);
      }
    } else if (k === 'flood') {
      if (pct >= 15) {
        parts.push(`FLOOD: ${pct}% chance within 25 miles. PWAT near ${ing.pwatIn.toFixed(2)}″ supports heavy rain, especially if storms move over the same area.`);
      } else if (pct >= 5) {
        parts.push(`FLOOD: ${pct}% chance within 25 miles. Local heavy rain is possible with PWAT near ${ing.pwatIn.toFixed(2)}″.`);
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
    parts.push(`Overall forecast confidence is ${confPct}%. Severe potential is low, but an isolated stronger storm cannot be ruled out.`);
  } else if (out.confidence >= 0.80) {
    parts.push(`Forecast confidence is high (${confPct}%) because instability, shear, moisture, and lift line up well. The main question is exact coverage and timing.`);
  } else if (out.confidence >= 0.65) {
    parts.push(`Forecast confidence is moderate to high (${confPct}%). The severe threat is supported, but storm mode and exact hazard placement are still uncertain.`);
  } else if (out.confidence >= 0.50) {
    parts.push(`Forecast confidence is moderate (${confPct}%). Storm mode, coverage, and timing remain the main forecast uncertainties.`);
  } else if (out.confidence >= 0.35) {
    parts.push(`Forecast confidence is low to moderate (${confPct}%). Some ingredients disagree, so the forecast depends on how storms actually form.`);
  } else {
    parts.push(`Forecast confidence is low (${confPct}%). The severe threat is very conditional and may not happen if storms fail to form.`);
  }

  // Specific uncertainty drivers
  const drivers: string[] = [];
  if (ing.capStrength === 'strong' && ing.frontSignal !== 'strong') {
    drivers.push('cap may not break');
  }
  if (ing.sfcDewpointF < 55) {
    drivers.push('not enough low-level moisture');
  }
  if (ing.initiationConf < 0.5) {
    drivers.push('storm development is uncertain');
  }
  if (ing.stormMode === 'mixed') {
    drivers.push('storm mode is uncertain');
  }
  if (ing.capStrength === 'none' && ing.mlcape >= 2000) {
    drivers.push('too many early storms could limit intensity');
  }

  if (drivers.length > 0) {
    parts.push(`KEY UNCERTAINTY DRIVERS: ${drivers.join('; ')}.`);
  }

  parts.push(watchScenario(snap));

  // Upside / downside scenarios
  if (out.category !== 'TSTM' && out.confidence < 0.70) {
    const upside = ing.mlcape >= 2000 && ing.shear06Kt >= 35
      ? 'If storms form where instability and shear overlap best, the risk could be higher than shown.'
      : 'A smaller but stronger storm corridor is possible if local boundaries focus development.';
    const downside = ing.capStrength === 'strong'
      ? 'The event could underperform if the cap does not break.'
      : 'The risk could be lower if storms merge into messy rain areas and fail to stay organized.';
    parts.push(`UPSIDE SCENARIO: ${upside}\nDOWNSIDE SCENARIO: ${downside}`);
  }

  return `...UNCERTAINTY...\n${parts.join('\n')}`;
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
