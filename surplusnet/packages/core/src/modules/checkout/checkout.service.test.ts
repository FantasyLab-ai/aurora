import { describe, expect, it } from 'vitest';
import { CheckoutService } from './checkout.service.js';
import { CommunityFundService } from '../funding/community-fund.service.js';
import { InMemoryWalletStore, WalletService } from '../wallet/wallet.service.js';
import { InMemorySurplusItemRepository } from '../inventory/surplus-item.repository.js';
import { SurplusItemService } from '../inventory/surplus-item.service.js';
import { DonationLedger, InMemoryLedgerStore } from '../tax/donation-ledger.js';
import {
  InsufficientBalanceError,
  InvalidStateTransitionError,
  ValidationError,
} from '../../lib/errors.js';

async function makeFixture() {
  const repo = new InMemorySurplusItemRepository();
  const itemService = new SurplusItemService(repo, new DonationLedger(new InMemoryLedgerStore()));
  const store = new InMemoryWalletStore();
  const wallets = new WalletService(store);
  const fund = new CommunityFundService(wallets);
  const checkout = new CheckoutService(repo, wallets, fund, { fundShareRate: 0.2 });

  const item = await itemService.listSurplus({
    supplierId: 'grocer-1',
    title: 'Organic Produce Bundle',
    category: 'produce',
    fmvCents: 2000,
    cogsCents: 800,
    latitude: 40.7,
    longitude: -74.0,
    safeUntil: new Date(Date.now() + 4 * 3_600_000),
    salePriceCents: 1000,
  });

  return { repo, store, wallets, fund, checkout, item };
}

describe('CheckoutService.purchase', () => {
  it('splits an all-cash sale between supplier proceeds and the community fund', async () => {
    const { checkout, item, store, repo } = await makeFixture();
    store.createWallet('buyer', { cashBalanceCents: 1500 });

    const receipt = await checkout.purchase({
      orderId: 'o1',
      itemId: item.id,
      recipientId: 'buyer',
      cashCents: 1000,
      communityCredits: 0,
    });

    expect(receipt.fundContributionCents).toBe(200);
    expect(receipt.supplierProceedsCents).toBe(800);
    expect((await repo.findById(item.id))?.currentState).toBe('CLAIMED');
    expect((await repo.findById(item.id))?.recipientId).toBe('buyer');
  });

  it('treats a mixed cash + credit payment identically at the same total', async () => {
    const { checkout, item, store, fund, wallets } = await makeFixture();
    store.createWallet('buyer', { cashBalanceCents: 400 });
    fund.contribute('grant:city', 1000);
    await fund.allocateMonthly('2026-09', ['buyer'], 600);

    const receipt = await checkout.purchase({
      orderId: 'o1',
      itemId: item.id,
      recipientId: 'buyer',
      cashCents: 400,
      communityCredits: 600,
    });

    // 20% of the cash portion funds the pool; spent credits are paid out
    // to the supplier by the fund and retired.
    expect(receipt.fundContributionCents).toBe(80);
    expect(receipt.supplierProceedsCents).toBe(400 - 80 + 600);
    expect(await wallets.balances('buyer')).toMatchObject({
      cashBalanceCents: 0,
      communityCreditBalance: 0,
    });
    expect(fund.state().outstandingCredits).toBe(0);
    expect(fund.state().poolCents).toBe(1000 + 80 - 600);
  });

  it('rejects a payment that does not match the price', async () => {
    const { checkout, item, store } = await makeFixture();
    store.createWallet('buyer', { cashBalanceCents: 5000 });
    await expect(
      checkout.purchase({ orderId: 'o1', itemId: item.id, recipientId: 'buyer', cashCents: 999, communityCredits: 0 }),
    ).rejects.toThrow(ValidationError);
  });

  it('refunds the cash debit when the credit debit fails mid-payment', async () => {
    const { checkout, item, store, wallets } = await makeFixture();
    store.createWallet('buyer', { cashBalanceCents: 400, communityCreditBalance: 100 });

    await expect(
      checkout.purchase({ orderId: 'o1', itemId: item.id, recipientId: 'buyer', cashCents: 400, communityCredits: 600 }),
    ).rejects.toThrow(InsufficientBalanceError);

    expect(await wallets.balances('buyer')).toMatchObject({
      cashBalanceCents: 400,
      communityCreditBalance: 100,
    });
  });

  it('refunds in full when another buyer claims the item first', async () => {
    const { checkout, item, store, wallets } = await makeFixture();
    store.createWallet('fast', { cashBalanceCents: 1000 });
    store.createWallet('slow', { cashBalanceCents: 1000 });

    await checkout.purchase({ orderId: 'o1', itemId: item.id, recipientId: 'fast', cashCents: 1000, communityCredits: 0 });
    await expect(
      checkout.purchase({ orderId: 'o2', itemId: item.id, recipientId: 'slow', cashCents: 1000, communityCredits: 0 }),
    ).rejects.toThrow(InvalidStateTransitionError);

    expect((await wallets.balances('slow')).cashBalanceCents).toBe(1000);
  });
});

describe('CheckoutService.claimDonation', () => {
  it('claims a donation-phase item with no payment, exactly once', async () => {
    const { checkout, item, repo } = await makeFixture();
    await repo.transitionState(item.id, 'SALES_PHASE', 'DONATION_PHASE', { salePriceCents: 0 });

    const claimed = await checkout.claimDonation(item.id, 'pantry-1');
    expect(claimed.currentState).toBe('CLAIMED');
    expect(claimed.recipientId).toBe('pantry-1');

    await expect(checkout.claimDonation(item.id, 'pantry-2')).rejects.toThrow(InvalidStateTransitionError);
  });

  it('refuses to claim an item still in the paid sales phase', async () => {
    const { checkout, item } = await makeFixture();
    await expect(checkout.claimDonation(item.id, 'pantry-1')).rejects.toThrow(InvalidStateTransitionError);
  });
});
