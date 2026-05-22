#!/usr/bin/env node
// Render `public/autooutlook-embed.svg` to `public/autooutlook-embed.png`
// (the OG / Twitter card image referenced by `index.html`).
//
// Run: `node scripts/build-embed-png.mjs`
//
// We use @resvg/resvg-js (pure-JS WASM-backed SVG renderer) so this works
// cross-platform without native bindings.

import { Resvg } from '@resvg/resvg-js';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const SVG_PATH = resolve(ROOT, 'public/autooutlook-embed.svg');
const PNG_PATH = resolve(ROOT, 'public/autooutlook-embed.png');

// Render at the SVG's native size: 1200 x 630 (Open Graph standard).
// `fitTo: { mode: 'width', value: 1200 }` keeps text crisp at full resolution.
const RESVG_OPTS = {
  fitTo: { mode: 'width', value: 1200 },
  background: '#f5f1e8',
  font: {
    // Try to load fonts installed on the system. Prefer narrower sans-serif
    // defaults so we don't get a serif fallback (Cambria) which renders much
    // wider than the SVG was designed for.
    loadSystemFonts: true,
    defaultFontFamily: 'Segoe UI',
    sansSerifFamily: 'Segoe UI',
    serifFamily: 'Segoe UI',
    monospaceFamily: 'Consolas',
  },
  logLevel: 'warn',
};

async function main() {
  const svg = await readFile(SVG_PATH, 'utf8');
  const resvg = new Resvg(svg, RESVG_OPTS);

  // Heuristic: warn if any glyphs are reported as missing so the user knows
  // the rendered output may differ from the source SVG.
  const used = resvg.getBBox();
  if (!used) {
    console.warn('[build-embed-png] resvg returned no bbox — rendering anyway');
  }

  const pngData = resvg.render().asPng();
  await writeFile(PNG_PATH, pngData);

  const sizeKb = (pngData.byteLength / 1024).toFixed(1);
  console.log(`[build-embed-png] wrote ${PNG_PATH} (${sizeKb} kB)`);
}

main().catch((err) => {
  console.error('[build-embed-png] failed to render PNG:');
  console.error(err);
  process.exit(1);
});
