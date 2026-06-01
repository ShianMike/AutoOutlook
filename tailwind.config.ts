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
        // View-transition overlay animations
        overlayIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        overlayOut: {
          '0%': { opacity: '1', transform: 'translateY(0)' },
          '100%': { opacity: '0', transform: 'translateY(-6px)' },
        },
        panelIn: {
          '0%': { opacity: '0', transform: 'translateY(12px) scale(0.97)' },
          '60%': { opacity: '1', transform: 'translateY(0) scale(1.005)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        loadBar: {
          '0%': { width: '0%' },
          '60%': { width: '78%' },
          '100%': { width: '100%' },
        },
        radarSweep: {
          '0%': { transform: 'rotate(0deg)' },
          '100%': { transform: 'rotate(360deg)' },
        },
        cornerSpin: {
          '0%': { transform: 'rotate(0deg)' },
          '100%': { transform: 'rotate(90deg)' },
        },
        bootLine: {
          '0%': { opacity: '0', transform: 'translateX(-6px)' },
          '40%, 100%': { opacity: '1', transform: 'translateX(0)' },
        },
        blink: {
          '0%, 49%': { opacity: '1' },
          '50%, 100%': { opacity: '0' },
        },
      },
      animation: {
        'pulse-dot': 'pulseDot 1.4s ease-in-out infinite',
        scan: 'scan 6s linear infinite',
        ticker: 'ticker 40s linear infinite',
        'overlay-in': 'overlayIn 180ms ease-out forwards',
        'overlay-out': 'overlayOut 280ms ease-in forwards',
        'panel-in': 'panelIn 320ms cubic-bezier(0.2, 0.85, 0.35, 1.05) forwards',
        'load-bar': 'loadBar 1900ms cubic-bezier(0.32, 0, 0.34, 1) forwards',
        'radar-sweep': 'radarSweep 1.6s linear infinite',
        'corner-spin': 'cornerSpin 700ms ease-out forwards',
        'boot-line': 'bootLine 360ms ease-out forwards',
        blink: 'blink 0.9s steps(2, start) infinite',
      },
    },
  },
  plugins: [],
} satisfies Config;
