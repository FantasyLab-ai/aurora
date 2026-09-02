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
export * from './modules/checkout/checkout.service.js';
export * from './modules/karma/partner-redemption.service.js';
export * from './modules/karma/engagement.service.js';

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
import { CheckoutService } from './modules/checkout/checkout.service.js';
import { PartnerRedemptionService } from './modules/karma/partner-redemption.service.js';
import { EngagementService } from './modules/karma/engagement.service.js';

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
  const items = new SurplusItemService(itemRepository, ledger, clock);
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

  const fund = new CommunityFundService(wallets);
  const checkout = new CheckoutService(
    itemRepository,
    wallets,
    fund,
    { ...(options.fundShareRate !== undefined ? { fundShareRate: options.fundShareRate } : {}) },
    clock,
  );
  const partners = new PartnerRedemptionService(wallets);
  const engagement = new EngagementService(
    { ...(options.volunteerMinutesPerDelivery !== undefined
      ? { minutesPerDelivery: options.volunteerMinutesPerDelivery }
      : {}) },
    clock,
  );

  const karmaPerDelivery = options.karmaCreditsPerDelivery ?? 10;

  bus.on('donation.available', async ({ itemId, latitude, longitude }) => {
    await dispatch.dispatchForItem(itemId, { latitude, longitude });
  });

  bus.on('delivery.completed', async ({ deliveryId, itemId, courierId }) => {
    // Idempotent mint: only the first event for a delivery pays karma,
    // counts toward streaks/badges, and logs volunteer minutes.
    const minted = await wallets.mintKarmaForDelivery(courierId, deliveryId, karmaPerDelivery);
    if (minted) {
      engagement.recordDelivery(courierId, deliveryId, clock.now());
      await ledger.record(itemId, 'DELIVERY_VERIFIED', { deliveryId, courierId });
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
  };
}
