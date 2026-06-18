// Shared client-side navigator for AutoOutlook's hash-based view routing.
//
// Views are resolved by `App.tsx#viewFromHash` from `window.location.hash`:
//   ''                -> landing
//   '#dashboard' or a known dashboard section anchor -> dashboard
//   '#docs' or '#docs-*'                              -> docs
//   '#changelog' or '#release-*'                      -> changelog
//
// `App.tsx` only listens to the browser `hashchange` event, so this helper
// makes sure that even when we strip the hash entirely (returning to the
// landing page), the view re-evaluates.

export type NavigationTarget = '' | '#landing' | '#dashboard' | '#docs' | string;

export type ResolvedView = 'landing' | 'dashboard' | 'docs' | 'changelog';

// Known dashboard section anchors. A bare `#dashboard` or any of these section
// ids resolves to the dashboard view. Shared with `App.tsx#viewFromHash` so the
// navigator and the router agree on what counts as a known destination.
export const DASHBOARD_ANCHORS = new Set([
  'dashboard',
  'time-scrubber',
  'outlook-map',
  'primary-outlook',
  'hazards',
  'ingredients',
  'timeline',
  'discussion',
  'readiness',
  'verification',
  'system-status',
]);

// Dispatched on `window` when a navigation request cannot be resolved to a known
// destination view. `App.tsx` listens for it to surface a non-blocking "view
// change did not complete" indication while retaining the current active view
// (Req 9.6). No overlay is mounted for the unresolved destination because the
// active view/cycle never changes.
export const NAVIGATION_UNRESOLVED_EVENT = 'autooutlook:navigation-unresolved';

function normalizeTarget(target: NavigationTarget): string {
  return target === '' || target === '#landing'
    ? ''
    : target.startsWith('#')
      ? target
      : `#${target}`;
}

// Strictly resolve a navigation target to a known view, or `null` when the
// target does not correspond to any known destination. Unlike `viewFromHash`
// (which falls back to landing for any arbitrary hash already sitting in the
// URL), this resolver reports unknown navigation *requests* as unresolved so
// callers can signal failure rather than silently routing to landing.
export function resolveTargetView(target: NavigationTarget): ResolvedView | null {
  const id = normalizeTarget(target).replace(/^#/, '');
  if (id === '') return 'landing';
  if (id === 'docs' || id.startsWith('docs-')) return 'docs';
  if (id === 'changelog' || id.startsWith('release-')) return 'changelog';
  if (DASHBOARD_ANCHORS.has(id)) return 'dashboard';
  return null;
}

// Navigate to `target`. Returns `true` when the request resolved to a known view
// and the hash was updated, `false` when the destination could not be resolved
// (in which case the current hash/view is left untouched and a
// `NAVIGATION_UNRESOLVED_EVENT` is dispatched).
export function navigateView(target: NavigationTarget): boolean {
  if (typeof window === 'undefined') return false;

  // Unresolvable destination: keep the current hash/view untouched and notify
  // listeners so the UI can surface a non-blocking failure indication (Req 9.6).
  if (resolveTargetView(target) === null) {
    window.dispatchEvent(
      new CustomEvent(NAVIGATION_UNRESOLVED_EVENT, { detail: { target } }),
    );
    return false;
  }

  const next = normalizeTarget(target);

  const current = window.location.hash;

  if (next === '' && current === '') {
    window.scrollTo({ top: 0 });
    return true;
  }

  if (next === '') {
    // Drop the hash without leaving a bare "#" in the URL bar.
    window.history.pushState(null, '', window.location.pathname + window.location.search);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    window.scrollTo({ top: 0 });
    return true;
  }

  if (current === next) {
    // Same hash: force re-fire so the App routes again and we scroll back up.
    window.location.hash = '';
    window.requestAnimationFrame(() => {
      window.location.hash = next;
    });
  } else {
    window.location.hash = next;
  }

  window.scrollTo({ top: 0 });
  return true;
}

// Convenience: build an onClick handler that navigates to the given target.
export function viewLinkHandler(target: NavigationTarget) {
  return (event: { preventDefault: () => void }) => {
    event.preventDefault();
    navigateView(target);
  };
}
