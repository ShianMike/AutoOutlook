import type { ReactNode } from 'react';

interface RetroPanelProps {
  title?: string;
  eyebrow?: string;
  badge?: ReactNode;
  children: ReactNode;
  className?: string;
  size?: 'sm' | 'md' | 'lg';
  scanline?: boolean;
}

const sizeClasses = {
  sm: 'border-[2px] shadow-retro-sm',
  md: 'border-[3px] shadow-retro',
  lg: 'border-[4px] shadow-retro-lg',
} as const;

export default function RetroPanel({
  title,
  eyebrow,
  badge,
  children,
  className = '',
  size = 'md',
  scanline = false,
}: RetroPanelProps) {
  return (
    <section
      className={[
        'bg-paper border-ink rounded-none flex flex-col',
        sizeClasses[size],
        scanline ? 'retro-scanline' : '',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      {(title || eyebrow || badge) && (
        <header className="flex items-end justify-between gap-3 border-b-[3px] border-ink bg-ink px-4 py-2 text-paper">
          <div className="flex flex-col">
            {eyebrow && (
              <span className="font-mono text-[10px] uppercase tracking-[0.25em] text-paper/60">
                {eyebrow}
              </span>
            )}
            {title && (
              <h2 className="font-display text-lg font-extrabold uppercase leading-none tracking-wide">
                {title}
              </h2>
            )}
          </div>
          {badge && <div className="shrink-0">{badge}</div>}
        </header>
      )}
      <div className="flex-1 p-4">{children}</div>
    </section>
  );
}
