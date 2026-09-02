import type { SurplusItem } from '../../domain/types.js';
import type { DonationLedger } from '../tax/donation-ledger.js';
import { ValidationError } from '../../lib/errors.js';

/**
 * The Impact Ledger — the environmental twin of the financial ledger, and
 * the single primitive nearly every other incentive surface consumes:
 * supplier ESG exports, city diversion dashboards, sponsor impact meters,
 * consumer impact receipts, team competitions, and (eventually) verified
 * methane-avoidance carbon credits.
 *
 * Per rescued item it computes meals rescued, pounds diverted, CO2e avoided,
 * and hauling cost avoided, writes the record to the immutable ledger
 * (IMPACT_RECORDED), and maintains aggregates by supplier, zone, and month.
 *
 * Factors are transparent, documented estimates (USDA meal weight, EPA
 * WARM-style emission factors, typical commercial hauling rates) and are
 * injectable so a jurisdiction or auditor can swap in their own.
 */

export interface CategoryImpactFactors {
  /** kg CO2e avoided per kg of food diverted from landfill. */
  co2eKgPerKg: number;
  /** Typical weight of one listed unit in grams, used when weightGrams is absent. */
  defaultUnitWeightGrams: number;
}

export const DEFAULT_IMPACT_FACTORS: Record<string, CategoryImpactFactors> = {
  produce: { co2eKgPerKg: 2.1, defaultUnitWeightGrams: 4000 },
  bakery: { co2eKgPerKg: 1.9, defaultUnitWeightGrams: 2500 },
  dairy: { co2eKgPerKg: 3.2, defaultUnitWeightGrams: 3000 },
  meat: { co2eKgPerKg: 6.5, defaultUnitWeightGrams: 2000 },
  prepared: { co2eKgPerKg: 3.0, defaultUnitWeightGrams: 1500 },
  default: { co2eKgPerKg: 2.5, defaultUnitWeightGrams: 2500 },
};

/** USDA: one meal ≈ 1.2 lb ≈ 544 g. */
export const GRAMS_PER_MEAL = 544;
/** Typical commercial organics hauling cost, cents per kg. */
export const DEFAULT_DISPOSAL_CENTS_PER_KG = 18;
const GRAMS_PER_POUND = 453.592;

export interface ItemImpact {
  itemId: string;
  weightGrams: number;
  mealsRescued: number;
  poundsDiverted: number;
  co2eGrams: number;
  avoidedDisposalCents: number;
}

export interface ImpactTotals {
  itemCount: number;
  mealsRescued: number;
  poundsDiverted: number;
  co2eKg: number;
  avoidedDisposalCents: number;
}

export interface ImpactQuery {
  supplierId?: string;
  zoneId?: string;
  /** UTC year/month; both or neither. */
  year?: number;
  month?: number;
}

interface ImpactRecord extends ItemImpact {
  supplierId: string;
  zoneId?: string;
  recordedAt: Date;
}

const EMPTY_TOTALS: ImpactTotals = {
  itemCount: 0,
  mealsRescued: 0,
  poundsDiverted: 0,
  co2eKg: 0,
  avoidedDisposalCents: 0,
};

export class ImpactAccountingService {
  private records: ImpactRecord[] = [];

  constructor(
    private readonly ledger: DonationLedger,
    private readonly factors: Record<string, CategoryImpactFactors> = DEFAULT_IMPACT_FACTORS,
    private readonly disposalCentsPerKg: number = DEFAULT_DISPOSAL_CENTS_PER_KG,
  ) {
    if (!factors['default']) {
      throw new ValidationError('impact factors must include a "default" category');
    }
  }

  /** Pure computation — also used for pre-donation previews in the supplier UI. */
  computeForItem(item: SurplusItem): ItemImpact {
    const factor = this.factors[item.category] ?? this.factors['default']!;
    const weightGrams = item.weightGrams ?? factor.defaultUnitWeightGrams * item.quantity;
    const kg = weightGrams / 1000;
    return {
      itemId: item.id,
      weightGrams,
      mealsRescued: Math.round((weightGrams / GRAMS_PER_MEAL) * 10) / 10,
      poundsDiverted: Math.round((weightGrams / GRAMS_PER_POUND) * 10) / 10,
      co2eGrams: Math.round(kg * factor.co2eKgPerKg * 1000),
      avoidedDisposalCents: Math.round(kg * this.disposalCentsPerKg),
    };
  }

  /**
   * Called when a rescue actually lands (delivery verified or item claimed) —
   * impact is only booked for food that reached someone, never for listings.
   * Idempotent per item.
   */
  async recordRescue(item: SurplusItem, recordedAt: Date): Promise<ItemImpact | undefined> {
    if (this.records.some((r) => r.itemId === item.id)) return undefined;

    const impact = this.computeForItem(item);
    this.records.push({
      ...impact,
      supplierId: item.supplierId,
      ...(item.zoneId !== undefined ? { zoneId: item.zoneId } : {}),
      recordedAt,
    });
    await this.ledger.record(item.id, 'IMPACT_RECORDED', { ...impact });
    return impact;
  }

  totals(query: ImpactQuery = {}): ImpactTotals {
    if ((query.year === undefined) !== (query.month === undefined)) {
      throw new ValidationError('year and month must be provided together');
    }
    return this.records
      .filter(
        (r) =>
          (query.supplierId === undefined || r.supplierId === query.supplierId) &&
          (query.zoneId === undefined || r.zoneId === query.zoneId) &&
          (query.year === undefined ||
            (r.recordedAt.getUTCFullYear() === query.year &&
              r.recordedAt.getUTCMonth() + 1 === query.month)),
      )
      .reduce(
        (t, r) => ({
          itemCount: t.itemCount + 1,
          mealsRescued: Math.round((t.mealsRescued + r.mealsRescued) * 10) / 10,
          poundsDiverted: Math.round((t.poundsDiverted + r.poundsDiverted) * 10) / 10,
          co2eKg: Math.round((t.co2eKg + r.co2eGrams / 1000) * 10) / 10,
          avoidedDisposalCents: t.avoidedDisposalCents + r.avoidedDisposalCents,
        }),
        EMPTY_TOTALS,
      );
  }
}
