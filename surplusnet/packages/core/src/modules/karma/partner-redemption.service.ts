import { randomUUID } from 'node:crypto';
import { NotFoundError, ValidationError } from '../../lib/errors.js';
import type { WalletService } from '../wallet/wallet.service.js';

/**
 * The Karma Token Utility System (Epic 3): local merchants — cafes, transit,
 * bookstores, the very grocers being helped — register perks that couriers
 * buy with Karma Credits. Karma never converts to cash for the courier; it
 * converts to real local value, which keeps the network a volunteer economy
 * rather than a sub-minimum-wage gig market.
 *
 * Redemption debits the courier's karma balance (idempotent per redemption),
 * decrements perk inventory, and issues a voucher code the merchant scans.
 * Every redemption is tallied per partner so sponsors / the city can settle
 * with merchants at an agreed rate.
 */

export interface Partner {
  partnerId: string;
  name: string;
  category: 'CAFE' | 'TRANSIT' | 'GROCER' | 'BOOKSTORE' | 'EVENTS' | 'OTHER';
}

export interface Perk {
  perkId: string;
  partnerId: string;
  title: string;
  costKarma: number;
  inventory: number;
}

export interface Voucher {
  voucherCode: string;
  perkId: string;
  partnerId: string;
  courierId: string;
  costKarma: number;
  issuedAt: Date;
  redeemedAt?: Date;
}

export class PartnerRedemptionService {
  private partners = new Map<string, Partner>();
  private perks = new Map<string, Perk>();
  private vouchers = new Map<string, Voucher>();

  constructor(private readonly wallets: WalletService) {}

  registerPartner(partner: Partner): void {
    if (!partner.partnerId || !partner.name.trim()) {
      throw new ValidationError('partnerId and name are required');
    }
    this.partners.set(partner.partnerId, { ...partner });
  }

  addPerk(perk: Perk): void {
    if (!this.partners.has(perk.partnerId)) {
      throw new NotFoundError(`no partner ${perk.partnerId}`);
    }
    if (!Number.isSafeInteger(perk.costKarma) || perk.costKarma <= 0) {
      throw new ValidationError('costKarma must be a positive integer');
    }
    if (!Number.isSafeInteger(perk.inventory) || perk.inventory < 0) {
      throw new ValidationError('inventory must be a non-negative integer');
    }
    this.perks.set(perk.perkId, { ...perk });
  }

  listPerks(): Perk[] {
    return [...this.perks.values()].filter((p) => p.inventory > 0);
  }

  /**
   * Spend karma on a perk. `redemptionId` comes from the client so a retried
   * request (flaky mobile network) can never double-charge: the wallet layer
   * rejects the replayed idempotency key before inventory is touched.
   */
  async redeem(courierId: string, perkId: string, redemptionId: string): Promise<Voucher> {
    const perk = this.perks.get(perkId);
    if (!perk) throw new NotFoundError(`no perk ${perkId}`);
    if (perk.inventory <= 0) {
      throw new ValidationError(`perk ${perkId} is out of stock`);
    }

    await this.wallets.debit(
      courierId,
      'KARMA_CREDIT',
      perk.costKarma,
      `perk:${perkId}`,
      `redeem-${redemptionId}`,
    );

    perk.inventory -= 1;
    const voucher: Voucher = {
      voucherCode: randomUUID(),
      perkId,
      partnerId: perk.partnerId,
      courierId,
      costKarma: perk.costKarma,
      issuedAt: new Date(),
    };
    this.vouchers.set(voucher.voucherCode, voucher);
    return voucher;
  }

  /** Merchant scans the voucher at the counter; single-use. */
  markVoucherUsed(voucherCode: string): Voucher {
    const voucher = this.vouchers.get(voucherCode);
    if (!voucher) throw new NotFoundError(`no voucher ${voucherCode}`);
    if (voucher.redeemedAt) {
      throw new ValidationError(`voucher ${voucherCode} was already used`);
    }
    voucher.redeemedAt = new Date();
    return { ...voucher };
  }

  /** Settlement view: karma redeemed per partner, for sponsor reimbursement. */
  partnerSettlement(partnerId: string): { partnerId: string; vouchersIssued: number; karmaRedeemed: number } {
    if (!this.partners.has(partnerId)) throw new NotFoundError(`no partner ${partnerId}`);
    const issued = [...this.vouchers.values()].filter((v) => v.partnerId === partnerId);
    return {
      partnerId,
      vouchersIssued: issued.length,
      karmaRedeemed: issued.reduce((s, v) => s + v.costKarma, 0),
    };
  }
}
