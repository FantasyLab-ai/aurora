import type { createSurplusNet, InMemoryCourierLocationSource, InMemoryWalletStore } from '@surplusnet/core';

export type SurplusNet = ReturnType<typeof createSurplusNet>;

export const ZONE = 'greenpoint';

export const HUBS = [
  { hubId: 'plant-arcade', name: 'SurplusNet Smart Locker · The Plant Arcade', latitude: 40.7267, longitude: -73.9524 },
  { hubId: 'mccarren-fridge', name: 'Community Fridge · McCarren Park', latitude: 40.7208, longitude: -73.9516 },
];

const CENTER = { latitude: 40.724, longitude: -73.951 };

export const DEMO = {
  recipient: 'demo-recipient',
  courier: 'demo-courier',
  supplier: 'daily-knead',
};

const CATALOG = [
  { title: 'Gourmet Bakery Assortment', category: 'bakery', supplier: 'daily-knead', fmv: 1430, cogs: 500, price: 429, tags: ['vegetarian'] },
  { title: 'Organic Produce Bundle', category: 'produce', supplier: 'verde-grocer', fmv: 1660, cogs: 620, price: 499, tags: ['vegan', 'gluten-free'] },
  { title: 'Artisan Salad Bowls (x3)', category: 'prepared', supplier: 'sweetleaf', fmv: 2400, cogs: 900, price: 720, tags: ['vegetarian'] },
  { title: 'Dairy & Yogurt Box', category: 'dairy', supplier: 'verde-grocer', fmv: 1200, cogs: 480, price: 360, tags: ['vegetarian', 'gluten-free'] },
];

export async function seed(net: SurplusNet): Promise<void> {
  const wallets = net.walletStore as InMemoryWalletStore;
  const locations = net.courierLocations as InMemoryCourierLocationSource;
  const now = new Date();
  const month = now.toISOString().slice(0, 7);

  // ── Sponsors fund the flywheel ──────────────────────────────────────────
  net.sponsorship.registerSponsor({ sponsorId: 'greenpoint-bank', name: 'Greenpoint Community Bank' });
  net.sponsorship.grant('greenpoint-bank', 250_00);
  net.sponsorship.fundKarmaSubsidy('greenpoint-bank', 50_00);
  net.sponsorship.createMatchCampaign({
    campaignId: `${month}-match`,
    sponsorId: 'greenpoint-bank',
    month,
    ratio: 1,
    capCents: 100_00,
    zoneId: ZONE,
  });

  // ── Karma partners ──────────────────────────────────────────────────────
  net.partners.registerPartner({ partnerId: 'corner-cafe', name: 'Corner Café', category: 'CAFE' });
  net.partners.registerPartner({ partnerId: 'city-transit', name: 'City Transit', category: 'TRANSIT' });
  net.partners.addPerk({ perkId: 'free-coffee', partnerId: 'corner-cafe', title: 'Free oat-milk latte', costKarma: 25, inventory: 40 });
  net.partners.addPerk({ perkId: 'transit-pass', partnerId: 'city-transit', title: '$5 transit credit', costKarma: 45, inventory: 20 });
  net.partners.addPerk({ perkId: 'grocer-voucher', partnerId: 'corner-cafe', title: '$10 grocery voucher', costKarma: 90, inventory: 10 });

  // ── Suppliers with standing schedules ───────────────────────────────────
  await net.onboarding.onboardSupplier({
    userId: DEMO.supplier,
    zoneId: ZONE,
    firstSchedule: {
      scheduleId: 'daily-knead-close',
      title: 'Evening Bakery Box',
      category: 'bakery',
      quantity: 1,
      fmvCents: 1430,
      cogsCents: 500,
      salePriceCents: 429,
      latitude: 40.7251,
      longitude: -73.9512,
      listAtHourUtc: 1, // 9pm ET
      safeForHours: 14,
      dietaryTags: ['vegetarian'],
      zoneId: ZONE,
    },
  });
  for (const supplierId of ['verde-grocer', 'sweetleaf', 'norte-deli', 'harvest-coop']) {
    await net.onboarding.onboardSupplier({ userId: supplierId, zoneId: ZONE });
  }

  // ── Hubs & courier network density ──────────────────────────────────────
  for (const hub of HUBS) net.zoneHealth.registerHub(ZONE, hub.hubId);
  net.teams.createTeam('greenpoint-riders', 'Greenpoint Riders', ZONE);
  net.teams.createTeam('acme-lunch-crew', 'Acme Lunch Crew', ZONE);

  const courierIds = [DEMO.courier, ...Array.from({ length: 31 }, (_, i) => `courier-${i + 1}`)];
  for (const [i, courierId] of courierIds.entries()) {
    await net.onboarding.onboardCourier({
      userId: courierId,
      zoneId: ZONE,
      teamId: i % 2 === 0 ? 'greenpoint-riders' : 'acme-lunch-crew',
    });
  }
  net.engagement.linkEmployer(DEMO.courier, 'acme-corp');
  await net.certifications.complete(DEMO.courier, 'food-handler-101');

  // Live positions for a handful of couriers near the zone center
  for (const [i, courierId] of courierIds.slice(0, 6).entries()) {
    locations.upsert({
      courierId,
      latitude: CENTER.latitude + (i - 3) * 0.004,
      longitude: CENTER.longitude + (i % 3) * 0.003,
      lastSeenAt: now,
      transport: i % 3 === 0 ? 'EBIKE' : i % 3 === 1 ? 'BIKE' : 'FOOT',
    });
  }

  // ── Recipients ──────────────────────────────────────────────────────────
  await net.onboarding.onboardRecipient({
    userId: DEMO.recipient,
    zoneId: ZONE,
    profile: { excludeCategories: [], avoidTags: [] },
  });
  await net.wallets.credit(DEMO.recipient, 'CASH', 20_00, 'demo top-up', 'seed-cash');
  for (const recipientId of ['neighbor-1', 'neighbor-2', 'neighbor-3']) {
    await net.onboarding.onboardRecipient({ userId: recipientId, zoneId: ZONE });
  }
  await net.fund.allocateMonthly(month, [DEMO.recipient, 'neighbor-1', 'neighbor-2'], 15_00);

  // ── History: completed rescues so impact, streaks, fill rate are real ───
  const historyCouriers = [DEMO.courier, DEMO.courier, DEMO.courier, 'courier-1', 'courier-1', 'courier-2', 'courier-3', 'courier-4', 'courier-5', 'courier-6'];
  for (const [i, courierId] of historyCouriers.entries()) {
    const spec = CATALOG[i % CATALOG.length]!;
    const item = await net.items.listSurplus({
      supplierId: spec.supplier,
      title: spec.title,
      category: spec.category,
      fmvCents: spec.fmv,
      cogsCents: spec.cogs,
      salePriceCents: spec.price,
      latitude: CENTER.latitude,
      longitude: CENTER.longitude,
      zoneId: ZONE,
      dietaryTags: spec.tags,
      safeUntil: new Date(now.getTime() + 6 * 3_600_000),
    });
    await net.itemRepository.transitionState(item.id, 'SALES_PHASE', 'DONATION_PHASE', {
      salePriceCents: 0,
      rolledOverAt: new Date(now.getTime() - (20 - i) * 60_000),
    });
    const hub = HUBS[i % HUBS.length]!;
    const delivery = await net.deliveries.accept({
      itemId: item.id,
      courierId,
      dropoffName: hub.name,
      dropoff: { latitude: hub.latitude, longitude: hub.longitude },
      karmaOnCompletion: 10 + (i % 3) * 4,
    });
    net.deliveries.pickUp(delivery.id);
    net.deliveries.recordTemp(delivery.id, 3.2 + (i % 4) * 0.3);
    await net.deliveries.dropOff(delivery.id);
  }
  // One expiry so the fill rate is honest (10 rescued / 1 expired ≈ 91%)
  const expiredItem = await net.items.listSurplus({
    supplierId: 'norte-deli',
    title: 'Deli Platter',
    category: 'prepared',
    fmvCents: 1800,
    cogsCents: 700,
    latitude: CENTER.latitude,
    longitude: CENTER.longitude,
    zoneId: ZONE,
    safeUntil: new Date(now.getTime() + 3_600_000),
  });
  await net.itemRepository.transitionState(expiredItem.id, 'SALES_PHASE', 'DONATION_PHASE', { salePriceCents: 0 });
  await net.itemRepository.transitionState(expiredItem.id, 'DONATION_PHASE', 'EXPIRED');
  await net.bus.emit('item.expired', { itemId: expiredItem.id });

  // ── Tonight's live feed ─────────────────────────────────────────────────
  for (const [i, spec] of CATALOG.entries()) {
    await net.items.listSurplus({
      supplierId: spec.supplier,
      title: spec.title,
      category: spec.category,
      fmvCents: spec.fmv,
      cogsCents: spec.cogs,
      salePriceCents: spec.price,
      latitude: CENTER.latitude + (i - 2) * 0.003,
      longitude: CENTER.longitude + (i % 2) * 0.004,
      zoneId: ZONE,
      dietaryTags: spec.tags,
      weightGrams: 2000 + i * 700,
      safeUntil: new Date(now.getTime() + 10 * 3_600_000),
      salesWindowMinutes: 45,
    });
  }
  // One box already in the free community pool, waiting for a courier
  const donationBox = await net.items.listSurplus({
    supplierId: DEMO.supplier,
    title: 'Fresh Bakery Assortment Box',
    category: 'bakery',
    fmvCents: 1430,
    cogsCents: 500,
    latitude: 40.7251,
    longitude: -73.9512,
    zoneId: ZONE,
    dietaryTags: ['vegetarian'],
    weightGrams: 2600,
    safeUntil: new Date(now.getTime() + 3 * 3_600_000),
  });
  await net.itemRepository.transitionState(donationBox.id, 'SALES_PHASE', 'DONATION_PHASE', {
    salePriceCents: 0,
    rolledOverAt: now,
  });

  void wallets;
}
