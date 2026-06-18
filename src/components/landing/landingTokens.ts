/**
 * Brutalist design-system token resolver for the landing page refresh.
 *
 * Requirement 1 (Preserve Brutalist Visual Identity) requires that every
 * color, border, shadow, and type role used on the landing page resolves to an
 * existing token defined in `tailwind.config.ts` / `src/index.css`. This module
 * is the single source of truth for that token set and provides
 * `resolveToken(property, value)`, which guarantees its result is always a
 * member of the allowed token set for the requested property:
 *
 *   1. If the requested value matches an allowed token (by token name or by the
 *      token's underlying value), that token is returned.            (1.1–1.4)
 *   2. Otherwise the property's default token is returned.            (1.5)
 *   3. If the default token is itself unavailable, the nearest defined token in
 *      the same category is returned.                                (1.6)
 *
 * The function never returns a value outside the token set.
 */

export type TokenProperty = 'color' | 'border' | 'shadow' | 'type';

/**
 * Color palette tokens (from `tailwind.config.ts` `theme.extend.colors`).
 * Keys are the canonical token names; values are the underlying hex values so
 * that a requested hex/value can be mapped back to its token, and so the
 * "nearest defined token" fallback can compare by perceptual distance.
 */
export const COLOR_TOKENS = {
  paper: '#f5f1e8',
  ink: '#111111',
  navy: '#0e1a3a',
  'signal-red': '#ef3b2c',
  'signal-amber': '#f7b500',
  'signal-orange': '#ff8c00',
  'signal-lime': '#9ad62a',
  'signal-cyan': '#16c1ff',
  'signal-violet': '#7a0177',
  'risk-tstm': '#9ad62a',
  'risk-mrgl': '#f7b500',
  'risk-slgt': '#ff8c00',
  'risk-enh': '#ef3b2c',
  'risk-mod': '#b30000',
  'risk-high': '#7a0177',
} as const;

/**
 * Border tokens. The brutalist system uses heavy `border-ink` outlines at three
 * widths (see `.retro-*` classes in `src/index.css`). Every border token pairs
 * with `border-ink`; the token name carries the width.
 */
export const BORDER_TOKENS = ['border-[2px]', 'border-[3px]', 'border-[4px]'] as const;

/**
 * Shadow tokens (from `tailwind.config.ts` `theme.extend.boxShadow`). Hard
 * offset shadows only — no blurred/soft shadow exists in the set.
 */
export const SHADOW_TOKENS = [
  'shadow-retro',
  'shadow-retro-sm',
  'shadow-retro-lg',
  'shadow-retro-inset',
] as const;

/**
 * Type-role tokens (from `tailwind.config.ts` `theme.extend.fontFamily`).
 * display → headings, mono → labels, sans → body copy.
 */
export const TYPE_TOKENS = ['font-display', 'font-mono', 'font-sans'] as const;

/** Per-property default token (Requirement 1.5). */
export const DEFAULT_TOKENS: Record<TokenProperty, string> = {
  color: 'ink',
  border: 'border-[3px]',
  shadow: 'shadow-retro',
  type: 'font-sans',
};

const COLOR_TOKEN_NAMES = Object.keys(COLOR_TOKENS) as Array<keyof typeof COLOR_TOKENS>;

/** Ordered list of allowed token names for a property category. */
export function tokensForProperty(property: TokenProperty): readonly string[] {
  switch (property) {
    case 'color':
      return COLOR_TOKEN_NAMES;
    case 'border':
      return BORDER_TOKENS;
    case 'shadow':
      return SHADOW_TOKENS;
    case 'type':
      return TYPE_TOKENS;
    default:
      return [];
  }
}

/** Normalize a token-name candidate for tolerant matching. */
function normalizeName(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, '-');
}

/** Parse a `#rgb` / `#rrggbb` string into an [r, g, b] triple, or null. */
function parseHex(value: string): [number, number, number] | null {
  const hex = value.trim().toLowerCase();
  const short = /^#?([0-9a-f])([0-9a-f])([0-9a-f])$/.exec(hex);
  if (short) {
    return [
      parseInt(short[1] + short[1], 16),
      parseInt(short[2] + short[2], 16),
      parseInt(short[3] + short[3], 16),
    ];
  }
  const full = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/.exec(hex);
  if (full) {
    return [
      parseInt(full[1], 16),
      parseInt(full[2], 16),
      parseInt(full[3], 16),
    ];
  }
  return null;
}

/** Squared Euclidean distance between two RGB triples. */
function rgbDistanceSq(a: [number, number, number], b: [number, number, number]): number {
  const dr = a[0] - b[0];
  const dg = a[1] - b[1];
  const db = a[2] - b[2];
  return dr * dr + dg * dg + db * db;
}

/**
 * Nearest defined token in the same category (Requirement 1.6 fallback). For
 * colors with a parseable hex value this is the perceptually closest palette
 * token; otherwise it is the first defined token in the category (which always
 * exists for the non-empty brutalist token set).
 */
function nearestToken(property: TokenProperty, value: string): string {
  const tokens = tokensForProperty(property);
  if (property === 'color') {
    const requested = parseHex(value);
    if (requested) {
      let best = COLOR_TOKEN_NAMES[0] as string;
      let bestDist = Infinity;
      for (const name of COLOR_TOKEN_NAMES) {
        const tokenRgb = parseHex(COLOR_TOKENS[name]);
        if (!tokenRgb) continue;
        const dist = rgbDistanceSq(requested, tokenRgb);
        if (dist < bestDist) {
          bestDist = dist;
          best = name;
        }
      }
      return best;
    }
  }
  return tokens[0];
}

/** True when `token` is a member of the allowed set for `property`. */
export function isAllowedToken(property: TokenProperty, token: string): boolean {
  return tokensForProperty(property).includes(token);
}

/**
 * Resolve a requested style value to an allowed brutalist token for the given
 * property. The returned token is always a member of the property's token set.
 *
 * @param property One of `color`, `border`, `shadow`, `type`.
 * @param value    The requested token name or underlying value.
 * @returns        An allowed token name for `property`.
 */
export function resolveToken(property: TokenProperty, value: string): string {
  const tokens = tokensForProperty(property);

  // 1. Direct match on the token name (case/spacing tolerant).
  if (typeof value === 'string') {
    if (tokens.includes(value)) {
      return value;
    }
    const normalized = normalizeName(value);
    const byName = tokens.find((token) => normalizeName(token) === normalized);
    if (byName) {
      return byName;
    }

    // 2. For colors, match on the underlying hex value.
    if (property === 'color') {
      const requested = parseHex(value);
      if (requested) {
        const byValue = COLOR_TOKEN_NAMES.find((name) => {
          const tokenRgb = parseHex(COLOR_TOKENS[name]);
          return tokenRgb !== null && rgbDistanceSq(requested, tokenRgb) === 0;
        });
        if (byValue) {
          return byValue;
        }
      }
    }
  }

  // 3. Fall back to the property's default token (Requirement 1.5).
  const fallback = DEFAULT_TOKENS[property];
  if (fallback && tokens.includes(fallback)) {
    return fallback;
  }

  // 4. Default unavailable: nearest defined token in the same category (1.6).
  return nearestToken(property, value);
}
