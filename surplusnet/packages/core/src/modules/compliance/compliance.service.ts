import { NotFoundError } from '../../lib/errors.js';
import type { SurplusItemRepository } from '../inventory/surplus-item.repository.js';
import type { DeliveryService } from '../delivery/delivery.service.js';
import type { ImpactAccountingService } from '../impact/impact-accounting.service.js';

/**
 * The Automated Liability Shield — the answer to the #1 reason businesses
 * refuse to donate (lawsuit fear, despite Bill Emerson Act protection).
 *
 * Per donation it compiles a compliance certificate from evidence the
 * platform already captures — custody chain, timestamped cold-chain
 * readings, safe-until adherence — so every donation is documented better
 * than the donor's own kitchen logs. Excursions are surfaced honestly:
 * a certificate that hides problems protects nobody.
 *
 * `recoveryReport` is the regulatory product: jurisdictions like California
 * (SB 1383) legally require food businesses to recover surplus and keep
 * records of type, quantity, frequency, and receiving entity. We generate
 * that filing, and staple the CFO's three numbers to it: tax deduction,
 * avoided hauling cost, and CO2e for the ESG report.
 */

export interface CustodyEvent {
  event: string;
  at: Date;
  actor: string;
}

export interface ComplianceCertificate {
  itemId: string;
  supplierId: string;
  itemSummary: { title: string; category: string; quantity: number };
  custodyChain: CustodyEvent[];
  tempLog: Array<{ at: Date; celsius: number }>;
  coldChainCompliant: boolean;
  safeUntilRespected: boolean;
  overallCompliant: boolean;
  goodFaithStatement: string;
}

export interface RecoveryReport {
  supplierId: string;
  year: number;
  month: number;
  itemCount: number;
  poundsRecovered: number;
  mealsRescued: number;
  destinations: string[];
  /** CFO line 1 — enhanced deduction under IRS 170(e)(3). */
  taxDeductionCents: number;
  /** CFO line 2 — hauling fees not paid. */
  avoidedDisposalCents: number;
  /** ESG line — kg CO2e kept out of the atmosphere. */
  co2eKg: number;
  jurisdictionNote: string;
}

export const GOOD_FAITH_STATEMENT =
  'Donated in good faith for distribution to individuals in need, with documented ' +
  'safe-handling custody, under the protections of the Bill Emerson Good Samaritan ' +
  'Food Donation Act (42 U.S.C. § 1791) or the regional equivalent.';

export class ComplianceService {
  constructor(
    private readonly items: SurplusItemRepository,
    private readonly deliveries: DeliveryService,
    private readonly impact: ImpactAccountingService,
  ) {}

  async certificate(itemId: string): Promise<ComplianceCertificate> {
    const item = await this.items.findById(itemId);
    if (!item) throw new NotFoundError(`no surplus item ${itemId}`);
    const delivery = this.deliveries.forItem(itemId);

    const custodyChain: CustodyEvent[] = [
      { event: 'LISTED_BY_SUPPLIER', at: item.listedAt, actor: item.supplierId },
    ];
    if (item.rolledOverAt) {
      custodyChain.push({ event: 'ENTERED_DONATION_POOL', at: item.rolledOverAt, actor: 'system' });
    }
    if (delivery) {
      custodyChain.push({ event: 'ACCEPTED_BY_COURIER', at: delivery.acceptedAt, actor: delivery.courierId });
      if (delivery.pickedUpAt) {
        custodyChain.push({ event: 'PICKED_UP', at: delivery.pickedUpAt, actor: delivery.courierId });
      }
      if (delivery.droppedOffAt) {
        custodyChain.push({ event: 'DELIVERED_TO_RECIPIENT', at: delivery.droppedOffAt, actor: delivery.dropoffName });
      }
    }

    const completedAt = delivery?.droppedOffAt;
    const safeUntilRespected =
      completedAt === undefined ? true : completedAt.getTime() <= item.safeUntil.getTime();
    const coldChainCompliant = delivery?.coldChainCompliant ?? true;

    return {
      itemId,
      supplierId: item.supplierId,
      itemSummary: { title: item.title, category: item.category, quantity: item.quantity },
      custodyChain,
      tempLog: delivery ? delivery.tempReadings.map((r) => ({ ...r })) : [],
      coldChainCompliant,
      safeUntilRespected,
      overallCompliant: coldChainCompliant && safeUntilRespected,
      goodFaithStatement: GOOD_FAITH_STATEMENT,
    };
  }

  async recoveryReport(supplierId: string, year: number, month: number): Promise<RecoveryReport> {
    const supplierItems = await this.items.findBySupplier(supplierId);
    const rescued = supplierItems.filter((item) => {
      if (item.currentState !== 'DELIVERED' && item.currentState !== 'CLAIMED') return false;
      const at = item.rolledOverAt ?? item.listedAt;
      return at.getUTCFullYear() === year && at.getUTCMonth() + 1 === month;
    });

    const impactTotals = this.impact.totals({ supplierId, year, month });
    const destinations = [
      ...new Set(
        rescued
          .map((item) => this.deliveries.forItem(item.id)?.dropoffName ?? item.recipientId)
          .filter((d): d is string => Boolean(d)),
      ),
    ];

    return {
      supplierId,
      year,
      month,
      itemCount: rescued.length,
      poundsRecovered: impactTotals.poundsDiverted,
      mealsRescued: impactTotals.mealsRescued,
      destinations,
      taxDeductionCents: rescued.reduce((s, item) => s + item.calculatedTaxDeductionCents, 0),
      avoidedDisposalCents: impactTotals.avoidedDisposalCents,
      co2eKg: impactTotals.co2eKg,
      jurisdictionNote:
        'Record of edible food recovery: type, quantity, and receiving entities per period. ' +
        'Formatted for organics-recovery mandates such as California SB 1383 §18991.4; ' +
        'verify your jurisdiction’s filing schedule with counsel.',
    };
  }
}
