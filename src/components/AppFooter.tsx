interface AppFooterProps {
  mlDriven?: boolean;
}

export default function AppFooter({ mlDriven = false }: AppFooterProps) {
  return (
    <footer className="border-t-[3px] border-ink bg-ink text-paper">
      <div className="w-full px-4 py-3 xl:px-5 flex items-center justify-between flex-wrap gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/60">
          AutoOutlook · Automated Convective Risk Intelligence · v1.3
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-paper/40">
          {mlDriven
            ? 'Hazard-probability model · Provider chain: live → fallback → mock'
            : 'Rule-based outlook engine · Provider chain: live → fallback → mock'}
        </span>
      </div>
    </footer>
  );
}
