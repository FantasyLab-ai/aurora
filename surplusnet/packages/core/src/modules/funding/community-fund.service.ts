import { InsufficientBalanceError, ValidationError } from '../../lib/errors.js';
import type { WalletService } from '../wallet/wallet.service.js';
import { DuplicateTransactionError } from '../../lib/errors.js';

/**
 * The self-sustaining Community Credit fund (Epic 2, Open-Market Tier).
 *
 * Every Community Credit in circulation is backed 1:1 (credit = cent) by
 * money actually in the pool, sourced from:
 *   - a cut of each Phase-1 cash sale (the open-market tier funding the
 *     free tier), and
 *   - municipal / non-profit / corporate grants.
 *
 * Invariant: outstandingCredits <= poolCents at all times.
 *   - Allocation mints credits only up to the unbacked headroom.
 *   - When credits are spent at checkout, the pool pays the supplier and
 *     outstanding shrinks by the same amount — the loop closes.
 *
 * This is what makes "free" food not-charity on both sides: recipients
 * spend credits like money, suppliers receive real money for them.
 */

export interface FundState {
  poolCents: number;
  outstandingCredits: number;
  /** Credits the pool can still back right now. */
  headroomCredits: number;
}

export interface ContributionRecord {
  source: string;
  amountCents: number;
  at: Date;
}

export interface AllocationResult {
  month: string;
  requestedPerRecipient: number;
  funded: string[];
  /** Recipients skipped because the pool ran out of headroom. */
  unfunded: string[];
  /** Recipients skipped because this month's allocation already reached them. */
  alreadyAllocated: string[];
}

export type ContributionListener = (record: ContributionRecord) => void;

export class CommunityFundService {
  private poolCents = 0;
  private outstandingCredits = 0;
  private readonly contributions: ContributionRecord[] = [];
  private readonly listeners: ContributionListener[] = [];

  constructor(private readonly wallets: WalletService) {}

  /** Observers (e.g. sponsor matching campaigns) are notified of every contribution. */
  onContribution(listener: ContributionListener): void {
    this.listeners.push(listener);
  }

  state(): FundState {
    return {
      poolCents: this.poolCents,
      outstandingCredits: this.outstandingCredits,
      headroomCredits: this.poolCents - this.outstandingCredits,
    };
  }

  /** Grants and the community-fund share of each cash sale land here. */
  contribute(source: string, amountCents: number, at: Date = new Date()): void {
    if (!Number.isSafeInteger(amountCents) || amountCents <= 0) {
      throw new ValidationError(`contribution must be a positive integer, got ${amountCents}`);
    }
    this.poolCents += amountCents;
    const record: ContributionRecord = { source, amountCents, at };
    this.contributions.push(record);
    for (const listener of this.listeners) listener(record);
  }

  contributionHistory(): ContributionRecord[] {
    return [...this.contributions];
  }

  /**
   * Monthly allocation run for enrolled recipients. Mints at most the pool's
   * headroom, first-come-first-served in the order given (callers pass a
   * need-ranked list). Idempotent per recipient per month: replaying the run
   * never double-allocates.
   */
  async allocateMonthly(
    month: string, // "2026-09"
    recipientIds: string[],
    creditsPerRecipient: number,
  ): Promise<AllocationResult> {
    if (!/^\d{4}-\d{2}$/.test(month)) {
      throw new ValidationError(`month must look like YYYY-MM, got ${month}`);
    }
    if (!Number.isSafeInteger(creditsPerRecipient) || creditsPerRecipient <= 0) {
      throw new ValidationError('creditsPerRecipient must be a positive integer');
    }

    const funded: string[] = [];
    const unfunded: string[] = [];
    const alreadyAllocated: string[] = [];

    for (const recipientId of recipientIds) {
      if (this.poolCents - this.outstandingCredits < creditsPerRecipient) {
        unfunded.push(recipientId);
        continue;
      }
      try {
        await this.wallets.credit(
          recipientId,
          'COMMUNITY_CREDIT',
          creditsPerRecipient,
          `community allocation ${month}`,
          `alloc-${month}-${recipientId}`,
        );
        this.outstandingCredits += creditsPerRecipient;
        funded.push(recipientId);
      } catch (err) {
        if (err instanceof DuplicateTransactionError) {
          alreadyAllocated.push(recipientId);
        } else {
          throw err;
        }
      }
    }

    return { month, requestedPerRecipient: creditsPerRecipient, funded, unfunded, alreadyAllocated };
  }

  /**
   * One-off backed mint (e.g. referral rewards): mints only when the pool
   * has headroom, so unbacked credits are never created. Returns false when
   * skipped for headroom or when the idempotency key already paid.
   */
  async tryMintBacked(
    recipientId: string,
    credits: number,
    reason: string,
    idempotencyKey: string,
  ): Promise<boolean> {
    if (!Number.isSafeInteger(credits) || credits <= 0) {
      throw new ValidationError('credits must be a positive integer');
    }
    if (this.poolCents - this.outstandingCredits < credits) return false;
    try {
      await this.wallets.credit(recipientId, 'COMMUNITY_CREDIT', credits, reason, idempotencyKey);
    } catch (err) {
      if (err instanceof DuplicateTransactionError) return false;
      throw err;
    }
    this.outstandingCredits += credits;
    return true;
  }

  /**
   * Called by checkout when Community Credits are spent: the pool pays out
   * the supplier's proceeds and the credits leave circulation.
   */
  settleSpentCredits(credits: number): void {
    if (!Number.isSafeInteger(credits) || credits <= 0) {
      throw new ValidationError(`settled credits must be a positive integer, got ${credits}`);
    }
    if (credits > this.outstandingCredits) {
      throw new InsufficientBalanceError(
        `settling ${credits} credits but only ${this.outstandingCredits} outstanding`,
      );
    }
    this.outstandingCredits -= credits;
    this.poolCents -= credits;
  }
}
