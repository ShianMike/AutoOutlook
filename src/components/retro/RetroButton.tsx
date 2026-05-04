import type { ButtonHTMLAttributes, ReactNode } from 'react';

interface RetroButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  primary?: boolean;
  iconOnly?: boolean;
}

export default function RetroButton({
  children,
  primary = false,
  iconOnly = false,
  className = '',
  type = 'button',
  ...rest
}: RetroButtonProps) {
  return (
    <button
      type={type}
      className={[
        'retro-button',
        primary ? 'retro-button-primary' : '',
        iconOnly ? '!px-2 !py-2' : '',
        rest.disabled ? 'opacity-40 cursor-not-allowed' : '',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      {...rest}
    >
      {children}
    </button>
  );
}
