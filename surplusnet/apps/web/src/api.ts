export interface ItemImpact {
  mealsRescued: number;
  poundsDiverted: number;
  co2eGrams: number;
  avoidedDisposalCents: number;
}

export interface FeedItem {
  id: string;
  title: string;
  category: string;
  supplierId: string;
  dietaryTags: string[];
  state: 'SALES_PHASE' | 'DONATION_PHASE' | 'CLAIMED' | 'DELIVERED' | 'EXPIRED';
  priceCents: number;
  fmvCents: number;
  taxDeductionCents: number;
  minutesLeftInSale: number;
  minutesUntilUnsafe: number;
  impact: ItemImpact;
}

export interface Balances {
  cashBalanceCents: number;
  karmaCreditBalance: number;
  communityCreditBalance: number;
}

export interface FundState {
  poolCents: number;
  outstandingCredits: number;
  headroomCredits: number;
}

export interface KarmaQuote {
  baseKarma: number;
  multiplier: number;
  karma: number;
  reasons: string[];
}

export interface Offer {
  item: FeedItem;
  distanceMeters: number;
  quote: KarmaQuote;
  hub: { hubId: string; name: string };
  weather: string;
}

export interface Delivery {
  id: string;
  itemId: string;
  courierId: string;
  state: 'ACCEPTED' | 'PICKED_UP' | 'DROPPED_OFF' | 'CANCELLED';
  dropoffName: string;
  karmaOnCompletion: number;
  tempReadings: Array<{ at: string; celsius: number }>;
  coldChainCompliant: boolean;
}

export interface CourierProfile {
  balances: Balances;
  engagement: {
    totalDeliveries: number;
    currentStreakDays: number;
    longestStreakDays: number;
    badges: string[];
  } | null;
  team: { teamId: string; name: string } | null;
  leaderboard: Array<{ rank: number; courierId: string; totalDeliveries: number; currentStreakDays: number }>;
  teamLeaderboard: Array<{ rank: number; name: string; rescues: number; activeMembers: number }>;
  perks: Array<{ perkId: string; title: string; costKarma: number; inventory: number }>;
  certified: boolean;
}

export interface SupplierDashboard {
  report: {
    itemCount: number;
    poundsRecovered: number;
    mealsRescued: number;
    destinations: string[];
    taxDeductionCents: number;
    avoidedDisposalCents: number;
    co2eKg: number;
  };
  schedules: Array<{ scheduleId: string; title: string; listAtHourUtc: number; paused: boolean }>;
  items: FeedItem[];
  impact: { itemCount: number; mealsRescued: number; poundsDiverted: number; co2eKg: number };
}

export interface ZoneMetrics {
  zoneId: string;
  suppliers: number;
  couriers: number;
  hubs: number;
  listings: number;
  rescued: number;
  expired: number;
  fillRate?: number;
  medianMinutesToRescue?: number;
  status: 'SEED' | 'READY_TO_LAUNCH' | 'LIVE_HEALTHY' | 'AT_RISK';
  gaps: string[];
}

export interface ZonesResponse {
  zones: ZoneMetrics[];
  networkImpact: { itemCount: number; mealsRescued: number; poundsDiverted: number; co2eKg: number; avoidedDisposalCents: number };
  fund: FundState;
  sponsor: { grantedCents: number; matchedCents: number; karmaSubsidyCents: number };
  openRescues: number;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error ?? `request failed: ${res.status}`);
  return body as T;
}

export const api = {
  feed: (userId: string) => request<{ fund: FundState; items: FeedItem[] }>(`/api/feed?userId=${userId}`),
  purchase: (input: { itemId: string; recipientId: string; cashCents: number; communityCredits: number; karmaCredits?: number }) =>
    request<{ receipt: { supplierProceedsCents: number; fundContributionCents: number }; wallet: Balances }>(
      '/api/checkout/purchase',
      { method: 'POST', body: JSON.stringify(input) },
    ),
  claimDonation: (itemId: string, recipientId: string) =>
    request<{ item: FeedItem }>('/api/checkout/claim', { method: 'POST', body: JSON.stringify({ itemId, recipientId }) }),
  claims: (userId: string) => request<{ claims: Array<FeedItem & { delivery: Delivery | null }> }>(`/api/recipient/${userId}/claims`),
  wallet: (userId: string) => request<{ balances: Balances }>(`/api/wallet/${userId}`),
  offers: (courierId: string) => request<{ offers: Offer[] }>(`/api/courier/${courierId}/offers`),
  accept: (input: { itemId: string; courierId: string; karma: number; hubId: string }) =>
    request<{ delivery: Delivery }>('/api/courier/accept', { method: 'POST', body: JSON.stringify(input) }),
  active: (courierId: string) => request<{ delivery: Delivery | null; item: FeedItem | null }>(`/api/courier/${courierId}/active`),
  pickup: (deliveryId: string) => request<{ delivery: Delivery }>(`/api/delivery/${deliveryId}/pickup`, { method: 'POST', body: '{}' }),
  temp: (deliveryId: string, celsius: number) =>
    request<{ delivery: Delivery }>(`/api/delivery/${deliveryId}/temp`, { method: 'POST', body: JSON.stringify({ celsius }) }),
  dropoff: (deliveryId: string) =>
    request<{ delivery: Delivery; wallet: Balances }>(`/api/delivery/${deliveryId}/dropoff`, { method: 'POST', body: '{}' }),
  courierProfile: (courierId: string) => request<CourierProfile>(`/api/courier/${courierId}/profile`),
  redeemPerk: (courierId: string, perkId: string) =>
    request<{ voucher: { voucherCode: string }; balances: Balances }>('/api/perks/redeem', {
      method: 'POST',
      body: JSON.stringify({ courierId, perkId }),
    }),
  supplierDashboard: (supplierId: string) => request<SupplierDashboard>(`/api/supplier/${supplierId}/dashboard`),
  skipToday: (scheduleId: string) => request<{ ok: true }>(`/api/supplier/schedule/${scheduleId}/skip`, { method: 'POST', body: '{}' }),
  zones: () => request<ZonesResponse>('/api/zones'),
};

export const dollars = (cents: number): string => `$${(cents / 100).toFixed(2)}`;
export const categoryEmoji: Record<string, string> = {
  bakery: '🥐',
  produce: '🥬',
  dairy: '🥛',
  prepared: '🥗',
  meat: '🍗',
};
