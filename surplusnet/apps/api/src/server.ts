import Fastify from 'fastify';
import fastifyStatic from '@fastify/static';
import { existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  createSurplusNet,
  haversineMeters,
  DomainError,
  type InMemoryCourierLocationSource,
  type SurplusItem,
  type WeatherCondition,
} from '@surplusnet/core';
import { seed, DEMO, HUBS, ZONE } from './seed.js';

const net = createSurplusNet({ fundShareRate: 0.2 });
await seed(net);

const locations = net.courierLocations as InMemoryCourierLocationSource;
const WEATHER: WeatherCondition = (process.env['DEMO_WEATHER'] as WeatherCondition) ?? 'RAIN';

const app = Fastify();

const webDist = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../web/dist');
if (existsSync(webDist)) {
  await app.register(fastifyStatic, { root: webDist });
}

app.setErrorHandler((err, _req, reply) => {
  if (err instanceof DomainError) {
    return reply.status(err.code === 'NOT_FOUND' ? 404 : 400).send({ error: err.message, code: err.code });
  }
  app.log.error(err);
  return reply.status(500).send({ error: 'internal error' });
});

function minutesLeft(until: Date, from = new Date()): number {
  return Math.max(0, Math.round((until.getTime() - from.getTime()) / 60_000));
}

function serializeItem(item: SurplusItem) {
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
    zoneId: item.zoneId,
    minutesLeftInSale: item.currentState === 'SALES_PHASE' ? minutesLeft(salesEndsAt, now) : 0,
    minutesUntilUnsafe: minutesLeft(item.safeUntil, now),
    impact: net.impact.computeForItem(item),
    latitude: item.latitude,
    longitude: item.longitude,
  };
}

/**
 * Route geometry between two points: OSRM when reachable (self-hosted via
 * OSRM_URL, or the public demo server), a two-leg fallback otherwise so the
 * UI always has something honest to draw. Cycling profile — this is an
 * e-bike network.
 */
async function computeRoute(from: { latitude: number; longitude: number }, to: { latitude: number; longitude: number }) {
  const osrmBase = process.env['OSRM_URL'] ?? 'https://router.project-osrm.org';
  try {
    const url = `${osrmBase}/route/v1/cycling/${from.longitude},${from.latitude};${to.longitude},${to.latitude}?overview=full&geometries=geojson`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2500);
    const res = await fetch(url, { signal: controller.signal });
    clearTimeout(timer);
    if (res.ok) {
      const body = (await res.json()) as {
        routes?: Array<{ distance: number; duration: number; geometry: { coordinates: [number, number][] } }>;
      };
      const route = body.routes?.[0];
      if (route) {
        return {
          source: 'osrm' as const,
          distanceMeters: Math.round(route.distance),
          durationSeconds: Math.round(route.duration),
          points: route.geometry.coordinates.map(([lng, lat]) => ({ latitude: lat, longitude: lng })),
        };
      }
    }
  } catch {
    // fall through to the estimate
  }
  const distanceMeters = Math.round(haversineMeters(from, to) * 1.3); // street factor
  return {
    source: 'estimate' as const,
    distanceMeters,
    durationSeconds: Math.round(distanceMeters / 4.5),
    points: [
      from,
      { latitude: from.latitude, longitude: to.longitude },
      to,
    ],
  };
}

app.get('/api/route', async (req) => {
  const q = req.query as { fromLat: string; fromLng: string; toLat: string; toLng: string };
  return {
    route: await computeRoute(
      { latitude: Number(q.fromLat), longitude: Number(q.fromLng) },
      { latitude: Number(q.toLat), longitude: Number(q.toLng) },
    ),
  };
});

async function openRescueCount(): Promise<number> {
  return (await net.itemRepository.findInState('DONATION_PHASE')).length;
}

// ── Recipient ─────────────────────────────────────────────────────────────

app.get('/api/feed', async (req) => {
  const { userId } = req.query as { userId?: string };
  const sales = await net.itemRepository.findInState('SALES_PHASE');
  const donations = await net.itemRepository.findInState('DONATION_PHASE');
  const visible = userId ? net.preferences.filterFeed(userId, [...sales, ...donations]) : [...sales, ...donations];
  return {
    fund: net.fund.state(),
    items: visible
      .sort((a, b) => a.safeUntil.getTime() - b.safeUntil.getTime())
      .map(serializeItem),
  };
});

app.post('/api/checkout/purchase', async (req) => {
  const body = req.body as {
    itemId: string;
    recipientId: string;
    cashCents: number;
    communityCredits: number;
    karmaCredits?: number;
  };
  const receipt = await net.checkout.purchase({ orderId: randomUUID(), ...body });
  return { receipt, wallet: await net.wallets.balances(body.recipientId) };
});

app.post('/api/checkout/claim', async (req) => {
  const { itemId, recipientId } = req.body as { itemId: string; recipientId: string };
  const item = await net.checkout.claimDonation(itemId, recipientId);
  return { item: serializeItem(item) };
});

app.get('/api/recipient/:userId/claims', async (req) => {
  const { userId } = req.params as { userId: string };
  const items = await net.itemRepository.findByRecipient(userId);
  return {
    claims: items.map((item) => ({
      ...serializeItem(item),
      delivery: net.deliveries.forItem(item.id) ?? null,
    })),
  };
});

app.get('/api/wallet/:userId', async (req) => {
  const { userId } = req.params as { userId: string };
  return { balances: await net.wallets.balances(userId) };
});

// ── Courier ───────────────────────────────────────────────────────────────

// Courier app heartbeat: live position feeds dispatch, staleness filtering,
// and per-offer distances. Falls back to the zone center for unknown couriers.
app.post('/api/courier/:courierId/location', async (req) => {
  const { courierId } = req.params as { courierId: string };
  const { latitude, longitude, transport } = req.body as {
    latitude: number;
    longitude: number;
    transport?: 'FOOT' | 'BIKE' | 'EBIKE';
  };
  locations.upsert({
    courierId,
    latitude,
    longitude,
    lastSeenAt: new Date(),
    transport: transport ?? locations.get(courierId)?.transport ?? 'BIKE',
  });
  return { ok: true };
});

app.get('/api/courier/:courierId/offers', async (req) => {
  const { courierId } = req.params as { courierId: string };
  const open = await net.itemRepository.findInState('DONATION_PHASE');
  const openCount = open.length;
  const me = locations.get(courierId) ?? { latitude: 40.724, longitude: -73.951 };
  const activeCouriers = (await locations.activeCouriersNear(me, 5000)).length;
  const now = new Date();

  const offers = open.map((item) => {
    const distanceMeters = Math.round(haversineMeters(me, item));
    const quote = net.karmaPricing.quote({
      minutesUntilUnsafe: minutesLeft(item.safeUntil, now),
      distanceMeters,
      weather: WEATHER,
      hourOfDay: now.getUTCHours(),
      activeCouriers,
      openRescues: openCount,
    });
    const hub = HUBS.reduce((a, b) => (haversineMeters(item, a) <= haversineMeters(item, b) ? a : b));
    return { item: serializeItem(item), distanceMeters, quote, hub, weather: WEATHER };
  });
  return { courierId, offers };
});

app.post('/api/courier/accept', async (req) => {
  const { itemId, courierId, karma, hubId } = req.body as {
    itemId: string;
    courierId: string;
    karma: number;
    hubId: string;
  };
  const hub = HUBS.find((h) => h.hubId === hubId) ?? HUBS[0]!;
  const delivery = await net.deliveries.accept({
    itemId,
    courierId,
    dropoffName: hub.name,
    dropoff: { latitude: hub.latitude, longitude: hub.longitude },
    karmaOnCompletion: karma,
  });
  return { delivery };
});

app.get('/api/courier/:courierId/active', async (req) => {
  const { courierId } = req.params as { courierId: string };
  const open = await net.itemRepository.findInState('CLAIMED');
  for (const item of open) {
    const delivery = net.deliveries.forItem(item.id);
    if (delivery && delivery.courierId === courierId && delivery.state !== 'DROPPED_OFF') {
      return { delivery, item: serializeItem(item) };
    }
  }
  return { delivery: null, item: null };
});

app.post('/api/delivery/:deliveryId/pickup', async (req) => {
  const { deliveryId } = req.params as { deliveryId: string };
  return { delivery: net.deliveries.pickUp(deliveryId) };
});

app.post('/api/delivery/:deliveryId/temp', async (req) => {
  const { deliveryId } = req.params as { deliveryId: string };
  const { celsius } = req.body as { celsius: number };
  return { delivery: net.deliveries.recordTemp(deliveryId, celsius) };
});

app.post('/api/delivery/:deliveryId/dropoff', async (req) => {
  const { deliveryId } = req.params as { deliveryId: string };
  const delivery = await net.deliveries.dropOff(deliveryId);
  return { delivery, wallet: await net.wallets.balances(delivery.courierId) };
});

app.get('/api/courier/:courierId/profile', async (req) => {
  const { courierId } = req.params as { courierId: string };
  const now = new Date();
  const month = now.toISOString().slice(0, 7);
  const employerId = net.engagement.employerOf(courierId);
  const volunteerRow = employerId
    ? net.engagement
        .monthlyVolunteerReport(employerId, now.getUTCFullYear(), now.getUTCMonth() + 1)
        .find((r) => r.courierId === courierId)
    : undefined;
  return {
    balances: await net.wallets.balances(courierId),
    engagement: net.engagement.engagement(courierId) ?? null,
    team: net.teams.teamOf(courierId) ?? null,
    leaderboard: net.engagement.leaderboard(5),
    teamLeaderboard: net.teams.monthlyLeaderboard(month, ZONE, 5),
    perks: net.partners.listPerks(),
    certified: net.certifications.isCertified(courierId, 'food-handler-101'),
    employer: employerId ?? null,
    volunteerMinutesThisMonth: volunteerRow?.minutes ?? 0,
  };
});

app.post('/api/courier/certify', async (req) => {
  const { courierId, courseId } = req.body as { courierId: string; courseId: string };
  const result = await net.certifications.complete(courierId, courseId);
  return { ...result, balances: await net.wallets.balances(courierId) };
});

app.post('/api/perks/redeem', async (req) => {
  const { courierId, perkId } = req.body as { courierId: string; perkId: string };
  const voucher = await net.partners.redeem(courierId, perkId, randomUUID());
  return { voucher, balances: await net.wallets.balances(courierId) };
});

// ── Supplier ──────────────────────────────────────────────────────────────

app.get('/api/supplier/:supplierId/dashboard', async (req) => {
  const { supplierId } = req.params as { supplierId: string };
  const now = new Date();
  const items = await net.itemRepository.findBySupplier(supplierId);
  const report = await net.compliance.recoveryReport(supplierId, now.getUTCFullYear(), now.getUTCMonth() + 1);
  return {
    report,
    schedules: net.recurring.schedulesOf(supplierId),
    items: items
      .sort((a, b) => b.listedAt.getTime() - a.listedAt.getTime())
      .slice(0, 12)
      .map(serializeItem),
    impact: net.impact.totals({ supplierId }),
  };
});

app.post('/api/supplier/schedule', async (req) => {
  const body = req.body as {
    supplierId: string;
    title: string;
    category: string;
    fmvCents: number;
    cogsCents: number;
    salePriceCents?: number;
    listAtHourUtc: number;
    safeForHours: number;
    dietaryTags?: string[];
  };
  const schedule = net.recurring.addSchedule({
    scheduleId: `${body.supplierId}-${Date.now()}`,
    supplierId: body.supplierId,
    title: body.title,
    category: body.category,
    quantity: 1,
    fmvCents: body.fmvCents,
    cogsCents: body.cogsCents,
    ...(body.salePriceCents !== undefined ? { salePriceCents: body.salePriceCents } : {}),
    latitude: 40.7251,
    longitude: -73.9512,
    zoneId: ZONE,
    ...(body.dietaryTags !== undefined ? { dietaryTags: body.dietaryTags } : {}),
    listAtHourUtc: body.listAtHourUtc,
    safeForHours: body.safeForHours,
  });
  return { schedule };
});

app.post('/api/supplier/schedule/:scheduleId/skip', async (req) => {
  const { scheduleId } = req.params as { scheduleId: string };
  net.recurring.skipToday(scheduleId);
  return { ok: true };
});

app.get('/api/supplier/certificate/:itemId', async (req) => {
  const { itemId } = req.params as { itemId: string };
  return { certificate: await net.compliance.certificate(itemId) };
});

// ── Ops / zones ───────────────────────────────────────────────────────────

app.get('/api/zones', async () => ({
  zones: net.zoneHealth.allZones(),
  networkImpact: net.impact.totals(),
  fund: net.fund.state(),
  sponsor: net.sponsorship.impactMeter('greenpoint-bank', ZONE),
  openRescues: await openRescueCount(),
}));

// ── Background workers ────────────────────────────────────────────────────

setInterval(() => {
  void net.rolloverWorker.tick().catch((err) => app.log.error(err));
  void net.escalation.tick().catch((err) => app.log.error(err));
  void net.recurring.tick().catch((err) => app.log.error(err));
}, 15_000).unref();

const port = Number(process.env['PORT'] ?? 4000);
await app.listen({ port, host: '0.0.0.0' });
console.log(`SurplusNet API on http://localhost:${port} (weather: ${WEATHER})`);
