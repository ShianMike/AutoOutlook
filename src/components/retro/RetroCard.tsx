import type { CSSProperties, ReactNode } from 'react';

interface RetroCardProps {
  children: ReactNode;
  className?: string;
  size?: 'sm' | 'md' | 'lg';
  tone?: 'paper' | 'ink' | 'navy';
  style?: CSSProperties;
  scanline?: boolean;
}

const toneClasses: Record<NonNullable<RetroCardProps['tone']>, string> = {
  paper: 'bg-paper text-ink',
  ink: 'bg-ink text-paper',
  navy: 'bg-navy text-paper',
};

const sizeClasses: Record<NonNullable<RetroCardProps['size']>, string> = {
  sm: 'border-[2px] shadow-retro-sm',
  md: 'border-[3px] shadow-retro',
  lg: 'border-[4px] shadow-retro-lg',
};

export default function RetroCard({
  children,
  className = '',
  size = 'md',
  tone = 'paper',
  style,
  scanline = false,
}: RetroCardProps) {
  return (
    <div
      style={style}
      className={[
        'border-ink rounded-none relative',
        toneClasses[tone],
        sizeClasses[size],
        scanline ? 'retro-scanline' : '',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      {children}
    </div>
  );
}
