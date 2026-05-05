interface MapWatermarkProps {
  className?: string;
}

export default function MapWatermark({ className = '' }: MapWatermarkProps) {
  return (
    <span
      className={[
        'pointer-events-none inline-flex items-center border-[1.5px] border-paper bg-paper px-2 py-0.5 font-mono text-[8px] font-bold uppercase leading-none tracking-[0.2em] text-ink shadow-[1px_1px_0_0_#777777]',
        className,
      ].filter(Boolean).join(' ')}
    >
      autooutlook.tech
    </span>
  );
}
