import { ValidationError } from '../../lib/errors.js';

/**
 * Enhanced charitable deduction for donated food inventory under
 * IRS Section 170(e)(3):
 *
 *   deduction = COGS + (FMV - COGS) / 2, capped at 2 x COGS
 *
 * When the food's fair market value has fallen below its cost basis, the
 * enhanced formula would understate reality; the deduction is then limited
 * to FMV (you cannot deduct appreciation that does not exist).
 *
 * All amounts are integer cents. Results round down to the nearest cent,
 * the conservative direction for an audit.
 */
export interface TaxDeductionResult {
  fmvCents: number;
  cogsCents: number;
  deductionCents: number;
  /** True when the 2 x COGS ceiling clipped the raw formula. */
  cappedAtTwiceCogs: boolean;
}

function assertMonetary(name: string, cents: number): void {
  if (!Number.isSafeInteger(cents)) {
    throw new ValidationError(`${name} must be an integer number of cents, got ${cents}`);
  }
  if (cents < 0) {
    throw new ValidationError(`${name} must be >= 0, got ${cents}`);
  }
}

export function calculateEnhancedDeduction(fmvCents: number, cogsCents: number): TaxDeductionResult {
  assertMonetary('fmvCents', fmvCents);
  assertMonetary('cogsCents', cogsCents);

  if (fmvCents <= cogsCents) {
    return { fmvCents, cogsCents, deductionCents: fmvCents, cappedAtTwiceCogs: false };
  }

  const enhanced = cogsCents + Math.floor((fmvCents - cogsCents) / 2);
  const cap = 2 * cogsCents;
  const deductionCents = Math.min(enhanced, cap);

  return { fmvCents, cogsCents, deductionCents, cappedAtTwiceCogs: enhanced > cap };
}
