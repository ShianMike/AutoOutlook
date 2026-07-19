import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    // Keep route-level lazy loading effective as the dashboard grows.
    chunkSizeWarningLimit: 350,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
    watch: {
      // Don't trigger HMR on Python venv / backend / temp dirs.
      ignored: [
        '**/.venv/**',
        '**/backend/**',
        '**/__pycache__/**',
        '**/.tmp/**',
        '**/dist/**',
      ],
    },
  },
});
