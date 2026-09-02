import type { Clock } from '../../lib/clock.js';
import { systemClock } from '../../lib/clock.js';
import { ValidationError } from '../../lib/errors.js';

/**
 * Zone health — the density flywheel made measurable, and the guard against
 * the expansion mistake that kills marketplace-model rescue startups:
 * launching a neighborhood before it has the supply, courier, and hub
 * density to be reliable, burning every early adopter's trust.
 *
 * Each zone (a zip-code-sized area) tracks its party counts and rescue
 * outcomes, and gets a readiness verdict:
 *
 *   SEED             — recruiting; do NOT go live (listings would rot)
 *   READY_TO_LAUNCH  — pre-commit thresholds met (default: 5 suppliers,
 *                      30 couriers, 2 hubs — the zone launch playbook)
 *   LIVE_HEALTHY     — operating with a fill rate ≥ 80%
 *   AT_RISK          — fill rate below 50% at meaningful volume: pause
 *                      marketing, fix reliability first
 *
 * Fill rate = rescued / (rescued + expired). Expiries are counted, not
 * hidden — an unfilled rescue is the single most corrosive event in the
 * network and this is where it becomes visible.
 */

export interface ZoneThresholds {
  minSuppliers: number;
  minCouriers: number;
  minHubs: number;
  healthyFillRate: number;
  atRiskFillRate: number;
  /** Outcomes needed before fill rate is judged at all. */
  minOutcomes: number;
}

export const DEFAULT_ZONE_THRESHOLDS: ZoneThresholds = {
  minSuppliers: 5,
  minCouriers: 30,
  minHubs: 2,
  healthyFillRate: 0.8,
  atRiskFillRate: 0.5,
  minOutcomes: 10,
};

export type ZoneStatus = 'SEED' | 'READY_TO_LAUNCH' | 'LIVE_HEALTHY' | 'AT_RISK';

export interface ZoneMetrics {
  zoneId: string;
  suppliers: number;
  couriers: number;
  hubs: number;
  listings: number;
  rescued: number;
  expired: number;
  /** undefined until minOutcomes reached. */
  fillRate?: number;
  /** undefined until a rescue lands. */
  medianMinutesToRescue?: number;
  status: ZoneStatus;
  /** What's still missing to advance (empty when LIVE_HEALTHY). */
  gaps: string[];
}

interface ZoneState {
  suppliers: Set<string>;
  couriers: Set<string>;
  hubs: Set<string>;
  listings: number;
  rescued: number;
  expired: number;
  rescueMinutes: number[];
}

export class ZoneHealthService {
  private zones = new Map<string, ZoneState>();

  constructor(
    private readonly thresholds: ZoneThresholds = DEFAULT_ZONE_THRESHOLDS,
    private readonly clock: Clock = systemClock,
  ) {}

  registerSupplier(zoneId: string, supplierId: string): void {
    this.zone(zoneId).suppliers.add(supplierId);
  }

  registerCourier(zoneId: string, courierId: string): void {
    this.zone(zoneId).couriers.add(courierId);
  }

  registerHub(zoneId: string, hubId: string): void {
    this.zone(zoneId).hubs.add(hubId);
  }

  recordListing(zoneId: string): void {
    this.zone(zoneId).listings += 1;
  }

  recordRescue(zoneId: string, minutesToRescue: number): void {
    if (minutesToRescue < 0) throw new ValidationError('minutesToRescue must be >= 0');
    const zone = this.zone(zoneId);
    zone.rescued += 1;
    zone.rescueMinutes.push(minutesToRescue);
  }

  recordExpiry(zoneId: string): void {
    this.zone(zoneId).expired += 1;
  }

  metrics(zoneId: string): ZoneMetrics {
    const zone = this.zone(zoneId);
    const outcomes = zone.rescued + zone.expired;
    const fillRate = outcomes >= this.thresholds.minOutcomes ? zone.rescued / outcomes : undefined;

    const gaps: string[] = [];
    if (zone.suppliers.size < this.thresholds.minSuppliers) {
      gaps.push(`suppliers ${zone.suppliers.size}/${this.thresholds.minSuppliers}`);
    }
    if (zone.couriers.size < this.thresholds.minCouriers) {
      gaps.push(`couriers ${zone.couriers.size}/${this.thresholds.minCouriers}`);
    }
    if (zone.hubs.size < this.thresholds.minHubs) {
      gaps.push(`hubs ${zone.hubs.size}/${this.thresholds.minHubs}`);
    }

    let status: ZoneStatus;
    if (gaps.length > 0) {
      status = 'SEED';
    } else if (fillRate === undefined) {
      status = 'READY_TO_LAUNCH';
    } else if (fillRate < this.thresholds.atRiskFillRate) {
      status = 'AT_RISK';
      gaps.push(`fill rate ${(fillRate * 100).toFixed(0)}% — fix reliability before growing`);
    } else if (fillRate >= this.thresholds.healthyFillRate) {
      status = 'LIVE_HEALTHY';
    } else {
      status = 'READY_TO_LAUNCH';
      gaps.push(`fill rate ${(fillRate * 100).toFixed(0)}% below healthy ${this.thresholds.healthyFillRate * 100}%`);
    }

    const sorted = [...zone.rescueMinutes].sort((a, b) => a - b);
    const medianMinutesToRescue =
      sorted.length === 0 ? undefined : sorted[Math.floor((sorted.length - 1) / 2)];

    return {
      zoneId,
      suppliers: zone.suppliers.size,
      couriers: zone.couriers.size,
      hubs: zone.hubs.size,
      listings: zone.listings,
      rescued: zone.rescued,
      expired: zone.expired,
      ...(fillRate !== undefined ? { fillRate: Math.round(fillRate * 100) / 100 } : {}),
      ...(medianMinutesToRescue !== undefined ? { medianMinutesToRescue } : {}),
      status,
      gaps,
    };
  }

  allZones(): ZoneMetrics[] {
    return [...this.zones.keys()].map((zoneId) => this.metrics(zoneId));
  }

  private zone(zoneId: string): ZoneState {
    if (!zoneId) throw new ValidationError('zoneId is required');
    let state = this.zones.get(zoneId);
    if (!state) {
      state = {
        suppliers: new Set(),
        couriers: new Set(),
        hubs: new Set(),
        listings: 0,
        rescued: 0,
        expired: 0,
        rescueMinutes: [],
      };
      this.zones.set(zoneId, state);
    }
    return state;
  }
}
