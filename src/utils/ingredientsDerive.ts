// ingredientsDerive: helpers to fill composite indices when a provider
// only supplies the raw fields. Used by openMeteoProvider primarily.

import type { Ingredients, StormMode, SignalStrength } from '../types/forecast';

export function deriveSTP(args: {
  sbcape: number;
  srh01: number;
  shear06Kt: number;
  lclM: number;
  cin: number;
}): number {
  const { sbcape, srh01, shear06Kt, lclM, cin } = args;
  const cape = Math.min(Math.max(0, sbcape) / 1500, 1.5);
  const srh  = Math.min(Math.max(0, srh01) / 150, 1.5);
  const shearMs = shear06Kt / 1.9438445;
  const shr = shearMs < 12.5 ? 0 : Math.min(shearMs / 20, 1.5);
  const lcl  = lclM < 1000 ? 1.0 : lclM < 2000 ? (2000 - lclM) / 1000 : 0;
  const cinT = Math.max(0, Math.min(1, (200 + cin) / 150));
  return Math.max(0, cape * srh * shr * lcl * cinT);
}

export function deriveSCP(args: {
  mucape: number;
  srh03: number;
  shear06Kt: number;
  cinMu?: number;
}): number {
  const { mucape, srh03, shear06Kt, cinMu = 0 } = args;
  const cape = mucape / 1000;
  const srh = Math.max(0, srh03) / 50;
  const shearMs = shear06Kt / 1.9438445;
  const shr = shearMs < 10 ? 0 : Math.min(shearMs / 20, 1);
  const cinTerm = cinMu > -40 ? 1 : Math.max(0, Math.min(1, -40 / Math.min(cinMu, -1e-6)));
  return Math.max(0, Math.min(cape * srh * shr * cinTerm, 24));
}

export function deriveEHI(args: { mlcape: number; srh01: number }): number {
  return (args.mlcape * Math.max(0, args.srh01)) / 160000;
}

export function deriveSHIP(args: {
  mucape: number;
  shear06Kt: number;
  mixingRatioGKg?: number;
  lapseRate700500CPerKm?: number;
  t500C?: number;
  freezingLevelM?: number;
}): number {
  const {
    mucape,
    shear06Kt,
    mixingRatioGKg,
    lapseRate700500CPerKm,
    t500C,
    freezingLevelM,
  } = args;
  if (
    !Number.isFinite(mucape) ||
    !Number.isFinite(shear06Kt) ||
    !Number.isFinite(mixingRatioGKg) ||
    !Number.isFinite(lapseRate700500CPerKm) ||
    !Number.isFinite(t500C) ||
    !Number.isFinite(freezingLevelM) ||
    mucape <= 0
  ) {
    return 0;
  }
  const shearMs = shear06Kt / 1.9438445;
  const mrTerm = Math.max(11, Math.min(13.6, mixingRatioGKg as number));
  const shearTerm = Math.max(7, Math.min(27, shearMs));
  const tempTerm = Math.max(-(t500C as number), 5.5);
  const lapse = lapseRate700500CPerKm as number;
  const freezing = freezingLevelM as number;
  const base = (mucape * mrTerm * lapse * tempTerm * shearTerm) / 42000000;
  const capeModifier = mucape < 1300 ? Math.max(0, Math.min(1, mucape / 1300)) : 1;
  const lapseModifier = lapse < 5.8 ? Math.max(0, Math.min(1, lapse / 5.8)) : 1;
  const freezingModifier = freezing < 2400 ? Math.max(0, Math.min(1, freezing / 2400)) : 1;
  return Math.max(0, Math.min(8, base * capeModifier * lapseModifier * freezingModifier));
}

// ── Storm-mode classification ───────────────────────────────────────
// Operational mode discrimination based on:
//   • Thompson et al. (2003, 2007, 2012): supercell environments require
//     effective bulk shear (~0-6 km) ≥ 25-40 kt and 0-3 km SRH ≥ 150 m²/s².
//   • Smith et al. (2012): companion classification — discrete supercell,
//     QLCS, and disorganized modes are distinguishable by EBS and 0-1 km
//     SRH, with strong frontal forcing biasing toward linear organisation.
//   • Dial, Bunkers, Smith (2010, Wea. Forecasting): operational mode
//     guidance for short-fused watches.
//   • Weisman & Rotunno (1988): squall-line theory — strong synoptic-
//     scale forcing aligned with (or perpendicular to) the shear vector
//     organises convection into a line, irrespective of magnitude, UNTIL
//     deep-layer shear becomes large enough (~50 kt, Bunkers right-mover
//     deviation ~7.5 m/s) that individual cells can break away from the
//     line and become discrete supercells.
// Bug fixed: the previous implementation made the `linear` branch
// unreachable (a strong-front + 30-50 kt shear setup fell through into
// `mixed`), and used an SRH threshold of 200 m²/s² that is climatologically
// "significant tornado", not "supercell". Multicell threshold raised from
// 20 → 25 kt to match the Thompson 2007 organised/pulse boundary.
export function deriveStormMode(args: {
  shear06Kt: number;
  srh03: number;
  frontStrength: SignalStrength;
}): StormMode {
  const { shear06Kt, srh03, frontStrength } = args;
  // Disorganised pulse / weak multicell regime — deep-layer shear too
  // weak to sustain organised mode (Thompson 2007; Smith 2012).
  if (shear06Kt < 25) return 'multicell';
  const supercellSignal = shear06Kt >= 40 && srh03 >= 150;
  // QLCS / linear — strong frontal forcing organises storms into a line
  // (Weisman & Rotunno 1988), but should not veto a strong supercell
  // signal. Reserve linear for strongly forced, lower-helicity cases.
  if (frontStrength === 'strong' && shear06Kt < 45 && srh03 < 200) return 'linear';
  // Discrete supercell — sufficient deep-layer shear AND mesocyclone-
  // scale helicity, without overwhelming linear forcing. SRH threshold
  // 150 m²/s² is the Thompson 2003 operational supercell boundary.
  if (supercellSignal) return 'discrete';
  // Mixed mode — organised but neither purely linear nor purely
  // discrete (transitional supercell-in-line, broken line, or weakly
  // sheared organised multicell).
  return 'mixed';
}

// ── Cap (CIN) strength ──────────────────────────────────────────────
// Operational thresholds follow NWS/SPC-style CIN guidance: very small
// inhibition is breakable early, 50-150 J/kg needs a focused trigger,
// and 150+ J/kg sharply lowers initiation odds unless forcing is strong.
//   ≥ -15  J/kg : effectively no cap
//   -15 → -50   : weak
//   -50 → -150  : moderate
//   < -150      : strong
export function deriveCapStrength(cin: number): SignalStrength {
  const a = Math.abs(cin);
  if (a >= 150) return 'strong';
  if (a >= 50)  return 'moderate';
  if (a >= 15)  return 'weak';
  return 'none';
}

export function fillIngredientComposites(base: Omit<Ingredients,
  'stp' | 'scp' | 'ehi' | 'ship' | 'tornadoComposite'
>): Ingredients {
  const stp = deriveSTP({
    sbcape: base.sbcape,
    srh01: base.srh01,
    shear06Kt: base.shear06Kt,
    lclM: base.lclM,
    cin: base.cinSb ?? base.cin,
  });
  const scp = deriveSCP({
    mucape: base.mucape,
    srh03: base.srh03,
    shear06Kt: base.shear06Kt,
    cinMu: base.cinMu ?? base.cin,
  });
  const ehi = deriveEHI({ mlcape: base.mlcape, srh01: base.srh01 });
  const ship = deriveSHIP({
    mucape: base.mucape,
    shear06Kt: base.shear06Kt,
    mixingRatioGKg: base.mixingRatioGKg,
    lapseRate700500CPerKm: base.lapseRate700500CPerKm,
    t500C: base.t500C,
    freezingLevelM: base.freezingLevelM,
  });
  const shipAvailable = Number.isFinite(base.mixingRatioGKg)
    && Number.isFinite(base.lapseRate700500CPerKm)
    && Number.isFinite(base.t500C)
    && Number.isFinite(base.freezingLevelM);
  const tornadoComposite = (stp * 0.6) + Math.max(0, base.srh01) / 200;
  return { ...base, stp, scp, ehi, ship, shipAvailable, tornadoComposite };
}
