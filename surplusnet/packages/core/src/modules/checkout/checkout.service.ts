import type { SurplusItem } from '../../domain/types.js';
import type { Clock } from '../../lib/clock.js';
import { systemClock } from '../../lib/clock.js';
import {
  InvalidStateTransitionError,
  NotFoundError,
  ValidationError,
} from '../../lib/errors.js';
import type { SurplusItemRepository } from '../inventory/surplus-item.repository.js';
import type { WalletService } from '../wallet/wallet.service.js';
import type { CommunityFundService } from '../funding/community-fund.service.js';

/**
 * Phase-1 checkout (Epic 2, Open-Market Tier) with dignity parity:
 * a payment is any mix of cash and Community Credits (1 credit = 1 cent),
 * and nothing downstream can tell which mix was used — same endpoint, same
 * receipt, same claim flow.
 *
 * Where the money goes, per sale:
 *   - cash portion: a configurable share (default 20%) is contributed to
 *     the Community Fund — the open-market tier funds the free tier — and
 *     the rest is supplier COGS recovery;
 *   - credit portion: the fund pays it out to the supplier at settlement,
 *     retiring those credits from circulation.
 *
 * Ordering note: the wallet debit happens before the compare-and-set claim.
 * If the claim loses a race, debits are compensated with idempotent refund
 * credits keyed to the order — the production adapter runs the same steps
 * inside one SERIALIZABLE transaction and gets the rollback for free.
 */

export interface PurchaseInput {
  orderId: string;
  itemId: string;
  recipientId: string;
  cashCents: number;
  communityCredits: number;
}

export interface PurchaseReceipt {
  orderId: string;
  itemId: string;
  recipientId: string;
  totalCents: number;
  supplierProceedsCents: number;
  fundContributionCents: number;
  claimedAt: Date;
}

export interface CheckoutOptions {
  /** Share of the cash portion contributed to the Community Fund. */
  fundShareRate?: number;
}

export class CheckoutService {
  private readonly fundShareRate: number;

  constructor(
    private readonly items: SurplusItemRepository,
    private readonly wallets: WalletService,
    private readonly fund: CommunityFundService,
    options: CheckoutOptions = {},
    private readonly clock: Clock = systemClock,
  ) {
    const rate = options.fundShareRate ?? 0.2;
    if (rate < 0 || rate >= 1) {
      throw new ValidationError(`fundShareRate must be in [0, 1), got ${rate}`);
    }
    this.fundShareRate = rate;
  }

  async purchase(input: PurchaseInput): Promise<PurchaseReceipt> {
    const { orderId, itemId, recipientId, cashCents, communityCredits } = input;
    if (!orderId) throw new ValidationError('orderId is required');
    for (const [name, v] of [['cashCents', cashCents], ['communityCredits', communityCredits]] as const) {
      if (!Number.isSafeInteger(v) || v < 0) {
        throw new ValidationError(`${name} must be a non-negative integer, got ${v}`);
      }
    }

    const item = await this.requireItem(itemId);
    if (item.currentState !== 'SALES_PHASE') {
      throw new InvalidStateTransitionError(
        `item ${itemId} is in ${item.currentState}, not purchasable`,
      );
    }
    const price = item.salePriceCents ?? 0;
    if (cashCents + communityCredits !== price) {
      throw new ValidationError(
        `payment ${cashCents + communityCredits} does not match price ${price}`,
      );
    }

    // Debit both portions (each idempotent on the order id), then claim.
    if (cashCents > 0) {
      await this.wallets.debit(recipientId, 'CASH', cashCents, `order:${orderId}`, `order-${orderId}-cash`);
    }
    if (communityCredits > 0) {
      try {
        await this.wallets.debit(
          recipientId,
          'COMMUNITY_CREDIT',
          communityCredits,
          `order:${orderId}`,
          `order-${orderId}-credits`,
        );
      } catch (err) {
        if (cashCents > 0) await this.refund(recipientId, orderId, cashCents, 0);
        throw err;
      }
    }

    const claimedAt = this.clock.now();
    const claimed = await this.items.transitionState(itemId, 'SALES_PHASE', 'CLAIMED', {
      recipientId,
      salePriceCents: price,
    });
    if (!claimed) {
      await this.refund(recipientId, orderId, cashCents, communityCredits);
      throw new InvalidStateTransitionError(`item ${itemId} was claimed or rolled over first`);
    }

    const fundContributionCents = Math.floor(cashCents * this.fundShareRate);
    if (fundContributionCents > 0) {
      this.fund.contribute(`sale:${orderId}`, fundContributionCents, claimedAt);
    }
    if (communityCredits > 0) {
      this.fund.settleSpentCredits(communityCredits);
    }

    return {
      orderId,
      itemId,
      recipientId,
      totalCents: price,
      // Credits are paid out to the supplier by the fund at face value.
      supplierProceedsCents: cashCents - fundContributionCents + communityCredits,
      fundContributionCents,
      claimedAt,
    };
  }

  /** $0 claim from the donation feed — same claim flow, no payment step. */
  async claimDonation(itemId: string, recipientId: string): Promise<SurplusItem> {
    const item = await this.requireItem(itemId);
    if (item.currentState !== 'DONATION_PHASE') {
      throw new InvalidStateTransitionError(
        `item ${itemId} is in ${item.currentState}, not claimable from the donation feed`,
      );
    }
    const claimed = await this.items.transitionState(itemId, 'DONATION_PHASE', 'CLAIMED', {
      recipientId,
    });
    if (!claimed) {
      throw new InvalidStateTransitionError(`item ${itemId} was claimed first`);
    }
    return claimed;
  }

  private async requireItem(itemId: string): Promise<SurplusItem> {
    const item = await this.items.findById(itemId);
    if (!item) throw new NotFoundError(`no surplus item ${itemId}`);
    return item;
  }

  private async refund(
    recipientId: string,
    orderId: string,
    cashCents: number,
    communityCredits: number,
  ): Promise<void> {
    if (cashCents > 0) {
      await this.wallets.credit(recipientId, 'CASH', cashCents, `refund:${orderId}`, `refund-${orderId}-cash`);
    }
    if (communityCredits > 0) {
      await this.wallets.credit(
        recipientId,
        'COMMUNITY_CREDIT',
        communityCredits,
        `refund:${orderId}`,
        `refund-${orderId}-credits`,
      );
    }
  }
}
