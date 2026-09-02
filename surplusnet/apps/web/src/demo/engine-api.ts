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
  };
}

function serializeDelivery(delivery: NonNullable<ReturnType<typeof net.deliveries.byId>>): Delivery {
  return {
    id: delivery.id,
    itemId: delivery.itemId,
    courierId: delivery.courierId,
    state: delivery.state,
    dropoffName: delivery.dropoffName,
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

  async offers(_courierId) {
    await ready;
    const open = await net.itemRepository.findInState('DONATION_PHASE');
    const activeCouriers = (await locations.activeCouriersNear(ME, 5000)).length;
    const now = new Date();
    return {
      offers: open.map((item) => {
        const distanceMeters = Math.round(haversineMeters(ME, item));
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
    const month = new Date().toISOString().slice(0, 7);
    return {
      balances: await balances(courierId),
      engagement: net.engagement.engagement(courierId) ?? null,
      team: net.teams.teamOf(courierId) ?? null,
      leaderboard: net.engagement.leaderboard(5),
      teamLeaderboard: net.teams.monthlyLeaderboard(month, ZONE, 5),
      perks: net.partners.listPerks(),
      certified: net.certifications.isCertified(courierId, 'food-handler-101'),
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
};
