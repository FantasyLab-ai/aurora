import { describe, expect, it } from 'vitest';
import { createSurplusNet, InMemoryWalletStore } from '../../index.js';

function makeNet() {
  return createSurplusNet();
}

describe('OnboardingService', () => {
  it('supplier: one call creates wallet, registers the zone, and activates the standing schedule', async () => {
    const net = makeNet();
    const result = await net.onboarding.onboardSupplier({
      userId: 'bakery-1',
      zoneId: 'downtown',
      firstSchedule: {
        scheduleId: 'bakery-1-close',
        title: 'Evening Bakery Box',
        category: 'bakery',
        quantity: 1,
        fmvCents: 2400,
        cogsCents: 900,
        latitude: 40.73,
        longitude: -73.99,
        listAtHourUtc: 21,
        safeForHours: 12,
      },
    });

    expect(result.walletCreated).toBe(true);
    expect(result.checklist.some((c) => c.includes('liability certificate'))).toBe(true);
    expect(result.checklist.some((c) => c.includes('nothing more to do'))).toBe(true);
    expect(net.recurring.schedulesOf('bakery-1')).toHaveLength(1);
    expect(net.zoneHealth.metrics('downtown').suppliers).toBe(1);
    expect(await net.wallets.balances('bakery-1')).toBeDefined();
  });

  it('courier: joins the team and counts toward zone density', async () => {
    const net = makeNet();
    net.teams.createTeam('acme', 'Acme Corp', 'downtown');

    const result = await net.onboarding.onboardCourier({
      userId: 'courier-1',
      zoneId: 'downtown',
      teamId: 'acme',
    });

    expect(result.walletCreated).toBe(true);
    expect(net.teams.teamOf('courier-1')?.teamId).toBe('acme');
    expect(net.zoneHealth.metrics('downtown').couriers).toBe(1);
  });

  it('recipient: dietary profile is live from the first feed', async () => {
    const net = makeNet();
    await net.onboarding.onboardRecipient({
      userId: 'neighbor-1',
      profile: { excludeCategories: ['meat'], avoidTags: ['contains-nuts'] },
    });

    expect(net.preferences.profileOf('neighbor-1')?.excludeCategories).toEqual(['meat']);
  });

  it('referral attribution pays the referrer when the new supplier first lists', async () => {
    const net = makeNet();
    const store = net.walletStore as InMemoryWalletStore;
    store.createWallet('courier-ref');

    await net.onboarding.onboardSupplier({
      userId: 'bakery-2',
      zoneId: 'downtown',
      referredBy: { referrerId: 'courier-ref', referrerRole: 'COURIER' },
    });
    expect((await net.wallets.balances('courier-ref')).karmaCreditBalance).toBe(0);

    await net.items.listSurplus({
      supplierId: 'bakery-2',
      title: 'First Box',
      category: 'bakery',
      fmvCents: 1000,
      cogsCents: 400,
      latitude: 40.7,
      longitude: -74.0,
      safeUntil: new Date(Date.now() + 4 * 3_600_000),
    });
    expect((await net.wallets.balances('courier-ref')).karmaCreditBalance).toBe(20);
  });

  it('re-onboarding an existing user never resets their wallet', async () => {
    const net = makeNet();
    const first = await net.onboarding.onboardCourier({ userId: 'c1', zoneId: 'z' });
    await net.wallets.credit('c1', 'KARMA_CREDIT', 50, 'test', 'k1');
    const second = await net.onboarding.onboardCourier({ userId: 'c1', zoneId: 'z' });

    expect(first.walletCreated).toBe(true);
    expect(second.walletCreated).toBe(false);
    expect((await net.wallets.balances('c1')).karmaCreditBalance).toBe(50);
  });
});
