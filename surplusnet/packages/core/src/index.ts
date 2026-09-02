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

  const karmaPerDelivery = options.karmaCreditsPerDelivery ?? 10;

  bus.on('donation.available', async ({ itemId, latitude, longitude }) => {
    await dispatch.dispatchForItem(itemId, { latitude, longitude });
  });

  bus.on('delivery.completed', async ({ deliveryId, itemId, courierId }) => {
    await wallets.mintKarmaForDelivery(courierId, deliveryId, karmaPerDelivery);
    await ledger.record(itemId, 'DELIVERY_VERIFIED', { deliveryId, courierId });
  });

  return { bus, ledger, auditExport, items, itemRepository, rolloverWorker, dispatch, wallets, walletStore, courierLocations };
}
