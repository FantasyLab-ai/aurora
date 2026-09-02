import type { UserRole } from '../../domain/types.js';
import { DuplicateTransactionError, ValidationError } from '../../lib/errors.js';
import type { WalletService } from '../wallet/wallet.service.js';

/**
 * Multi-sided referral engine — the flywheel's self-propulsion. Every party
 * recruiting *any* side earns in their own currency, and rewards pay out
 * only when the new user completes their first real action (first listing,
 * first rescue, first purchase), never at signup — so the loop can't be
 * farmed with empty accounts.
 *
 * Reward currency by referrer role:
 *   COURIER   → Karma Credits (spendable on perks or food)
 *   RECIPIENT → Community Credits, minted only if the fund has headroom
 *               (unbacked credits are never created; the reward is simply
 *               skipped and reported when the pool is dry)
 *   SUPPLIER  → featured placement (their boxes surface first) — suppliers'
 *               financial reward already scales with their own volume
 */

export interface ReferralRewardPolicy {
  courierKarma: number;
  recipientCredits: number;
  /** Days of featured placement for supplier referrers. */
  supplierFeaturedDays: number;
}

export const DEFAULT_REFERRAL_POLICY: ReferralRewardPolicy = {
  courierKarma: 20,
  recipientCredits: 300,
  supplierFeaturedDays: 7,
};

export interface Referral {
  referrerId: string;
  referrerRole: UserRole;
  newUserId: string;
  qualified: boolean;
  reward?: string;
}

export interface CreditMinter {
  /** Mints backed Community Credits; returns false when the pool lacks headroom. */
  tryMint(recipientId: string, credits: number, reason: string, idempotencyKey: string): Promise<boolean>;
}

export class ReferralService {
  private referrals = new Map<string, Referral>();
  private featuredUntil = new Map<string, Date>();

  constructor(
    private readonly wallets: WalletService,
    private readonly creditMinter: CreditMinter,
    private readonly policy: ReferralRewardPolicy = DEFAULT_REFERRAL_POLICY,
  ) {}

  /** Recorded at signup via the referral link; no reward yet. */
  register(referrerId: string, referrerRole: UserRole, newUserId: string): void {
    if (referrerId === newUserId) throw new ValidationError('self-referral is not allowed');
    if (this.referrals.has(newUserId)) {
      throw new ValidationError(`user ${newUserId} already has a referrer`);
    }
    this.referrals.set(newUserId, { referrerId, referrerRole, newUserId, qualified: false });
  }

  /**
   * Called on the new user's first meaningful action. Idempotent: only the
   * first qualification pays. Returns the reward description, or undefined
   * when there is no referral / it already paid.
   */
  async qualify(newUserId: string, now: Date = new Date()): Promise<string | undefined> {
    const referral = this.referrals.get(newUserId);
    if (!referral || referral.qualified) return undefined;
    referral.qualified = true;

    switch (referral.referrerRole) {
      case 'COURIER': {
        try {
          await this.wallets.credit(
            referral.referrerId,
            'KARMA_CREDIT',
            this.policy.courierKarma,
            `referral:${newUserId}`,
            `referral-${newUserId}`,
          );
        } catch (err) {
          if (!(err instanceof DuplicateTransactionError)) throw err;
        }
        referral.reward = `${this.policy.courierKarma} karma`;
        break;
      }
      case 'RECIPIENT': {
        const minted = await this.creditMinter.tryMint(
          referral.referrerId,
          this.policy.recipientCredits,
          `referral:${newUserId}`,
          `referral-${newUserId}`,
        );
        referral.reward = minted
          ? `${this.policy.recipientCredits} community credits`
          : 'skipped: community fund lacks headroom';
        break;
      }
      case 'SUPPLIER': {
        const current = this.featuredUntil.get(referral.referrerId);
        const base = current && current.getTime() > now.getTime() ? current : now;
        const until = new Date(base.getTime() + this.policy.supplierFeaturedDays * 86_400_000);
        this.featuredUntil.set(referral.referrerId, until);
        referral.reward = `featured until ${until.toISOString()}`;
        break;
      }
    }
    return referral.reward;
  }

  isFeatured(supplierId: string, now: Date = new Date()): boolean {
    const until = this.featuredUntil.get(supplierId);
    return until !== undefined && until.getTime() > now.getTime();
  }

  referralOf(newUserId: string): Referral | undefined {
    const r = this.referrals.get(newUserId);
    return r ? { ...r } : undefined;
  }
}
