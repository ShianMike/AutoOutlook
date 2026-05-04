import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        paper: '#f5f1e8',
        ink: '#111111',
        navy: '#0e1a3a',
        signal: {
          red: '#ef3b2c',
          amber: '#f7b500',
          orange: '#ff8c00',
          lime: '#9ad62a',
          cyan: '#16c1ff',
          violet: '#7a0177',
        },
        risk: {
          tstm: '#9ad62a',
          mrgl: '#f7b500',
          slgt: '#ff8c00',
          enh: '#ef3b2c',
          mod: '#b30000',
          high: '#7a0177',
        },
      },
      fontFamily: {
        sans: ['"Inter"', 'system-ui', 'sans-serif'],
        display: ['"Space Grotesk"', '"Inter"', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Courier New"', 'monospace'],
      },
      boxShadow: {
        retro: '6px 6px 0 0 #111111',
        'retro-sm': '3px 3px 0 0 #111111',
        'retro-lg': '10px 10px 0 0 #111111',
        'retro-inset': 'inset 4px 4px 0 0 #111111',
      },
      keyframes: {
        pulseDot: {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.4', transform: 'scale(0.85)' },
        },
        scan: {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100%)' },
        },
        ticker: {
          '0%': { transform: 'translateX(0)' },
          '100%': { transform: 'translateX(-50%)' },
        },
      },
      animation: {
        'pulse-dot': 'pulseDot 1.4s ease-in-out infinite',
        scan: 'scan 6s linear infinite',
        ticker: 'ticker 40s linear infinite',
      },
    },
  },
  plugins: [],
} satisfies Config;
