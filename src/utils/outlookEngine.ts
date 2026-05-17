// outlookEngine: SPC-style probability-driven categorical outlook.
// Single source of truth for the *displayed* outlook regardless of provider.
//
// The SPC determines the categorical risk by finding the MAXIMUM implied
// category across all individual hazard probabilities.  Tornado uses
// lower probability thresholds because tornadoes are rarer, while wind and
// hail use higher thresholds. Significant-severe rows are only used when a
// separate significant-severe probability exists.
// The categorical graphic is therefore a *derived product* — not a
// separate score.  AutoOutlook follows the same philosophy: compute
// per-hazard probabilities first (hazardEngine), then let the highest
// implied category set the categorical outlook.

import type {
  HazardAssessment,
  HazardKey,
  Ingredients,
  Outlook,
  RiskCategory,
} from '../types/forecast';

// Numeric ordinal -> category (matches RISK_META.ord).
const ORD_TO_CAT: RiskCategory[] = ['TSTM', 'MRGL', 'SLGT', 'ENH', 'MOD', 'HIGH'];
const CATEGORICAL_HAZARDS: HazardKey[] = ['tornado', 'hail', 'wind'];

function catOrd(cat: RiskCategory): number {
  return ORD_TO_CAT.indexOf(cat);
}

// ── SPC-style category from hazard probabilities ─────────────────────
// The categorical risk equals the MAXIMUM implied category across all
// individual hazard assessments.  This mirrors the SPC Day-1 approach
// where, e.g., a 10% tornado probability alone is enough for ENH even
// if wind/hail are only 5%.
export function categoryFromHazards(
  hazards: Record<HazardKey, HazardAssessment>,
): { category: RiskCategory; drivingHazard: HazardKey } {
  let best: RiskCategory = 'TSTM';
  let driver: HazardKey = 'wind';
  CATEGORICAL_HAZARDS.forEach((k) => {
    const h = hazards[k];
    if (catOrd(h.level) > catOrd(best) ||
        (catOrd(h.level) === catOrd(best) && h.probability > hazards[driver].probability)) {
      best = h.level;
      driver = k;
    }
  });
  return { category: best, drivingHazard: driver };
}

// ── Legacy helper — kept for callers that only have Ingredients ──────
// Backward-compatible numeric score → category.  Used only when hazard
// assessments aren't available (e.g. polygon builder).
export function categoryFromScore(score: number): RiskCategory {
  if (score >= 10.6) return 'HIGH';
  if (score >= 9.1) return 'MOD';
  if (score >= 7.2) return 'ENH';
  if (score >= 4.4) return 'SLGT';
  if (score >= 2.4) return 'MRGL';
  return 'TSTM';
}

export function outlookScore(ing: Ingredients): number {
  const capeTerm  = Math.min(ing.mlcape / 1000, 4);
  const shearTerm = Math.min(ing.shear06Kt / 20, 2.5);
  const srhTerm   = Math.min(Math.max(ing.srh01, 0) / 150, 1.5);
  const stpTerm   = Math.min(Math.max(ing.stp, 0) / 2, 2);
  const scpTerm   = Math.min(Math.max(ing.scp, 0) / 4, 1.5);
  const moistTerm = Math.min(Math.max(ing.sfcDewpointF - 55, 0) / 12, 1);
  const capPenalty =
    ing.capStrength === 'strong'   ? 2.5 :
    ing.capStrength === 'moderate' ? 0.9 :
    ing.capStrength === 'weak'     ? 0.2 : 0;
  const forcingBoost =
    ing.frontSignal === 'strong'   ? 0.6 :
    ing.frontSignal === 'moderate' ? 0.3 :
    ing.frontSignal === 'weak'     ? 0.1 : 0;
  const initBoost = ing.initiationConf * 0.5;
  const raw =
    0.9 * capeTerm + 1.0 * shearTerm + 0.8 * srhTerm +
    0.9 * stpTerm + 0.7 * scpTerm + 0.5 * moistTerm +
    forcingBoost + initBoost - capPenalty;
  return Math.max(0, Math.min(11, raw));
}

// ── Confidence ───────────────────────────────────────────────────────
// Confidence reflects how aligned the ingredients are.  When signals
// conflict (e.g. strong cap with weak forcing), confidence drops — the
// SPC often flags such setups as "conditional" or "bust-risk" scenarios.
export function confidenceFromIngredients(
  ing: Ingredients,
  hazards?: Record<HazardKey, HazardAssessment>,
): number {
  const capeC  = Math.min(ing.mlcape / 2500, 1);
  const shearC = Math.min(ing.shear06Kt / 40, 1);
  const force  =
    ing.frontSignal === 'strong'   ? 0.9 :
    ing.frontSignal === 'moderate' ? 0.7 :
    ing.frontSignal === 'weak'     ? 0.5 : 0.3;
  const init   = ing.initiationConf;
  // Strong cap with little forcing tanks confidence (bust risk).
  const capPenalty = ing.capStrength === 'strong' && ing.frontSignal !== 'strong' ? 0.22 : 0;
  // Inter-hazard agreement: if multiple hazards suggest the same category,
  // the forecast is more confident.
  let agreementBoost = 0;
  if (hazards) {
    const cats = CATEGORICAL_HAZARDS.map((k) => catOrd(hazards[k].level));
    const above = cats.filter((c) => c >= 2).length; // SLGT+
    agreementBoost = above >= 3 ? 0.08 : above >= 2 ? 0.04 : 0;
  }
  return Math.max(0.15, Math.min(0.95,
    0.24 * capeC + 0.24 * shearC + 0.24 * force + 0.20 * init + agreementBoost - capPenalty,
  ));
}

// ── Headline generation ──────────────────────────────────────────────
const HAZARD_NOUN: Record<HazardKey, string> = {
  tornado: 'tornadoes',
  hail:    'large hail',
  wind:    'damaging straight-line wind',
  flood:   'localized flash flooding',
};

const HAZARD_SECONDARY: Record<HazardKey, string[]> = {
  tornado: ['large hail', 'damaging wind'],
  hail:    ['damaging wind', 'isolated tornadoes'],
  wind:    ['large hail', 'isolated tornadoes'],
  flood:   ['gusty wind', 'small hail'],
};

const CATEGORY_PHRASE: Record<RiskCategory, string> = {
  TSTM: 'general thunderstorms expected',
  MRGL: 'marginal severe risk',
  SLGT: 'slight severe risk',
  ENH:  'enhanced severe risk',
  MOD:  'moderate severe risk',
  HIGH: 'high severe risk',
};

function headlineFor(cat: RiskCategory, hz: HazardKey, sigSevere: boolean): string {
  const phrase = CATEGORY_PHRASE[cat];
  if (cat === 'TSTM') return 'General thunderstorms possible — minimal severe threat.';
  const main = HAZARD_NOUN[hz];
  const second = HAZARD_SECONDARY[hz][0];
  const wording =
    cat === 'MRGL' ? 'isolated storms could produce' :
    cat === 'SLGT' ? 'scattered storms may produce' :
    'organized storms may produce';
  const sigTag = sigSevere && catOrd(cat) >= 3 ? ' A few significant reports are possible where storms remain organized.' : '';
  return `${capitalize(phrase)} — ${wording} ${main}, with ${second} as a secondary concern.${sigTag}`;
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ── Primary entry point ──────────────────────────────────────────────
// SPC-style: categorical risk is derived from the hazard probabilities,
// not from a separate numeric score.
export function buildOutlook(
  ing: Ingredients,
  hazards?: Record<HazardKey, HazardAssessment>,
): Outlook {
  // If hazard assessments are provided, use the SPC probability-driven
  // approach.  Otherwise fall back to the legacy score path so existing
  // call sites that haven't been updated yet still work.
  let category: RiskCategory;
  let mainHazard: HazardKey;
  let significantSevere: boolean;

  if (hazards) {
    const result = categoryFromHazards(hazards);
    category = result.category;
    mainHazard = result.drivingHazard;
    significantSevere = CATEGORICAL_HAZARDS.some(
      (k) => hazards[k].significantSevere,
    );
  } else {
    // Legacy path (no hazard data available)
    category = categoryFromScore(outlookScore(ing));
    mainHazard = pickMainHazardLegacy(ing);
    significantSevere = false;
  }

  const confidence = confidenceFromIngredients(ing, hazards);
  return {
    category,
    mainHazard,
    confidence,
    significantSevere,
    headline: headlineFor(category, mainHazard, significantSevere),
  };
}

// Legacy ingredient-only main-hazard picker.
function pickMainHazardLegacy(ing: Ingredients): HazardKey {
  const tor  = Math.max(ing.stp, 0) * 1.2 + Math.max(ing.srh01, 0) / 150;
  const hail = Math.max(ing.ship, 0) * 1.0 + Math.min(ing.mucape / 2500, 1.5);
  const wind = Math.min(ing.shear06Kt / 30, 1.5) +
               Math.min(ing.mlcape / 2500, 1.0) +
               (ing.stormMode === 'linear' || ing.stormMode === 'mixed' ? 0.6 : 0);
  const flood = Math.min(ing.pwatIn / 1.5, 1.5) +
                Math.min(ing.moistureDepthM / 3000, 1.0) +
                (ing.stormMode === 'linear' ? 0.3 : 0) +
                (ing.shear06Kt < 25 ? 0.3 : 0);
  const entries: [HazardKey, number][] = [
    ['tornado', tor], ['hail', hail], ['wind', wind], ['flood', flood],
  ];
  entries.sort((a, b) => b[1] - a[1]);
  return entries[0][0];
}

// Helper exported for tests/visuals
export function categoryRamp(cat: RiskCategory): RiskCategory[] {
  // Returns categories from this one stepping down to TSTM.
  const ord = ORD_TO_CAT.indexOf(cat);
  return ORD_TO_CAT.slice(0, ord + 1);
}
