export * from './domain/types.js';
export * from './lib/errors.js';
export * from './lib/clock.js';
export * from './lib/event-bus.js';
export * from './modules/tax/tax-valuation.service.js';
export * from './modules/tax/donation-ledger.js';
export * from './modules/tax/audit-export.service.js';
export * from './modules/inventory/surplus-item.repository.js';
export * from './modules/inventory/surplus-item.service.js';
export * from './modules/inventory/phase-rollover.worker.js';
export * from './modules/routing/geo.js';
export * from './modules/routing/courier-dispatch.service.js';
export * from './modules/wallet/wallet.service.js';
export * from './modules/funding/community-fund.service.js';
export * from './modules/funding/sponsorship.service.js';
export * from './modules/checkout/checkout.service.js';
export * from './modules/karma/partner-redemption.service.js';
export * from './modules/karma/engagement.service.js';
export * from './modules/karma/karma-pricing.service.js';
export * from './modules/karma/team-competition.service.js';
export * from './modules/karma/certification.service.js';
export * from './modules/impact/impact-accounting.service.js';
export * from './modules/delivery/delivery.service.js';
export * from './modules/compliance/compliance.service.js';
export * from './modules/growth/referral.service.js';
export * from './modules/growth/zone-health.service.js';
export * from './modules/growth/onboarding.service.js';
export * from './modules/recipient/preferences.service.js';
export * from './modules/inventory/recurring-listing.service.js';
export * from './modules/routing/escalation.worker.js';

import { EventBus } from './lib/event-bus.js';
import type { Clock } from './lib/clock.js';
import { systemClock } from './lib/clock.js';
import { DonationLedger, InMemoryLedgerStore } from './modules/tax/donation-ledger.js';
import { AuditExportService } from './modules/tax/audit-export.service.js';
import {
  InMemorySurplusItemRepository,
  type SurplusItemRepository,
} from './modules/inventory/surplus-item.repository.js';
import { SurplusItemService } from './modules/inventory/surplus-item.service.js';
import { PhaseRolloverWorker } from './modules/inventory/phase-rollover.worker.js';
import {
  CourierDispatchService,
  InMemoryCourierLocationSource,
  type CourierLocationSource,
  type DispatchNotifier,
} from './modules/routing/courier-dispatch.service.js';
import { InMemoryWalletStore, WalletService, type WalletStore } from './modules/wallet/wallet.service.js';
import type { LedgerStore } from './modules/tax/donation-ledger.js';
import { CommunityFundService } from './modules/funding/community-fund.service.js';
import { SponsorshipService } from './modules/funding/sponsorship.service.js';
import { CheckoutService } from './modules/checkout/checkout.service.js';
import { PartnerRedemptionService } from './modules/karma/partner-redemption.service.js';
import { EngagementService } from './modules/karma/engagement.service.js';
import { KarmaPricingService } from './modules/karma/karma-pricing.service.js';
import { TeamCompetitionService } from './modules/karma/team-competition.service.js';
import { CertificationService } from './modules/karma/certification.service.js';
import { ImpactAccountingService } from './modules/impact/impact-accounting.service.js';
import { DeliveryService } from './modules/delivery/delivery.service.js';
import { ComplianceService } from './modules/compliance/compliance.service.js';
import { ReferralService } from './modules/growth/referral.service.js';
import { ZoneHealthService } from './modules/growth/zone-health.service.js';
import { OnboardingService } from './modules/growth/onboarding.service.js';
import { RecipientPreferencesService } from './modules/recipient/preferences.service.js';
import { RecurringListingService } from './modules/inventory/recurring-listing.service.js';
import { EscalationWorker } from './modules/routing/escalation.worker.js';

export interface SurplusNetOptions {
  clock?: Clock;
  ledgerStore?: LedgerStore;
  itemRepository?: SurplusItemRepository;
  walletStore?: WalletStore;
  courierLocations?: CourierLocationSource;
  notifier?: DispatchNotifier;
  radiusMiles?: number;
  rolloverPollIntervalMs?: number;
  karmaCreditsPerDelivery?: number;
  /** Share of each cash sale contributed to the Community Fund. */
  fundShareRate?: number;
  /** Verified volunteer minutes credited per completed delivery. */
  volunteerMinutesPerDelivery?: number;
}

/**
 * Wires the full pipeline with sensible defaults (in-memory adapters unless
 * production stores are injected):
 *
 *   supplier lists item ──► tax engine + immutable ledger
 *   rollover worker ──► DONATION_PHASE ──► `donation.available`
 *   `donation.available` ──► courier dispatch fan-out
 *   `delivery.completed` ──► karma credit mint (idempotent)
 */
export function createSurplusNet(options: SurplusNetOptions = {}) {
  const clock = options.clock ?? systemClock;
  const bus = new EventBus();

  const ledgerStore = options.ledgerStore ?? new InMemoryLedgerStore();
  const ledger = new DonationLedger(ledgerStore, clock);
  const auditExport = new AuditExportService(ledger, () => ledgerStore.all());

  const itemRepository = options.itemRepository ?? new InMemorySurplusItemRepository();
  const items = new SurplusItemService(itemRepository, ledger, clock, bus);
  const rolloverWorker = new PhaseRolloverWorker(
    itemRepository,
    bus,
    { ...(options.rolloverPollIntervalMs !== undefined
      ? { pollIntervalMs: options.rolloverPollIntervalMs }
      : {}) },
    clock,
  );

  const courierLocations = options.courierLocations ?? new InMemoryCourierLocationSource();
  const notifier: DispatchNotifier =
    options.notifier ?? { offerPickup: async () => undefined };
  const dispatch = new CourierDispatchService(
    courierLocations,
    notifier,
    { ...(options.radiusMiles !== undefined ? { radiusMiles: options.radiusMiles } : {}) },
    clock,
  );

  const walletStore = options.walletStore ?? new InMemoryWalletStore();
  const wallets = new WalletService(walletStore);

  const impact = new ImpactAccountingService(ledger);
  const fund = new CommunityFundService(wallets);
  const sponsorship = new SponsorshipService(fund, impact);
  const checkout = new CheckoutService(
    itemRepository,
    wallets,
    fund,
    { ...(options.fundShareRate !== undefined ? { fundShareRate: options.fundShareRate } : {}) },
    clock,
    sponsorship,
  );
  const partners = new PartnerRedemptionService(wallets);
  const engagement = new EngagementService(
    { ...(options.volunteerMinutesPerDelivery !== undefined
      ? { minutesPerDelivery: options.volunteerMinutesPerDelivery }
      : {}) },
    clock,
  );
  const teams = new TeamCompetitionService();
  const certifications = new CertificationService(wallets, engagement);
  const referrals = new ReferralService(wallets, {
    tryMint: (recipientId, credits, reason, key) => fund.tryMintBacked(recipientId, credits, reason, key),
  });
  const preferences = new RecipientPreferencesService();

  const karmaPerDelivery = options.karmaCreditsPerDelivery ?? 10;
  const karmaPricing = new KarmaPricingService(karmaPerDelivery);
  const deliveries = new DeliveryService(itemRepository, bus, {}, clock);
  const compliance = new ComplianceService(itemRepository, deliveries, impact);

  const zoneHealth = new ZoneHealthService(undefined, clock);
  const recurring = new RecurringListingService(items, clock);
  const escalation = new EscalationWorker(
    itemRepository,
    dispatch,
    bus,
    { ...(options.radiusMiles !== undefined ? { baseRadiusMiles: options.radiusMiles } : {}) },
    clock,
  );
  const onboarding = new OnboardingService(
    walletStore,
    zoneHealth,
    referrals,
    teams,
    preferences,
    recurring,
  );

  bus.on('item.listed', async ({ supplierId, zoneId }) => {
    if (zoneId) zoneHealth.recordListing(zoneId);
    // A supplier's first listing qualifies whoever referred them.
    await referrals.qualify(supplierId, clock.now());
  });

  bus.on('donation.available', async ({ itemId, latitude, longitude }) => {
    await dispatch.dispatchForItem(itemId, { latitude, longitude });
  });

  bus.on('item.expired', async ({ itemId }) => {
    const item = await itemRepository.findById(itemId);
    if (item?.zoneId) zoneHealth.recordExpiry(item.zoneId);
  });

  bus.on('delivery.completed', async ({ deliveryId, itemId, courierId, karmaCredits }) => {
    // Idempotent mint: only the first event for a delivery pays karma,
    // counts toward streaks/badges/teams, logs volunteer minutes, books
    // the rescue's impact, and feeds zone health.
    const minted = await wallets.mintKarmaForDelivery(
      courierId,
      deliveryId,
      karmaCredits ?? karmaPerDelivery,
    );
    if (minted) {
      const now = clock.now();
      engagement.recordDelivery(courierId, deliveryId, now);
      teams.recordDelivery(courierId, now);
      await ledger.record(itemId, 'DELIVERY_VERIFIED', { deliveryId, courierId });
      await referrals.qualify(courierId, now);
      const item = await itemRepository.findById(itemId);
      if (item) {
        await impact.recordRescue(item, now);
        if (item.zoneId) {
          const enteredAt = item.rolledOverAt ?? item.listedAt;
          zoneHealth.recordRescue(
            item.zoneId,
            Math.max(0, Math.round((now.getTime() - enteredAt.getTime()) / 60_000)),
          );
        }
      }
    }
  });

  return {
    bus,
    ledger,
    auditExport,
    items,
    itemRepository,
    rolloverWorker,
    dispatch,
    wallets,
    walletStore,
    courierLocations,
    fund,
    checkout,
    partners,
    engagement,
    impact,
    sponsorship,
    teams,
    certifications,
    referrals,
    preferences,
    karmaPricing,
    deliveries,
    compliance,
    zoneHealth,
    recurring,
    escalation,
    onboarding,
  };
}
