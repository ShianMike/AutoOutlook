import { defineConfig } from 'vitest/config';

// Frontend test runner configuration.
// Pure-logic modules (cache identity, archive ordering, backing resolution,
// timeline derivation) run under the default `node` environment. Component
// render tests can opt into a DOM environment per-file via a
// `// @vitest-environment jsdom` docblock when introduced later.
export default defineConfig({
  test: {
    globals: true,
    environment: 'node',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
});
