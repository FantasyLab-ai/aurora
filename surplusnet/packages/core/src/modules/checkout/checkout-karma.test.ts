import { describe, expect, it } from 'vitest';
import { CheckoutService } from './checkout.service.js';
import { CommunityFundService } from '../funding/community-fund.service.js';
import { SponsorshipService } from '../funding/sponsorship.service.js';
import { ImpactAccountingService } from '../impact/impact-accounting.service.js';
import { DonationLedger, InMemoryLedgerStore } from '../tax/donation-ledger.js';
import { InMemoryWalletStore, WalletService } from '../wallet/wallet.service.js';
import { InMemorySurplusItemRepository } from '../inventory/surplus-item.repository.js';
import { SurplusItemService } from '../inventory/surplus-item.service.js';
import { InsufficientBalanceError, ValidationError } from '../../lib/errors.js';

async function makeFixture(subsidyCents: number) {
  const repo = new InMemorySurplusItemRepository();
  const ledger = new DonationLedger(new InMemoryLedgerStore());
  const itemService = new SurplusItemService(repo, ledger);
  const store = new InMemoryWalletStore();
  const wallets = new WalletService(store);
  const fund = new CommunityFundService(wallets);
  const sponsorship = new SponsorshipService(fund, new ImpactAccountingService(ledger));
  sponsorship.registerSponsor({ sponsorId: 'acme', name: 'Acme' });
  if (subsidyCents > 0) sponsorship.fundKarmaSubsidy('acme', subsidyCents);

  // 1 karma = 10 cents at checkout
  const checkout = new CheckoutService(repo, wallets, fund, { karmaCentsRate: 10 }, undefined, sponsorship);

  const item = await itemService.listSurplus({
    supplierId: 'bakery-1',
    title: 'Bakery Box',
    category: 'bakery',
    fmvCents: 2000,
    cogsCents: 800,
    latitude: 40.7,
    longitude: -74.0,
    safeUntil: new Date(Date.now() + 4 * 3_600_000),
    salePriceCents: 500,
  });
  return { store, wallets, sponsorship, checkout, item };
}

describe('karma at checkout (role fluidity)', () => {
  it('a courier eats what they rescue: karma pays, the sponsor pool makes the supplier whole', async () => {
    const { store, wallets, sponsorship, checkout, item } = await makeFixture(1000);
    store.createWallet('courier-1', { karmaCreditBalance: 60, cashBalanceCents: 100 });

    const receipt = await checkout.purchase({
      orderId: 'o1',
      itemId: item.id,
      recipientId: 'courier-1',
      cashCents: 100,
      communityCredits: 0,
      karmaCredits: 40, // 400 cents
    });

    expect(receipt.supplierProceedsCents).toBe(100 - 20 + 400);
    expect(await wallets.balances('courier-1')).toMatchObject({
      cashBalanceCents: 0,
      karmaCreditBalance: 20,
    });
    expect(sponsorship.karmaSubsidyAvailableCents()).toBe(600);
  });

  it('rejects karma payment when the sponsor pool cannot back it, touching nothing', async () => {
    const { store, wallets, sponsorship, checkout, item } = await makeFixture(100);
    store.createWallet('courier-1', { karmaCreditBalance: 60 });

    await expect(
      checkout.purchase({
        orderId: 'o1',
        itemId: item.id,
        recipientId: 'courier-1',
        cashCents: 0,
        communityCredits: 0,
        karmaCredits: 50,
      }),
    ).rejects.toThrow(InsufficientBalanceError);

    expect((await wallets.balances('courier-1')).karmaCreditBalance).toBe(60);
    expect(sponsorship.karmaSubsidyAvailableCents()).toBe(100);
  });

  it('returns the reserved subsidy when the karma debit itself fails', async () => {
    const { store, sponsorship, checkout, item } = await makeFixture(1000);
    store.createWallet('courier-1', { karmaCreditBalance: 10 }); // not enough karma

    await expect(
      checkout.purchase({
        orderId: 'o1',
        itemId: item.id,
        recipientId: 'courier-1',
        cashCents: 0,
        communityCredits: 0,
        karmaCredits: 50,
      }),
    ).rejects.toThrow(InsufficientBalanceError);
    expect(sponsorship.karmaSubsidyAvailableCents()).toBe(1000);
  });

  it('rejects karma payments when no sponsorship service is wired', async () => {
    const repo = new InMemorySurplusItemRepository();
    const wallets = new WalletService(new InMemoryWalletStore());
    const fund = new CommunityFundService(wallets);
    const checkout = new CheckoutService(repo, wallets, fund);

    await expect(
      checkout.purchase({
        orderId: 'o1',
        itemId: 'any',
        recipientId: 'u1',
        cashCents: 0,
        communityCredits: 0,
        karmaCredits: 10,
      }),
    ).rejects.toThrow(ValidationError);
  });
});
