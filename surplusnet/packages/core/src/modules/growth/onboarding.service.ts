import type { UserRole } from '../../domain/types.js';
import { ValidationError } from '../../lib/errors.js';
import type { WalletStore } from '../wallet/wallet.service.js';
import type { ZoneHealthService } from './zone-health.service.js';
import type { ReferralService } from './referral.service.js';
import type { TeamCompetitionService } from '../karma/team-competition.service.js';
import type { RecipientPreferencesService, RecipientProfile } from '../recipient/preferences.service.js';
import type { RecurringListingService, RecurringScheduleInput } from '../inventory/recurring-listing.service.js';

/**
 * One-call onboarding — every party productive in a single interaction,
 * because each extra setup step is where a marketplace loses its supply.
 *
 * - Supplier: wallet + zone registration + (crucially) their first standing
 *   surplus schedule, so they are DONE after signup — no daily action ever
 *   required again. The checklist leads with the two fear-killers: the
 *   liability certificate and the tax/compliance report they'll receive.
 * - Courier: wallet + zone + optional team, pointed at the food-handler
 *   micro-cert (bonus karma + the training half of the liability story).
 * - Recipient: wallet + dietary profile up front, so their first feed is
 *   already edible-for-them. Credit enrollment routes through partners.
 *
 * Referral attribution is registered here (rewards pay only on the new
 * user's first real action, wired elsewhere). Idempotent: onboarding an
 * existing user again never resets their wallet.
 */

export interface OnboardingResult {
  userId: string;
  role: UserRole;
  walletCreated: boolean;
  checklist: string[];
}

export class OnboardingService {
  constructor(
    private readonly wallets: WalletStore,
    private readonly zones: ZoneHealthService,
    private readonly referrals: ReferralService,
    private readonly teams: TeamCompetitionService,
    private readonly preferences: RecipientPreferencesService,
    private readonly recurring: RecurringListingService,
  ) {}

  async onboardSupplier(input: {
    userId: string;
    zoneId: string;
    referredBy?: { referrerId: string; referrerRole: UserRole };
    firstSchedule?: Omit<RecurringScheduleInput, 'supplierId'>;
  }): Promise<OnboardingResult> {
    const walletCreated = await this.ensureWallet(input.userId);
    this.zones.registerSupplier(input.zoneId, input.userId);
    this.applyReferral(input.userId, input.referredBy);

    const checklist = [
      'Every donation ships with a custody-documented liability certificate (Bill Emerson Act)',
      'Tax deduction is calculated instantly per item; the audit-ready report arrives monthly',
    ];
    if (input.firstSchedule) {
      this.recurring.addSchedule({ ...input.firstSchedule, supplierId: input.userId });
      checklist.push('Standing surplus schedule active — nothing more to do at close');
    } else {
      checklist.push('Set a standing schedule so listing happens automatically at close');
    }

    return { userId: input.userId, role: 'SUPPLIER', walletCreated, checklist };
  }

  async onboardCourier(input: {
    userId: string;
    zoneId: string;
    teamId?: string;
    referredBy?: { referrerId: string; referrerRole: UserRole };
  }): Promise<OnboardingResult> {
    const walletCreated = await this.ensureWallet(input.userId);
    this.zones.registerCourier(input.zoneId, input.userId);
    this.applyReferral(input.userId, input.referredBy);

    const checklist = [
      'Complete the 10-minute food-handler course for bonus karma and your first badge',
      'Rainy nights and closing safety windows pay up to 3x karma',
    ];
    if (input.teamId) {
      this.teams.join(input.userId, input.teamId);
      checklist.push('Your rescues now count for your team on the zone leaderboard');
    }

    return { userId: input.userId, role: 'COURIER', walletCreated, checklist };
  }

  async onboardRecipient(input: {
    userId: string;
    zoneId?: string;
    profile?: Omit<RecipientProfile, 'userId'>;
    referredBy?: { referrerId: string; referrerRole: UserRole };
  }): Promise<OnboardingResult> {
    const walletCreated = await this.ensureWallet(input.userId);
    this.applyReferral(input.userId, input.referredBy);
    if (input.profile) {
      this.preferences.setProfile({ userId: input.userId, ...input.profile });
    }

    return {
      userId: input.userId,
      role: 'RECIPIENT',
      walletCreated,
      checklist: [
        'Your feed only shows boxes that fit your dietary profile',
        'Cash, community credits, and karma all spend the same way at checkout',
      ],
    };
  }

  private async ensureWallet(userId: string): Promise<boolean> {
    if (!userId) throw new ValidationError('userId is required');
    const existing = await this.wallets.findByUserId(userId);
    if (existing) return false;
    await this.wallets.createWallet(userId);
    return true;
  }

  private applyReferral(
    newUserId: string,
    referredBy?: { referrerId: string; referrerRole: UserRole },
  ): void {
    if (!referredBy) return;
    // A repeat onboarding of an already-referred user should not fail the call.
    if (this.referrals.referralOf(newUserId)) return;
    this.referrals.register(referredBy.referrerId, referredBy.referrerRole, newUserId);
  }
}
