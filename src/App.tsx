import { lazy, Suspense, useEffect, useRef, useState } from 'react';

import ViewTransitionOverlay, { type TransitionView } from './components/ViewTransitionOverlay';
import { DASHBOARD_ANCHORS, NAVIGATION_UNRESOLVED_EVENT } from './utils/navigateView';

const LandingPage = lazy(() => import('./components/landing/LandingPage'));
const DashboardView = lazy(() => import('./views/DashboardView'));
const DocumentationView = lazy(() => import('./views/DocumentationView'));
const ChangelogPage = lazy(() => import('./components/changelog/ChangelogPage'));

type AppView = TransitionView;

function viewFromHash(): AppView {
  if (typeof window === 'undefined') return 'landing';
  const id = window.location.hash.replace(/^#/, '');
  if (id === 'docs' || id.startsWith('docs-')) return 'docs';
  if (id === 'changelog' || id.startsWith('release-')) return 'changelog';
  if (DASHBOARD_ANCHORS.has(id)) return 'dashboard';
  return 'landing';
}

function ViewLoadingFallback() {
  return <div className="min-h-screen bg-paper" aria-label="Loading view" />;
}

export default function App() {
  const [view, setView] = useState<AppView>(() => viewFromHash());
  const [cycle, setCycle] = useState(0);
  const [navNotice, setNavNotice] = useState<{ message: string; nonce: number } | null>(null);

  useEffect(() => {
    const sync = () => setView(viewFromHash());
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);

  useEffect(() => {
    const onUnresolved = () =>
      setNavNotice((previous) => ({
        message: 'View change did not complete',
        nonce: (previous?.nonce ?? 0) + 1,
      }));
    window.addEventListener(NAVIGATION_UNRESOLVED_EVENT, onUnresolved);
    return () => window.removeEventListener(NAVIGATION_UNRESOLVED_EVENT, onUnresolved);
  }, []);

  useEffect(() => {
    if (!navNotice) return;
    const timer = window.setTimeout(() => setNavNotice(null), 4000);
    return () => window.clearTimeout(timer);
  }, [navNotice?.nonce]);

  const previousViewRef = useRef(view);
  useEffect(() => {
    if (previousViewRef.current === view) return;
    previousViewRef.current = view;
    setCycle((value) => value + 1);
  }, [view]);

  // A lazy view may not be mounted on the first animation frame. Retry briefly
  // so deep links still land on their target after that view's chunk arrives.
  useEffect(() => {
    const hash = window.location.hash.replace(/^#/, '');
    let animationFrame = 0;
    let attempts = 0;
    const scrollToTarget = () => {
      if (!hash) {
        window.scrollTo({ top: 0 });
        return;
      }
      const target = document.getElementById(hash);
      if (target) {
        target.scrollIntoView({ block: 'start' });
        return;
      }
      attempts += 1;
      if (attempts < 120) {
        animationFrame = window.requestAnimationFrame(scrollToTarget);
      } else {
        window.scrollTo({ top: 0 });
      }
    };
    animationFrame = window.requestAnimationFrame(scrollToTarget);
    return () => window.cancelAnimationFrame(animationFrame);
  }, [view]);

  const navNoticeElement = navNotice ? (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 border-[3px] border-ink bg-signal-amber px-4 py-2 font-mono text-[11px] uppercase tracking-[0.2em] text-ink shadow-retro"
    >
      {navNotice.message}
    </div>
  ) : null;

  return (
    <>
      <Suspense fallback={<ViewLoadingFallback />}>
        {view === 'landing' && <LandingPage />}
        {view === 'dashboard' && <DashboardView />}
        {view === 'docs' && <DocumentationView />}
        {view === 'changelog' && <ChangelogPage />}
      </Suspense>
      <ViewTransitionOverlay key={`tx-${view}-${cycle}`} view={view} cycle={cycle} />
      {navNoticeElement}
    </>
  );
}
