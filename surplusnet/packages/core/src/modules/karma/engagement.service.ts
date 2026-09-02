import type { Clock } from '../../lib/clock.js';
import { systemClock } from '../../lib/clock.js';

/**
 * Gamified status & corporate volunteer time (Epic 3).
 *
 * - Streaks: consecutive calendar days (UTC) with at least one completed
 *   rescue. Missing a day resets to 1 on the next delivery.
 * - Badges: awarded at lifetime delivery milestones, kept forever.
 * - Leaderboard: ranked by lifetime rescues, streak as tiebreaker — the
 *   localized competition layer (scope to a neighborhood by keeping one
 *   service instance per zone, mirroring how dispatch is already zoned).
 * - Corporate volunteer hours: couriers linked to an employer accrue
 *   verified minutes per completed delivery; the monthly per-employer
 *   report is what HR imports to grant paid volunteer-hour credit.
 */

export const BADGE_THRESHOLDS: ReadonlyArray<{ deliveries: number; badge: string }> = [
  { deliveries: 1, badge: 'first-rescue' },
  { deliveries: 10, badge: 'block-hero' },
  { deliveries: 50, badge: 'neighborhood-legend' },
  { deliveries: 100, badge: 'city-champion' },
];

export interface CourierEngagement {
  courierId: string;
  totalDeliveries: number;
  currentStreakDays: number;
  longestStreakDays: number;
  badges: string[];
  lastDeliveryDay?: string;
}

export interface LeaderboardRow {
  rank: number;
  courierId: string;
  totalDeliveries: number;
  currentStreakDays: number;
  badges: string[];
}

interface VolunteerLogEntry {
  courierId: string;
  employerId: string;
  minutes: number;
  deliveryId: string;
  at: Date;
}

/** UTC calendar day, e.g. "2026-09-02". */
function dayOf(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function previousDay(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return dayOf(d);
}

export class EngagementService {
  private couriers = new Map<string, CourierEngagement>();
  private employerLinks = new Map<string, string>();
  private volunteerLog: VolunteerLogEntry[] = [];

  constructor(
    private readonly options: { minutesPerDelivery?: number } = {},
    private readonly clock: Clock = systemClock,
  ) {}

  /** HR links a courier to their employer's volunteer-hours program. */
  linkEmployer(courierId: string, employerId: string): void {
    this.employerLinks.set(courierId, employerId);
  }

  /** Called on every verified `delivery.completed`. Returns newly earned badges. */
  recordDelivery(courierId: string, deliveryId: string, at: Date = this.clock.now()): string[] {
    const day = dayOf(at);
    const existing = this.couriers.get(courierId) ?? {
      courierId,
      totalDeliveries: 0,
      currentStreakDays: 0,
      longestStreakDays: 0,
      badges: [],
    };

    existing.totalDeliveries += 1;
    if (existing.lastDeliveryDay === day) {
      // same-day deliveries extend totals, not the streak
    } else if (existing.lastDeliveryDay === previousDay(day)) {
      existing.currentStreakDays += 1;
    } else {
      existing.currentStreakDays = 1;
    }
    existing.lastDeliveryDay = day;
    existing.longestStreakDays = Math.max(existing.longestStreakDays, existing.currentStreakDays);

    const newBadges = BADGE_THRESHOLDS.filter(
      (t) => existing.totalDeliveries >= t.deliveries && !existing.badges.includes(t.badge),
    ).map((t) => t.badge);
    existing.badges.push(...newBadges);

    this.couriers.set(courierId, existing);

    const employerId = this.employerLinks.get(courierId);
    if (employerId) {
      this.volunteerLog.push({
        courierId,
        employerId,
        minutes: this.options.minutesPerDelivery ?? 15,
        deliveryId,
        at,
      });
    }

    return newBadges;
  }

  /** External badge grants (e.g. certifications); idempotent per badge. */
  awardBadge(courierId: string, badge: string): void {
    const existing = this.couriers.get(courierId) ?? {
      courierId,
      totalDeliveries: 0,
      currentStreakDays: 0,
      longestStreakDays: 0,
      badges: [],
    };
    if (!existing.badges.includes(badge)) existing.badges.push(badge);
    this.couriers.set(courierId, existing);
  }

  engagement(courierId: string): CourierEngagement | undefined {
    const e = this.couriers.get(courierId);
    return e ? { ...e, badges: [...e.badges] } : undefined;
  }

  leaderboard(limit = 10): LeaderboardRow[] {
    return [...this.couriers.values()]
      .sort(
        (a, b) =>
          b.totalDeliveries - a.totalDeliveries || b.currentStreakDays - a.currentStreakDays,
      )
      .slice(0, limit)
      .map((e, i) => ({
        rank: i + 1,
        courierId: e.courierId,
        totalDeliveries: e.totalDeliveries,
        currentStreakDays: e.currentStreakDays,
        badges: [...e.badges],
      }));
  }

  /** Verified volunteer time per employee, for HR's paid-volunteer-hours program. */
  monthlyVolunteerReport(
    employerId: string,
    year: number,
    month: number,
  ): Array<{ courierId: string; deliveries: number; minutes: number }> {
    const rows = new Map<string, { courierId: string; deliveries: number; minutes: number }>();
    for (const entry of this.volunteerLog) {
      if (
        entry.employerId !== employerId ||
        entry.at.getUTCFullYear() !== year ||
        entry.at.getUTCMonth() + 1 !== month
      ) {
        continue;
      }
      const row = rows.get(entry.courierId) ?? { courierId: entry.courierId, deliveries: 0, minutes: 0 };
      row.deliveries += 1;
      row.minutes += entry.minutes;
      rows.set(entry.courierId, row);
    }
    return [...rows.values()].sort((a, b) => b.minutes - a.minutes);
  }
}
