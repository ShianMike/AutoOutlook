// ingredientsDerive: helpers to fill composite indices when a provider
// only supplies the raw fields. Used by openMeteoProvider primarily.

import type { Ingredients, StormMode, SignalStrength } from '../types/forecast';

export function deriveSTP(args: {
  mlcape: number;
  srh01: number;
  shear06Kt: number;
  lclM: number;
  cin: number;
}): number {
  const { mlcape, srh01, shear06Kt, lclM, cin } = args;
  const cape = Math.min(mlcape / 1500, 1.5);
  const srh  = Math.max(0, srh01) / 150;
  const shr  = Math.max(0, Math.min((shear06Kt - 12.5) / 12.5, 1.5));
  const lcl  = lclM < 1000 ? 1.0 : lclM < 2000 ? (2000 - lclM) / 1000 : 0;
  const cinT = cin > -50 ? 1 : cin > -150 ? 1 - (Math.abs(cin) - 50) / 100 : 0;
  return Math.max(0, cape * srh * shr * lcl * cinT);
}

export function deriveSCP(args: {
  mucape: number;
  srh03: number;
  shear06Kt: number;
}): number {
  const { mucape, srh03, shear06Kt } = args;
  const cape = mucape / 1000;
  const srh  = Math.max(0, srh03) / 50;
  const shr  = Math.max(0, Math.min(shear06Kt / 20, 1.5));
  return Math.max(0, cape * srh * shr);
}

export function deriveEHI(args: { mlcape: number; srh01: number }): number {
  return (args.mlcape * Math.max(0, args.srh01)) / 160000;
}

export function deriveSHIP(args: {
  mucape: number;
  shear06Kt: number;
  // approximate inputs, real SHIP uses lapse rate and freezing level
}): number {
  const cape = Math.min(args.mucape / 2500, 2);
  const shr  = Math.min(args.shear06Kt / 30, 2);
  return Math.max(0, cape * shr * 0.6);
}

export function deriveStormMode(args: {
  shear06Kt: number;
  srh03: number;
  frontStrength: SignalStrength;
}): StormMode {
  const { shear06Kt, srh03, frontStrength } = args;
  if (shear06Kt >= 40 && srh03 >= 200) return 'discrete';
  if (shear06Kt >= 30 && (frontStrength === 'strong' || frontStrength === 'moderate')) return 'mixed';
  if (shear06Kt >= 25 && frontStrength === 'strong') return 'linear';
  if (shear06Kt < 20) return 'multicell';
  return 'mixed';
}

export function deriveCapStrength(cin: number): SignalStrength {
  const a = Math.abs(cin);
  if (a >= 200) return 'strong';
  if (a >= 100) return 'moderate';
  if (a >= 25)  return 'weak';
  return 'none';
}

export function fillIngredientComposites(base: Omit<Ingredients,
  'stp' | 'scp' | 'ehi' | 'ship' | 'tornadoComposite'
>): Ingredients {
  const stp = deriveSTP({
    mlcape: base.mlcape,
    srh01: base.srh01,
    shear06Kt: base.shear06Kt,
    lclM: base.lclM,
    cin: base.cin,
  });
  const scp = deriveSCP({ mucape: base.mucape, srh03: base.srh03, shear06Kt: base.shear06Kt });
  const ehi = deriveEHI({ mlcape: base.mlcape, srh01: base.srh01 });
  const ship = deriveSHIP({ mucape: base.mucape, shear06Kt: base.shear06Kt });
  const tornadoComposite = (stp * 0.6) + Math.max(0, base.srh01) / 200;
  return { ...base, stp, scp, ehi, ship, tornadoComposite };
}
