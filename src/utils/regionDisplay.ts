const GENERATED_OUTLOOK_FOCUS_RE = /^Generated(?:\s+(?:TSTM|MRGL|SLGT|ENH|MDT|MOD|HIGH))?\s+Outlook\s+Focus$/i;

export function displayRegionLabel(label: string | undefined, fallback = 'the highlighted corridor'): string {
  const cleaned = label?.trim();
  if (!cleaned || GENERATED_OUTLOOK_FOCUS_RE.test(cleaned)) return fallback;
  return cleaned;
}
