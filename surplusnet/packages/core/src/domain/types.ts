/**
 * Domain types mirrored from prisma/schema.prisma. Services program against
 * these plus the repository interfaces, so the business logic is testable
 * without a live database; the Prisma client plugs in behind the same
 * interfaces in production.
 */

export type UserRole = 'SUPPLIER' | 'COURIER' | 'RECIPIENT';

export type SurplusItemState =
  | 'SALES_PHASE'
  | 'DONATION_PHASE'
  | 'CLAIMED'
  | 'DELIVERED'
  | 'EXPIRED';

export type DeliveryState =
  | 'DISPATCHED'
  | 'ACCEPTED'
  | 'PICKED_UP'
  | 'IN_TRANSIT'
  | 'DROPPED_OFF'
  | 'VERIFIED'
  | 'CANCELLED';

export type TokenKind = 'CASH' | 'KARMA_CREDIT' | 'COMMUNITY_CREDIT';

export interface GeoPoint {
  latitude: number;
  longitude: number;
}

export interface SurplusItem extends GeoPoint {
  id: string;
  supplierId: string;
  recipientId?: string;
  title: string;
  category: string;
  quantity: number;
  photoUrl?: string;
  fmvCents: number;
  cogsCents: number;
  calculatedTaxDeductionCents: number;
  salePriceCents?: number;
  currentState: SurplusItemState;
  listedAt: Date;
  salesWindowMinutes: number;
  rolledOverAt?: Date;
  safeUntil: Date;
}

export interface CourierPresence extends GeoPoint {
  courierId: string;
  /** Last heartbeat from the courier app; stale entries are skipped. */
  lastSeenAt: Date;
  transport: 'FOOT' | 'BIKE' | 'EBIKE';
}

export interface WalletBalances {
  cashBalanceCents: number;
  karmaCreditBalance: number;
  communityCreditBalance: number;
}
