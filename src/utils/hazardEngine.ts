// hazardEngine: per-hazard probability/confidence/explanation.
// Pure function over Ingredients, used by every provider browser-side.
//
// SPC-calibrated: the categorical risk for each hazard is derived using
// the official Day-1 probability-to-category conversion tables so that
// per-hazard levels are physically consistent with the categorical
// outlook that outlookEngine produces.

import type {
  HazardAssessment,
  HazardKey,
  Ingredients,
  RiskCategory,
} from '../types/forecast';

// ── SPC Day-1 Probability → Category conversion tables ──────────────
// Tornado uses lower thresholds because tornadoes are rarer; even a 2%
// probability within 25 mi is well above climatology.
// These are the non-significant-severe rows from the SPC Day 1/2
// probability-to-category table. AutoOutlook does not forecast a separate
// significant-severe probability grid here, so MDT/HIGH are not inferred
// from significant-severe rows.
const TOR_THRESHOLDS: [number, RiskCategory][] = [
  [0.45, 'HIGH'],
  [0.30, 'MOD'],
  [0.10, 'ENH'],
  [0.05, 'SLGT'],
  [0.02, 'MRGL'],
];

// Wind and hail share the same non-significant-severe category ladder.
const WIND_HAIL_THRESHOLDS: [number, RiskCategory][] = [
  [0.60, 'MOD'],
  [0.30, 'ENH'],
  [0.15, 'SLGT'],
  [0.05, 'MRGL'],
];

// Flood is not an SPC categorical hazard but we map it analogously to
// wind/hail so AutoOutlook can show a consistent flood outlook.
const FLOOD_THRESHOLDS: [number, RiskCategory][] = [
  [0.45, 'HIGH'],
  [0.30, 'MOD'],
  [0.15, 'SLGT'],
  [0.05, 'MRGL'],
];

const HAZARD_THRESHOLD_TABLE: Record<HazardKey, [number, RiskCategory][]> = {
  tornado: TOR_THRESHOLDS,
  hail:    WIND_HAIL_THRESHOLDS,
  wind:    WIND_HAIL_THRESHOLDS,
  flood:   FLOOD_THRESHOLDS,
};

export function lvlFromProb(hazard: HazardKey, p: number): RiskCategory {
  for (const [thr, cat] of HAZARD_THRESHOLD_TABLE[hazard]) {
    if (p >= thr) return cat;
  }
  return 'TSTM';
}

// ── SPC Significant Severe thresholds ───────────────────────────────
// "Significant" = EF2+ tornado, 74 mph+ wind, or 2"+ hail.
// SPC hatches when >=10% prob of significant severe within 25 mi.
const SIG_SEVERE_PROB_THRESHOLD = 0.10;

function clamp01(x: number): number {
  return Math.max(0, Math.min(1, x));
}

// ── Initiation modifier (cap × forcing × initiation) ────────────────
// Models the SPC concept that favorable ingredients don't matter if
// storms never initiate.  A strong cap with weak forcing keeps the lid
// on; strong forcing can overcome even a nasty cap.
function initiationModifier(ing: Ingredients, floor = 0.35): number {
  const capFactor =
    ing.capStrength === 'strong' ? 0.22 :
    ing.capStrength === 'moderate' ? 0.46 :
    ing.capStrength === 'weak' ? 0.74 : 1.0;
  const forcingRelief =
    ing.frontSignal === 'strong' ? 0.20 :
    ing.frontSignal === 'moderate' ? 0.08 :
    ing.frontSignal === 'weak' ? 0.02 : 0;
  const effectiveCap = Math.min(1.0, capFactor + forcingRelief);
  const initFactor = floor + (1 - floor) * clamp01(ing.initiationConf);
  return effectiveCap * initFactor;
}

interface RawHazard {
  probability: number;
  sigSevereProb: number;      // probability of *significant* severe
  supporting: string[];
  explanation: string;
  baseConf: number;
}

// ── Tornado evaluation ──────────────────────────────────────────────
// Key SPC ingredients: STP, 0-1 km SRH, low LCL, surface-based CAPE,
// storm mode (discrete supercells strongly preferred).
function tornadoEval(ing: Ingredients): RawHazard {
  // SPC primary composite: STP (Significant Tornado Parameter).
  // STP > 1 historically correlates with significant tornado environments;
  // STP > 4 with violent tornadoes.  We use a two-segment curve so the
  // marginal-to-significant transition around STP=1 is steep.
  const stpNorm = Math.max(0, ing.stp);
  const stpTerm = stpNorm <= 1
    ? stpNorm * 0.04                               // marginal below 1
    : 0.04 + Math.min((stpNorm - 1) / 6, 1) * 0.12; // ramps through ENH/MOD/HIGH
  // 0-1 km SRH: mesocyclone potential.  150+ supercell-favorable, 300+ significant.
  const srh01Term = Math.min(Math.max(0, ing.srh01) / 350, 1) * 0.08;
  // LCL height — continuous function matching the STP formula's LCL term.
  // Low LCL (<1000 m) stretches near-ground vortex tubes; >2000 m kills tornado risk.
  const lclTerm = ing.lclM < 750 ? 0.05
    : ing.lclM < 1200 ? 0.02 + 0.03 * (1200 - ing.lclM) / 450
    : ing.lclM < 1800 ? 0.02 * (1800 - ing.lclM) / 600 : 0;
  const capeTerm = Math.min(ing.mlcape / 3000, 1) * 0.04;
  const ehiTerm  = Math.min(Math.max(0, ing.ehi) / 2.5, 1) * 0.03;
  // Discrete supercells are overwhelmingly the tornado producer.
  // Linear storms (QLCS) can produce weaker tornadoes at lower rates.
  const modeFactor =
    ing.stormMode === 'discrete' ? 0.70 :
    ing.stormMode === 'mixed' ? 0.24 :
    ing.stormMode === 'linear' ? 0.09 : 0.04;
  const frontalModeCap =
    ing.stormMode === 'linear' ? 0.019 :
    ing.stormMode === 'mixed' && ing.frontSignal === 'strong' ? 0.039 :
    ing.stormMode === 'mixed' ? 0.049 :
    ing.frontSignal === 'strong' ? 0.075 :
    1.0;
  const environmentGate =
    (ing.stp >= 1.0 ? 1 : 0.48) *
    (ing.lclM <= 1250 ? 1 : 0.55) *
    (ing.srh01 >= 150 ? 1 : 0.60);
  const raw = (stpTerm + srh01Term + lclTerm + capeTerm + ehiTerm) * initiationModifier(ing, 0.15) * modeFactor * environmentGate;
  const probability = clamp01(Math.min(raw, frontalModeCap));

  // Significant tornado (EF2+): requires STP >= 1.5, strong SRH, low LCL.
  const sigBase = Math.min(Math.max(0, ing.stp - 1.5) / 5, 1) * 0.16 +
                  (ing.srh01 >= 200 ? 0.05 : 0) +
                  (ing.lclM < 1000 ? 0.04 : 0) +
                  (ing.ehi >= 2 ? 0.03 : 0);
  const sigSevereProb = clamp01(sigBase * modeFactor * initiationModifier(ing, 0.35));

  const supporting: string[] = [];
  if (ing.stp >= 1)     supporting.push(`STP ${ing.stp.toFixed(1)}`);
  if (ing.srh01 >= 100) supporting.push(`0\u20131 km SRH ${Math.round(ing.srh01)} m\xB2/s\xB2`);
  if (ing.lclM < 1200)  supporting.push(`Low LCL (${Math.round(ing.lclM)} m)`);
  if (ing.ehi >= 1)     supporting.push(`EHI ${ing.ehi.toFixed(1)}`);
  if (ing.tornadoComposite >= 1) supporting.push(`Tor composite ${ing.tornadoComposite.toFixed(1)}`);
  if (supporting.length === 0)  supporting.push('Marginal low-level rotation potential');

  const explanation =
    probability >= 0.15
      ? 'Large STP and robust low-level shear within a strongly unstable, low-LCL environment support discrete supercells capable of producing significant tornadoes, including potential for long-track events.'
      : probability >= 0.10
      ? 'Favorable thermodynamic and kinematic overlap for significant supercell tornadoes; strong STP values and deep mesocyclone potential warrant enhanced risk.'
      : probability >= 0.05
      ? 'Sufficient mesocyclone-scale rotation for organized tornadoes; a few strong (EF2+) events possible with the strongest cells.'
      : probability >= 0.02
      ? 'Isolated tornado potential given marginal low-level shear and limited but non-zero STP values.'
      : 'Tornado threat appears negligible; weak low-level shear or unfavorable thermodynamics limit potential.';

  const baseConf = clamp01(0.30 + 0.30 * Math.min(ing.srh01 / 250, 1) + 0.25 * Math.min(ing.stp / 3, 1) + 0.10 * (ing.lclM < 1000 ? 1 : 0));
  return { probability, sigSevereProb, supporting, explanation, baseConf };
}

// ── Hail evaluation ─────────────────────────────────────────────────
// Key SPC ingredients: MUCAPE (updraft strength), SHIP, deep-layer
// shear, and real 700-500 mb lapse rates when pressure-level fields exist.
function hailEval(ing: Ingredients): RawHazard {
  const shipAvailable = ing.shipAvailable ?? ing.ship > 0;
  const lapseRate = typeof ing.lapseRate700500CPerKm === 'number' && Number.isFinite(ing.lapseRate700500CPerKm)
    ? ing.lapseRate700500CPerKm
    : undefined;
  const lapseAvailable = lapseRate !== undefined;
  // MUCAPE drives updraft strength — the engine that lofts hailstones
  // through the hail-growth zone above the freezing level.
  const muCapeTerm = Math.min(ing.mucape / 4000, 1) * 0.12;
  // SHIP (Significant Hail Parameter): two-segment curve like tornado STP.
  // SHIP > 1 historically correlates with significant (≥2") hail;
  // SHIP > 2 with very large stones.
  const shipNorm = Math.max(0, ing.ship);
  const shipTerm = shipNorm <= 1
    ? shipNorm * 0.045
    : 0.045 + Math.min((shipNorm - 1) / 2.5, 1) * 0.10;
  const shearTerm  = Math.min(ing.shear06Kt / 55, 1) * 0.08;
  const lapseTerm = lapseAvailable
    ? Math.min(Math.max(0, lapseRate - 6.0) / 2.0, 1) * 0.03
    : 0;
  // Storm-relative wind supports hail residence time in the updraft
  const srWindTerm = Math.min(ing.stormRelWindKt / 50, 1) * 0.025;
  // Very moist BL → lower freezing level → less hail growth potential
  const moistDrag  = ing.sfcDewpointF > 72 ? 0.02 : 0;
  const modeBoost  = ing.stormMode === 'discrete' ? 0.025 : ing.stormMode === 'mixed' ? 0.01 : 0;
  const hailEnvironmentGate =
    (ing.mucape >= 1250 ? 1 : 0.62) *
    (ing.shear06Kt >= 35 ? 1 : 0.70);
  const raw = (muCapeTerm + shipTerm + shearTerm + lapseTerm + srWindTerm + modeBoost - moistDrag) *
    initiationModifier(ing, 0.26) *
    hailEnvironmentGate;
  const probability = clamp01(raw);

  // Significant hail (≥2"): SHIP ≥ 1.5, strong shear, MUCAPE ≥ 2500,
  // and ideally discrete mode for the longest updraft residence times.
  const sigBase = Math.min(Math.max(0, ing.ship - 1.2) / 2.8, 1) * 0.10 +
                  (ing.mucape >= 3000 ? 0.04 : 0) +
                  (ing.shear06Kt >= 45 ? 0.025 : 0) +
                  (ing.stormMode === 'discrete' ? 0.02 : 0);
  const sigSevereProb = clamp01(sigBase * initiationModifier(ing, 0.38));

  const supporting: string[] = [];
  if (ing.mucape >= 2000) supporting.push(`MUCAPE ${Math.round(ing.mucape)} J/kg`);
  if (shipAvailable && ing.ship >= 1) supporting.push(`SHIP ${ing.ship.toFixed(1)}`);
  if (!shipAvailable) supporting.push('SHIP unavailable: missing pressure-level hail fields');
  if (lapseAvailable && lapseRate >= 6.5) supporting.push(`700-500 mb lapse ${lapseRate.toFixed(1)} C/km`);
  if (ing.shear06Kt >= 35) supporting.push(`Sfc–500 shear ${Math.round(ing.shear06Kt)} kt`);
  if (ing.scp >= 2)       supporting.push(`SCP ${ing.scp.toFixed(1)}`);
  if (ing.stormRelWindKt >= 30) supporting.push(`SR-wind proxy ${Math.round(ing.stormRelWindKt)} kt`);
  if (supporting.length === 0) supporting.push('Limited deep-layer instability');

  const explanation =
    probability >= 0.30
      ? 'Steep mid-level lapse rates and very strong updrafts in an intensely sheared environment favor very large hail (2"+) with long-track supercells; isolated giant hail not ruled out.'
      : probability >= 0.15
      ? 'Strong instability and deep-layer shear support large hail (1–2") with discrete storms; isolated very large stones possible with the most robust updrafts.'
      : probability >= 0.05
      ? 'Sufficient instability and shear support some risk of severe hail (≥1") with the strongest cells; coverage limited by storm mode and capping.'
      : 'Hail threat is low; updraft strength or shear is insufficient for severe stones.';

  const baseConf = clamp01(0.35 + 0.30 * Math.min(ing.mucape / 3000, 1) + 0.20 * Math.min(ing.ship / 2, 1) + 0.10 * Math.min(ing.shear06Kt / 50, 1));
  return { probability, sigSevereProb, supporting, explanation, baseConf };
}

// ── Wind evaluation ─────────────────────────────────────────────────
// Key SPC ingredients: low-to-midlevel shear proxy, CAPE (downdraft potential),
// storm mode (linear = higher wind coverage), storm-relative wind.
function windEval(ing: Ingredients): RawHazard {
  const moistQuality =
    ing.sfcDewpointF >= 65 ? 1.00 :
    ing.sfcDewpointF >= 60 ? 0.84 :
    ing.sfcDewpointF >= 56 ? 0.64 : 0.46;
  const organizedWindQuality =
    ing.shear06Kt >= 45 ? 1.00 :
    ing.shear06Kt >= 40 ? 0.78 :
    ing.shear06Kt >= 35 ? 0.64 : 0.50;
  const capeTerm  = Math.min(ing.mlcape / 3000, 1) * 0.10;
  const shearTerm = Math.min(ing.shear06Kt / 55, 1) * 0.11;
  const srWindTerm = Math.min(ing.stormRelWindKt / 50, 1) * 0.035;
  // Dry intrusion above BL can load downdrafts with evaporative cooling
  const dryTerm   = ing.cin > -50 && ing.mlcape >= 1000 ? 0.015 : 0;
  // Storm mode is a first-order control on wind coverage
  const modeBoost =
    ing.stormMode === 'linear' ? 0.045 :
    ing.stormMode === 'mixed'  ? 0.03 :
    ing.stormMode === 'multicell' ? 0.015 : 0;
  const raw = (capeTerm + shearTerm + srWindTerm + dryTerm + modeBoost) *
    initiationModifier(ing, 0.24) *
    moistQuality *
    organizedWindQuality;
  const probability = clamp01(raw);

  // Significant wind (>=74 mph): require a clearer linear/MCS signal, or an
  // exceptionally strong mixed-mode environment. Avoid hatching ordinary
  // SLGT wind setups from shear+CAPE alone.
  const sigSetup =
    (ing.stormMode === 'linear' && ing.shear06Kt >= 55 && ing.mlcape >= 1500) ||
    (ing.stormMode === 'mixed' && ing.shear06Kt >= 70 && ing.mlcape >= 3500);
  const sigBase = sigSetup
    ? (ing.stormMode === 'linear' ? 0.045 : 0.020) +
      Math.min(ing.shear06Kt / 65, 1) * 0.040 +
      Math.min(ing.mlcape / 3500, 1) * 0.030
    : 0.0;
  const sigSevereProb = clamp01(sigBase * initiationModifier(ing, 0.38));

  const supporting: string[] = [];
  if (ing.shear06Kt >= 35) supporting.push(`Strong Sfc–500 shear ${Math.round(ing.shear06Kt)} kt`);
  if (ing.mlcape >= 1500) supporting.push(`MLCAPE ${Math.round(ing.mlcape)} J/kg`);
  if (ing.stormMode === 'linear' || ing.stormMode === 'mixed') supporting.push(`Storm mode: ${ing.stormMode}`);
  if (ing.stormRelWindKt >= 30) supporting.push(`SR-wind proxy ${Math.round(ing.stormRelWindKt)} kt`);
  if (supporting.length === 0) supporting.push('Limited wind-producing potential');

  const explanation =
    probability >= 0.30
      ? 'A well-organized squall line or QLCS in a strongly sheared, high-CAPE environment favors widespread damaging gusts, some reaching hurricane force.'
      : probability >= 0.15
      ? 'Organized convection in a moderately to strongly sheared environment supports numerous damaging wind reports.'
      : probability >= 0.05
      ? 'Multicell to mixed-mode convection may produce isolated damaging wind reports, mainly from downbursts.'
      : 'Wind threat appears limited given current shear and storm-mode signals.';

  const baseConf = clamp01(0.35 + 0.40 * Math.min(ing.shear06Kt / 50, 1) + 0.15 * (ing.stormMode === 'linear' ? 1 : 0.5));
  return { probability, sigSevereProb, supporting, explanation, baseConf };
}

// ── Flood evaluation ────────────────────────────────────────────────
// Key ingredients based on hydrometeorological flash flood forecasting principles:
// 1. Moisture & Precipitation Efficiency (warm cloud depth and high precipitable water)
// 2. Convective Instability (high CAPE supporting extreme rainfall rates)
// 3. Duration & Echo Training (slow steering flow / low shear and training storm modes)
// 4. Initiation & Uplift Forcing
function floodEval(ing: Ingredients): RawHazard {
  // 1. Moisture & Precipitation Efficiency
  const pwatFactor = clamp01((ing.pwatIn - 0.75) / (2.2 - 0.75));
  const lclFactor = clamp01((2200 - ing.lclM) / (2200 - 600));
  const tdFactor = clamp01((ing.sfcDewpointF - 52) / (72 - 52));
  const moistureScore = 0.5 * pwatFactor + 0.3 * tdFactor + 0.2 * lclFactor;

  // 2. Convective Instability (potential rainfall intensity)
  const capeScore = clamp01(ing.mucape / 3000);

  // 3. Storm Duration & Echo Training Potential
  // Slow steering flow (low shear) increases point rainfall duration.
  const motionScore =
    ing.shear06Kt < 15 ? 1.00 :
    ing.shear06Kt < 25 ? 0.88 :
    ing.shear06Kt < 40 ? 0.65 :
    ing.shear06Kt < 55 ? 0.45 : 0.25;
  
  // Linear squall lines (training echo systems) or Multicell clusters are highly prone to training/back-building.
  const modeScore =
    ing.stormMode === 'linear' ? 1.00 :
    ing.stormMode === 'multicell' ? 0.85 :
    ing.stormMode === 'mixed' ? 0.60 : 0.35;

  const durationScore = 0.5 * motionScore + 0.5 * modeScore;

  // 4. Dynamic Forcing and Initiation modifier
  const initFactor = initiationModifier(ing, 0.40);

  // Physically grounded Flash Flood Potential (FFP) raw index
  const raw = (0.45 * moistureScore + 0.20 * capeScore + 0.35 * durationScore) * initFactor;

  // Dynamic cap limits flooding without supporting echo-training or extreme moisture
  const trainSignal = ing.shear06Kt < 30 || ing.stormMode === 'linear' || ing.stormMode === 'multicell';
  const broadFloodCap = trainSignal || ing.pwatIn >= 2.10 ? 0.48 : 0.15;
  const probability = clamp01(Math.min(raw, broadFloodCap));

  // Flood does not map to significant severe SPC wind/hail categories
  const sigSevereProb = 0;

  const supporting: string[] = [];
  if (ing.pwatIn >= 1.35)         supporting.push(`PWAT ${ing.pwatIn.toFixed(2)} in`);
  if (ing.sfcDewpointF >= 62)     supporting.push(`Sfc Dewpoint ${Math.round(ing.sfcDewpointF)}°F`);
  if (ing.lclM < 1200)            supporting.push(`Low LCL (${Math.round(ing.lclM)} m)`);
  if (ing.mucape >= 1200)         supporting.push(`MUCAPE ${Math.round(ing.mucape)} J/kg`);
  if (ing.shear06Kt < 25)         supporting.push('Slow storm motion');
  if (supporting.length === 0)    supporting.push('Limited heavy-rain signals');

  const explanation =
    probability >= 0.30
      ? 'Extremely high precipitable water, steep warm-cloud depth, and slow-moving, training convection structures support a high flash flooding and excessive rainfall threat.'
      : probability >= 0.15
      ? 'Favorable thermodynamic instability, high precipitable water, and potential for repeating/training convective tracks present a notable flash flood risk.'
      : probability >= 0.05
      ? 'Localized heavy rainfall is possible with robust convective cells, but limited storm duration or lower ambient moisture bounds the flash flood potential.'
      : 'Flash flood threat is low; weak instability, dry tropospheric layers, or fast steering flow bounds precipitation rates and training potential.';

  const baseConf = clamp01(0.35 + 0.35 * Math.min(ing.pwatIn / 2.0, 1) + 0.15 * (ing.shear06Kt < 25 ? 1 : 0.4));
  return { probability, sigSevereProb, supporting, explanation, baseConf };
}

const EVALS: Record<HazardKey, (i: Ingredients) => RawHazard> = {
  tornado: tornadoEval,
  hail:    hailEval,
  wind:    windEval,
  flood:   floodEval,
};

export function buildHazards(ing: Ingredients): Record<HazardKey, HazardAssessment> {
  const out: Partial<Record<HazardKey, HazardAssessment>> = {};
  (Object.keys(EVALS) as HazardKey[]).forEach((k) => {
    const r = EVALS[k](ing);
    out[k] = {
      level: lvlFromProb(k, r.probability),
      probability: r.probability,
      confidence: r.baseConf,
      significantSevere: k !== 'flood' && r.sigSevereProb >= SIG_SEVERE_PROB_THRESHOLD,
      supporting: r.supporting,
      explanation: r.explanation,
    };
  });
  return out as Record<HazardKey, HazardAssessment>;
}
