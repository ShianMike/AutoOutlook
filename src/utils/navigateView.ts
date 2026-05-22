// Shared client-side navigator for AutoOutlook's hash-based view routing.
//
// Views are resolved by `App.tsx#viewFromHash` from `window.location.hash`:
//   ''                -> landing
//   '#dashboard' or a known dashboard section anchor -> dashboard
//   '#docs' or '#docs-*'                              -> docs
//
// `App.tsx` only listens to the browser `hashchange` event, so this helper
// makes sure that even when we strip the hash entirely (returning to the
// landing page), the view re-evaluates.

export type NavigationTarget = '' | '#landing' | '#dashboard' | '#docs' | string;

export function navigateView(target: NavigationTarget) {
  if (typeof window === 'undefined') return;

  const next =
    target === '' || target === '#landing'
      ? ''
      : target.startsWith('#')
        ? target
        : `#${target}`;

  const current = window.location.hash;

  if (next === '' && current === '') {
    window.scrollTo({ top: 0 });
    return;
  }

  if (next === '') {
    // Drop the hash without leaving a bare "#" in the URL bar.
    window.history.pushState(null, '', window.location.pathname + window.location.search);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    window.scrollTo({ top: 0 });
    return;
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
}

// Convenience: build an onClick handler that navigates to the given target.
export function viewLinkHandler(target: NavigationTarget) {
  return (event: { preventDefault: () => void }) => {
    event.preventDefault();
    navigateView(target);
  };
}
