interface MapWatermarkProps {
  className?: string;
}

export default function MapWatermark({ className = '' }: MapWatermarkProps) {
  return (
    <span
      className={[
        'pointer-events-none inline-flex items-center border-[2px] border-ink bg-paper px-2 py-0.5 font-mono text-[8px] font-bold uppercase leading-none tracking-[0.2em] text-ink shadow-retro-sm',
        className,
      ].filter(Boolean).join(' ')}
    >
      autooutlook.tech
    </span>
  );
}
