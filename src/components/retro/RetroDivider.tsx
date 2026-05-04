interface RetroDividerProps {
  className?: string;
  vertical?: boolean;
  thickness?: 2 | 3 | 4;
}

export default function RetroDivider({
  className = '',
  vertical = false,
  thickness = 3,
}: RetroDividerProps) {
  const t = thickness === 2 ? 'h-[2px]' : thickness === 4 ? 'h-[4px]' : 'h-[3px]';
  const tv = thickness === 2 ? 'w-[2px]' : thickness === 4 ? 'w-[4px]' : 'w-[3px]';
  if (vertical) {
    return <div className={`${tv} h-full bg-ink ${className}`} aria-hidden />;
  }
  return <div className={`${t} w-full bg-ink ${className}`} aria-hidden />;
}
