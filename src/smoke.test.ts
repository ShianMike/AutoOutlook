import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

// Minimal smoke test confirming the vitest runner executes and that
// fast-check is wired up for the property-based tests introduced later.
describe('frontend test runner smoke test', () => {
  it('runs a basic assertion', () => {
    expect(1 + 1).toBe(2);
  });

  it('runs a fast-check property', () => {
    fc.assert(
      fc.property(fc.integer(), fc.integer(), (a, b) => {
        return a + b === b + a;
      }),
      { numRuns: 100 },
    );
  });
});
