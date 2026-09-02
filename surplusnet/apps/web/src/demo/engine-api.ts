import {
  createSurplusNet,
  haversineMeters,
  type InMemoryCourierLocationSource,
  type SurplusItem,
  type WeatherCondition,
} from '@surplusnet/core';
import { seed, HUBS, ZONE } from '../../../api/src/seed';
import type { ApiClient, Balances, Delivery, FeedItem } from '../api';

/**
 * The self-contained demo backend: the real @surplusnet/core engine running
 * in the page (ledger hashing, rollover timers, surge pricing, wallets and
 * all), seeded with the same demo world the API server uses. Every method
 * mirrors the REST layer's response shapes so the UI cannot tell the
 * difference.
 */

const net = createSurplusNet({ fundShareRate: 0.2 });
const ready = seed(net).then(() => {
  setInterval(() => {
    void net.rolloverWorker.tick().catch(() => undefined);
    void net.escalation.tick().catch(() => undefined);
    void net.recurring.tick().catch(() => undefined);
  }, 10_000);
});

const locations = net.courierLocations as InMemoryCourierLocationSource;
const WEATHER: WeatherCondition = 'RAIN';
const ME = { latitude: 40.724, longitude: -73.951 };

function minutesLeft(until: Date, from = new Date()): number {
  return Math.max(0, Math.round((until.getTime() - from.getTime()) / 60_000));
}

function serializeItem(item: SurplusItem): FeedItem {
  const now = new Date();
  const salesEndsAt = new Date(item.listedAt.getTime() + item.salesWindowMinutes * 60_000);
  return {
    id: item.id,
    title: item.title,
    category: item.category,
    supplierId: item.supplierId,
    dietaryTags: item.dietaryTags ?? [],
    state: item.currentState,
    priceCents: item.salePriceCents ?? 0,
    fmvCents: item.fmvCents,
    taxDeductionCents: item.calculatedTaxDeductionCents,
    minutesLeftInSale: item.currentState === 'SALES_PHASE' ? minutesLeft(salesEndsAt, now) : 0,
    minutesUntilUnsafe: minutesLeft(item.safeUntil, now),
    impact: net.impact.computeForItem(item),
    latitude: item.latitude,
    longitude: item.longitude,
  };
}

function serializeDelivery(delivery: NonNullable<ReturnType<typeof net.deliveries.byId>>): Delivery {
  return {
    id: delivery.id,
    itemId: delivery.itemId,
    courierId: delivery.courierId,
    state: delivery.state,
    dropoffName: delivery.dropoffName,
    dropoff: { latitude: delivery.dropoff.latitude, longitude: delivery.dropoff.longitude },
    karmaOnCompletion: delivery.karmaOnCompletion,
    tempReadings: delivery.tempReadings.map((r) => ({ at: r.at.toISOString(), celsius: r.celsius })),
    coldChainCompliant: delivery.coldChainCompliant,
  };
}

async function balances(userId: string): Promise<Balances> {
  return net.wallets.balances(userId);
}

export const engineApi: ApiClient = {
  async feed(userId) {
    await ready;
    const sales = await net.itemRepository.findInState('SALES_PHASE');
    const donations = await net.itemRepository.findInState('DONATION_PHASE');
    const visible = net.preferences.filterFeed(userId, [...sales, ...donations]);
    return {
      fund: net.fund.state(),
      items: visible.sort((a, b) => a.safeUntil.getTime() - b.safeUntil.getTime()).map(serializeItem),
    };
  },

  async purchase(input) {
    await ready;
    const receipt = await net.checkout.purchase({ orderId: globalThis.crypto.randomUUID(), ...input });
    return { receipt, wallet: await balances(input.recipientId) };
  },

  async claimDonation(itemId, recipientId) {
    await ready;
    return { item: serializeItem(await net.checkout.claimDonation(itemId, recipientId)) };
  },

  async claims(userId) {
    await ready;
    const items = await net.itemRepository.findByRecipient(userId);
    return {
      claims: items.map((item) => {
        const delivery = net.deliveries.forItem(item.id);
        return { ...serializeItem(item), delivery: delivery ? serializeDelivery(delivery) : null };
      }),
    };
  },

  async wallet(userId) {
    await ready;
    return { balances: await balances(userId) };
  },

  async courierLocation(courierId, latitude, longitude) {
    await ready;
    locations.upsert({
      courierId,
      latitude,
      longitude,
      lastSeenAt: new Date(),
      transport: locations.get(courierId)?.transport ?? 'BIKE',
    });
    return { ok: true as const };
  },

  async offers(courierId) {
    await ready;
    const open = await net.itemRepository.findInState('DONATION_PHASE');
    const me = locations.get(courierId) ?? ME;
    const activeCouriers = (await locations.activeCouriersNear(me, 5000)).length;
    const now = new Date();
    return {
      offers: open.map((item) => {
        const distanceMeters = Math.round(haversineMeters(me, item));
        const quote = net.karmaPricing.quote({
          minutesUntilUnsafe: minutesLeft(item.safeUntil, now),
          distanceMeters,
          weather: WEATHER,
          hourOfDay: now.getUTCHours(),
          activeCouriers,
          openRescues: open.length,
        });
        const hub = HUBS.reduce((a, b) => (haversineMeters(item, a) <= haversineMeters(item, b) ? a : b));
        return { item: serializeItem(item), distanceMeters, quote, hub, weather: WEATHER };
      }),
    };
  },

  async accept(input) {
    await ready;
    const hub = HUBS.find((h) => h.hubId === input.hubId) ?? HUBS[0]!;
    const delivery = await net.deliveries.accept({
      itemId: input.itemId,
      courierId: input.courierId,
      dropoffName: hub.name,
      dropoff: { latitude: hub.latitude, longitude: hub.longitude },
      karmaOnCompletion: input.karma,
    });
    return { delivery: serializeDelivery(delivery) };
  },

  async active(courierId) {
    await ready;
    for (const item of await net.itemRepository.findInState('CLAIMED')) {
      const delivery = net.deliveries.forItem(item.id);
      if (delivery && delivery.courierId === courierId && delivery.state !== 'DROPPED_OFF') {
        return { delivery: serializeDelivery(delivery), item: serializeItem(item) };
      }
    }
    return { delivery: null, item: null };
  },

  async pickup(deliveryId) {
    await ready;
    return { delivery: serializeDelivery(net.deliveries.pickUp(deliveryId)) };
  },

  async temp(deliveryId, celsius) {
    await ready;
    return { delivery: serializeDelivery(net.deliveries.recordTemp(deliveryId, celsius)) };
  },

  async dropoff(deliveryId) {
    await ready;
    const delivery = await net.deliveries.dropOff(deliveryId);
    return { delivery: serializeDelivery(delivery), wallet: await balances(delivery.courierId) };
  },

  async courierProfile(courierId) {
    await ready;
    const now = new Date();
    const month = now.toISOString().slice(0, 7);
    const employer = net.engagement.employerOf(courierId) ?? null;
    const volunteerRow = employer
      ? net.engagement
          .monthlyVolunteerReport(employer, now.getUTCFullYear(), now.getUTCMonth() + 1)
          .find((r) => r.courierId === courierId)
      : undefined;
    return {
      balances: await balances(courierId),
      engagement: net.engagement.engagement(courierId) ?? null,
      team: net.teams.teamOf(courierId) ?? null,
      leaderboard: net.engagement.leaderboard(5),
      teamLeaderboard: net.teams.monthlyLeaderboard(month, ZONE, 5),
      perks: net.partners.listPerks(),
      certified: net.certifications.isCertified(courierId, 'food-handler-101'),
      employer,
      volunteerMinutesThisMonth: volunteerRow?.minutes ?? 0,
    };
  },

  async route(from, to) {
    await ready;
    // No network in the self-contained demo: an honest two-leg street
    // estimate, same shape the server's OSRM fallback produces.
    const distanceMeters = Math.round(haversineMeters(from, to) * 1.3);
    return {
      route: {
        source: 'estimate' as const,
        distanceMeters,
        durationSeconds: Math.round(distanceMeters / 4.5),
        points: [from, { latitude: from.latitude, longitude: to.longitude }, to],
      },
    };
  },

  async certify(courierId, courseId) {
    await ready;
    const result = await net.certifications.complete(courierId, courseId);
    return { ...result, balances: await balances(courierId) };
  },

  async addSchedule(input) {
    await ready;
    const schedule = net.recurring.addSchedule({
      scheduleId: `${input.supplierId}-${Date.now()}`,
      supplierId: input.supplierId,
      title: input.title,
      category: input.category,
      quantity: 1,
      fmvCents: input.fmvCents,
      cogsCents: input.cogsCents,
      ...(input.salePriceCents !== undefined ? { salePriceCents: input.salePriceCents } : {}),
      latitude: 40.7251,
      longitude: -73.9512,
      zoneId: ZONE,
      ...(input.dietaryTags !== undefined ? { dietaryTags: input.dietaryTags } : {}),
      listAtHourUtc: input.listAtHourUtc,
      safeForHours: input.safeForHours,
    });
    return { schedule: { scheduleId: schedule.scheduleId } };
  },

  async certificate(itemId) {
    await ready;
    const cert = await net.compliance.certificate(itemId);
    return {
      certificate: {
        ...cert,
        custodyChain: cert.custodyChain.map((c) => ({ ...c, at: c.at.toISOString() })),
        tempLog: cert.tempLog.map((t) => ({ ...t, at: t.at.toISOString() })),
      },
    };
  },

  async redeemPerk(courierId, perkId) {
    await ready;
    const voucher = await net.partners.redeem(courierId, perkId, globalThis.crypto.randomUUID());
    return { voucher, balances: await balances(courierId) };
  },

  async supplierDashboard(supplierId) {
    await ready;
    const now = new Date();
    const items = await net.itemRepository.findBySupplier(supplierId);
    return {
      report: await net.compliance.recoveryReport(supplierId, now.getUTCFullYear(), now.getUTCMonth() + 1),
      schedules: net.recurring.schedulesOf(supplierId),
      items: items
        .sort((a, b) => b.listedAt.getTime() - a.listedAt.getTime())
        .slice(0, 12)
        .map(serializeItem),
      impact: net.impact.totals({ supplierId }),
    };
  },

  async skipToday(scheduleId) {
    await ready;
    net.recurring.skipToday(scheduleId);
    return { ok: true };
  },

  async zones() {
    await ready;
    return {
      zones: net.zoneHealth.allZones(),
      networkImpact: net.impact.totals(),
      fund: net.fund.state(),
      sponsor: net.sponsorship.impactMeter('greenpoint-bank', ZONE),
      openRescues: (await net.itemRepository.findInState('DONATION_PHASE')).length,
    };
  },

  async opsEvents() {
    await ready;
    return { events: opsEvents };
  },

  async referralPreview(referrerId, referrerRole) {
    await ready;
    const newUserId = `friend-${Date.now()}`;
    await net.onboarding.onboardRecipient({ userId: newUserId, referredBy: { referrerId, referrerRole } });
    const reward = await net.referrals.qualify(newUserId);
    return { newUserId, reward: reward ?? null, balances: await net.wallets.balances(referrerId) };
  },
};

const opsEvents: Array<{ at: string; kind: 'DONATION' | 'ESCALATED' | 'ALERT' | 'EXPIRED'; detail: string }> = [];
function pushOpsEvent(kind: (typeof opsEvents)[number]['kind'], detail: string): void {
  opsEvents.unshift({ at: new Date().toISOString(), kind, detail });
  if (opsEvents.length > 20) opsEvents.length = 20;
}
net.bus.on('donation.available', async ({ itemId }) => {
  const item = await net.itemRepository.findById(itemId);
  pushOpsEvent('DONATION', `${item?.title ?? itemId} entered the free pool`);
});
net.bus.on('donation.escalated', async ({ itemId, level, finalAlert }) => {
  const item = await net.itemRepository.findById(itemId);
  pushOpsEvent(
    finalAlert ? 'ALERT' : 'ESCALATED',
    finalAlert
      ? `${item?.title ?? itemId}: final alert — hub staff pinged`
      : `${item?.title ?? itemId}: unclaimed, re-dispatched wider (level ${level})`,
  );
});
net.bus.on('item.expired', async ({ itemId }) => {
  const item = await net.itemRepository.findById(itemId);
  pushOpsEvent('EXPIRED', `${item?.title ?? itemId} passed its safety window — counted against fill rate`);
});
