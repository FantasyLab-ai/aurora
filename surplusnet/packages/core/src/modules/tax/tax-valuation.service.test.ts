import { describe, expect, it } from 'vitest';
import { calculateEnhancedDeduction } from './tax-valuation.service.js';
import { ValidationError } from '../../lib/errors.js';

describe('calculateEnhancedDeduction (IRS 170(e)(3))', () => {
  it('applies COGS + half the appreciation for a typical donation', () => {
    // FMV $10.00, COGS $4.00 → 400 + (1000-400)/2 = 700
    const r = calculateEnhancedDeduction(1000, 400);
    expect(r.deductionCents).toBe(700);
    expect(r.cappedAtTwiceCogs).toBe(false);
  });

  it('caps the deduction at 2 x COGS for high-margin items', () => {
    // FMV $10.00, COGS $2.00 → raw 200 + 400 = 600, cap 400
    const r = calculateEnhancedDeduction(1000, 200);
    expect(r.deductionCents).toBe(400);
    expect(r.cappedAtTwiceCogs).toBe(true);
  });

  it('sits exactly on the cap when appreciation equals 2 x COGS', () => {
    // FMV = 3 x COGS: raw = COGS + COGS = 2 x COGS, precisely the ceiling
    const r = calculateEnhancedDeduction(900, 300);
    expect(r.deductionCents).toBe(600);
    expect(r.cappedAtTwiceCogs).toBe(false);
  });

  it('limits the deduction to FMV when value has fallen below cost', () => {
    const r = calculateEnhancedDeduction(300, 500);
    expect(r.deductionCents).toBe(300);
  });

  it('rounds odd appreciation down to the cent', () => {
    // 300 + (501-300)/2 = 300 + 100.5 → floor to 400
    const r = calculateEnhancedDeduction(501, 300);
    expect(r.deductionCents).toBe(400);
    expect(r.cappedAtTwiceCogs).toBe(false);
  });

  it('handles zero-cost items (home-garden gluts): deduction is 0, not half of FMV', () => {
    // COGS 0 → cap 2 x 0 = 0
    const r = calculateEnhancedDeduction(1000, 0);
    expect(r.deductionCents).toBe(0);
    expect(r.cappedAtTwiceCogs).toBe(true);
  });

  it('rejects negative and non-integer amounts', () => {
    expect(() => calculateEnhancedDeduction(-1, 100)).toThrow(ValidationError);
    expect(() => calculateEnhancedDeduction(100, -1)).toThrow(ValidationError);
    expect(() => calculateEnhancedDeduction(10.5, 1)).toThrow(ValidationError);
  });
});
