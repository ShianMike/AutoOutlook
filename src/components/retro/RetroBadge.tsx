import type { ReactNode } from 'react';

interface RetroBadgeProps {
  children: ReactNode;
  tone?: 'paper' | 'ink' | 'lime' | 'amber' | 'red' | 'cyan' | 'orange';
  className?: string;
  pulse?: boolean;
}

const toneClasses: Record<NonNullable<RetroBadgeProps['tone']>, string> = {
  paper: 'bg-paper text-ink',
  ink: 'bg-ink text-paper',
  lime: 'bg-signal-lime text-ink',
  amber: 'bg-signal-amber text-ink',
  red: 'bg-signal-red text-paper',
  cyan: 'bg-signal-cyan text-ink',
  orange: 'bg-signal-orange text-ink',
};

export default function RetroBadge({
  children,
  tone = 'paper',
  className = '',
  pulse = false,
}: RetroBadgeProps) {
  return (
    <span className={`retro-badge ${toneClasses[tone]} ${className}`}>
      {pulse && (
        <span
          className="inline-block h-2 w-2 rounded-full bg-current animate-pulse-dot"
          aria-hidden
        />
      )}
      {children}
    </span>
  );
}
