import { randomUUID } from 'node:crypto';
import type { SurplusItem } from '../../domain/types.js';
import type { Clock } from '../../lib/clock.js';
import { systemClock } from '../../lib/clock.js';
import { ValidationError } from '../../lib/errors.js';
import { calculateEnhancedDeduction } from '../tax/tax-valuation.service.js';
import type { DonationLedger } from '../tax/donation-ledger.js';
import type { SurplusItemRepository } from './surplus-item.repository.js';

export interface ListSurplusInput {
  supplierId: string;
  title: string;
  category: string;
  quantity?: number;
  weightGrams?: number;
  zoneId?: string;
  dietaryTags?: string[];
  photoUrl?: string;
  fmvCents: number;
  cogsCents: number;
  latitude: number;
  longitude: number;
  /** Cold-chain safety deadline; the item must reach a fridge before this. */
  safeUntil: Date;
  /** Commercial-sale window before rollover to the donation pool. */
  salesWindowMinutes?: number;
  /** Discounted Phase-1 price; defaults to 30% of FMV (a 70% discount). */
  salePriceCents?: number;
}

/**
 * Listing flow for suppliers: one tap (or one POS webhook) creates the item,
 * runs the tax valuation engine, and writes both the donation record and the
 * calculated deduction to the immutable ledger — the supplier does nothing
 * else.
 */
export class SurplusItemService {
  constructor(
    private readonly repo: SurplusItemRepository,
    private readonly ledger: DonationLedger,
    private readonly clock: Clock = systemClock,
  ) {}

  async listSurplus(input: ListSurplusInput): Promise<SurplusItem> {
    if (!input.supplierId) throw new ValidationError('supplierId is required');
    if (!input.title.trim()) throw new ValidationError('title is required');
    if (Math.abs(input.latitude) > 90 || Math.abs(input.longitude) > 180) {
      throw new ValidationError('invalid coordinates');
    }
    const now = this.clock.now();
    if (input.safeUntil.getTime() <= now.getTime()) {
      throw new ValidationError('safeUntil must be in the future — item is no longer safe to redistribute');
    }

    const deduction = calculateEnhancedDeduction(input.fmvCents, input.cogsCents);

    const item: SurplusItem = {
      id: randomUUID(),
      supplierId: input.supplierId,
      title: input.title.trim(),
      category: input.category,
      quantity: input.quantity ?? 1,
      ...(input.weightGrams !== undefined ? { weightGrams: input.weightGrams } : {}),
      ...(input.zoneId !== undefined ? { zoneId: input.zoneId } : {}),
      ...(input.dietaryTags !== undefined ? { dietaryTags: [...input.dietaryTags] } : {}),
      ...(input.photoUrl !== undefined ? { photoUrl: input.photoUrl } : {}),
      fmvCents: input.fmvCents,
      cogsCents: input.cogsCents,
      calculatedTaxDeductionCents: deduction.deductionCents,
      salePriceCents: input.salePriceCents ?? Math.floor(input.fmvCents * 0.3),
      currentState: 'SALES_PHASE',
      listedAt: now,
      salesWindowMinutes: input.salesWindowMinutes ?? 45,
      safeUntil: input.safeUntil,
      latitude: input.latitude,
      longitude: input.longitude,
    };

    const created = await this.repo.create(item);

    await this.ledger.record(created.id, 'DONATION_RECORDED', {
      supplierId: created.supplierId,
      title: created.title,
      category: created.category,
      quantity: created.quantity,
      listedAt: created.listedAt.toISOString(),
    });
    await this.ledger.record(created.id, 'TAX_DEDUCTION_CALCULATED', {
      fmvCents: deduction.fmvCents,
      cogsCents: deduction.cogsCents,
      deductionCents: deduction.deductionCents,
      cappedAtTwiceCogs: deduction.cappedAtTwiceCogs,
    });

    return created;
  }
}
