/**
 * Risk Archive event ordering (Requirement 5).
 *
 * The Risk Archive assembles its displayed events from two sources: the live
 * auto-archived ENH+ days (`liveEvents`) and the curated historical catalog
 * (`catalogEvents`). Previously the component sorted only the live set before
 * merging, which produced an inconsistent ordering across the combined set.
 *
 * `orderArchiveEvents` is a pure function that:
 *   1. Combines live + catalog into a single set (Requirement 5.2).
 *   2. Drops events whose `Issued_Date` is missing, null, or unparseable
 *      (Requirement 5.5).
 *   3. De-duplicates by parsed `Issued_Date`, keeping the live event when a
 *      live and a catalog event resolve to the same date (Requirement 5.3, 5.4).
 *   4. Sorts the combined, de-duplicated set by `Issued_Date` descending so the
 *      most recent event appears first (Requirement 5.1).
 *
 * `Issued_Date` corresponds to the event's `eventDate` field (`YYYY-MM-DD`).
 * The function is generic over the event shape so it can order either full
 * `HistoricalEnhPlusEvent` objects or lightweight catalog metadata items — it
 * only reads `eventDate`.
 */

/**
 * Parse an `Issued_Date` value into a comparable timestamp.
 *
 * Returns `null` when the value is missing, null, or unparseable so callers can
 * exclude the event (Requirement 5.5). We require a non-empty string that
 * `Date.parse` accepts; anything else (empty string, whitespace, garbage) is
 * treated as unparseable.
 */
function parseIssuedDate(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (trimmed === '') return null;
  const parsed = Date.parse(trimmed);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * Combine, de-duplicate, and order Risk Archive events by `Issued_Date`
 * descending, giving live events precedence over catalog events on ties.
 */
export function orderArchiveEvents<T extends { eventDate?: unknown }>(
  liveEvents: T[],
  catalogEvents: T[],
): T[] {
  // Live events are considered first so they win de-duplication ties against
  // catalog events sharing the same Issued_Date (Requirement 5.4).
  const combined: T[] = [...liveEvents, ...catalogEvents];

  const seen = new Set<string>();
  const deduped: Array<{ event: T; issuedAt: number }> = [];

  for (const event of combined) {
    const issuedAt = parseIssuedDate(event?.eventDate);
    // Exclude events with a missing/null/unparseable Issued_Date (Req 5.5).
    if (issuedAt === null) continue;
    // Key de-duplication on the parsed Issued_Date value (Req 5.3). Using the
    // parsed timestamp collapses differently-formatted-but-equal dates.
    const key = String(issuedAt);
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push({ event, issuedAt });
  }

  // Sort descending by parsed Issued_Date (Requirement 5.1). Ties cannot occur
  // here because de-duplication already removed same-date collisions.
  deduped.sort((a, b) => b.issuedAt - a.issuedAt);

  return deduped.map((entry) => entry.event);
}
