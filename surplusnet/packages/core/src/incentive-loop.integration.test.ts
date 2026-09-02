import { describe, expect, it } from 'vitest';
import { createSurplusNet, InMemoryWalletStore } from './index.js';

/**
 * The closed incentive loop, every party paid in its own currency:
 *   supplier  → tax deduction + COGS recovery (cash sales AND spent credits)
 *   consumer  → 50-70% off, funds the free tier with every purchase
 *   recipient → credits that spend exactly like cash
 *   courier   → karma → real local perks, plus badges and volunteer hours
 */
describe('SurplusNet incentive loop', () => {
  it('cash sales fund credits, credits pay suppliers, deliveries pay couriers in karma', async () => {
    const net = createSurplusNet({ fundShareRate: 0.2 });
    const store = net.walletStore as InMemoryWalletStore;
    store.createWallet('student', { cashBalanceCents: 2000 });
    store.createWallet('neighbor');
    store.createWallet('courier-1');

    const listItem = (title: string, salePriceCents: number) =>
      net.items.listSurplus({
        supplierId: 'bakery-9',
        title,
        category: 'bakery',
        fmvCents: 3000,
        cogsCents: 1000,
        latitude: 40.7359,
        longitude: -73.9911,
        safeUntil: new Date(Date.now() + 6 * 3_600_000),
        salePriceCents,
      });

    // 1. The city seeds the fund; a cash buyer keeps feeding it.
    net.fund.contribute('grant:city', 500);
    const boxA = await listItem('Bakery Box A', 1000);
    const saleA = await net.checkout.purchase({
      orderId: 'oA',
      itemId: boxA.id,
      recipientId: 'student',
      cashCents: 1000,
      communityCredits: 0,
    });
    expect(saleA.fundContributionCents).toBe(200);
    expect(net.fund.state().poolCents).toBe(700);

    // 2. Monthly allocation: the neighbor gets credits backed by that pool.
    const allocation = await net.fund.allocateMonthly('2026-09', ['neighbor'], 600);
    expect(allocation.funded).toEqual(['neighbor']);

    // 3. The neighbor spends credits like cash; the fund pays the supplier.
    const boxB = await listItem('Bakery Box B', 600);
    const saleB = await net.checkout.purchase({
      orderId: 'oB',
      itemId: boxB.id,
      recipientId: 'neighbor',
      cashCents: 0,
      communityCredits: 600,
    });
    expect(saleB.supplierProceedsCents).toBe(600);
    expect(net.fund.state()).toMatchObject({ outstandingCredits: 0, poolCents: 100 });

    // 4. A delivery mints karma once, earns the first badge, and the courier
    //    turns karma into a real-world perk.
    net.partners.registerPartner({ partnerId: 'cafe-1', name: 'Corner Cafe', category: 'CAFE' });
    net.partners.addPerk({ perkId: 'coffee', partnerId: 'cafe-1', title: 'Free coffee', costKarma: 10, inventory: 5 });

    const boxC = await listItem('Bakery Box C', 800);
    await net.bus.emit('delivery.completed', { deliveryId: 'd1', itemId: boxC.id, courierId: 'courier-1' });
    await net.bus.emit('delivery.completed', { deliveryId: 'd1', itemId: boxC.id, courierId: 'courier-1' });

    expect(net.engagement.engagement('courier-1')).toMatchObject({
      totalDeliveries: 1,
      badges: ['first-rescue'],
    });
    const voucher = await net.partners.redeem('courier-1', 'coffee', 'r1');
    expect(voucher.partnerId).toBe('cafe-1');
    expect((await net.wallets.balances('courier-1')).karmaCreditBalance).toBe(0);
    expect(net.engagement.leaderboard()[0]?.courierId).toBe('courier-1');
  });
});
