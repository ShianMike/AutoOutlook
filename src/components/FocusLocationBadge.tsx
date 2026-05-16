import type { FocusLocation } from '../utils/focusLocation';

interface FocusLocationBadgeProps {
  focus: FocusLocation;
  label?: string;
}

export default function FocusLocationBadge({
  focus,
  label = 'Risk Center',
}: FocusLocationBadgeProps) {
  const detail = [focus.usesCoordinateLabel ? '' : focus.coord, focus.states].filter(Boolean).join(' · ');
  return (
    <div className="max-w-[16rem] border-[2px] border-paper px-2 py-1 text-right">
      <div className="font-mono text-[8px] font-bold uppercase tracking-[0.18em] text-paper/55">
        {label}
      </div>
      <div className="truncate font-display text-[12px] font-extrabold uppercase leading-tight text-paper">
        {focus.label}
      </div>
      {detail && (
        <div className="truncate font-mono text-[9px] uppercase tracking-[0.12em] text-paper/70">
          {detail}
        </div>
      )}
    </div>
  );
}
