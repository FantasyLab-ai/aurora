import { describe, expect, it } from 'vitest';
import { RecurringListingService } from './recurring-listing.service.js';
import { SurplusItemService } from './surplus-item.service.js';
import { InMemorySurplusItemRepository } from './surplus-item.repository.js';
import { DonationLedger, InMemoryLedgerStore } from '../tax/donation-ledger.js';
import type { Clock } from '../../lib/clock.js';
import { ValidationError } from '../../lib/errors.js';

class FakeClock implements Clock {
  constructor(private current: Date) {}
  now(): Date {
    return new Date(this.current);
  }
  set(iso: string): void {
    this.current = new Date(iso);
  }
}

function makeFixture(startIso = '2026-09-02T18:00:00Z') {
  // 2026-09-02 is a Wednesday (UTC day 3)
  const clock = new FakeClock(new Date(startIso));
  const repo = new InMemorySurplusItemRepository();
  const items = new SurplusItemService(repo, new DonationLedger(new InMemoryLedgerStore()), clock);
  const service = new RecurringListingService(items, clock);

  service.addSchedule({
    scheduleId: 'bakery-close',
    supplierId: 'bakery-1',
    title: 'Evening Bakery Box',
    category: 'bakery',
    quantity: 1,
    fmvCents: 2400,
    cogsCents: 900,
    latitude: 40.73,
    longitude: -73.99,
    listAtHourUtc: 21,
    safeForHours: 12,
  });
  return { clock, repo, service };
}

describe('RecurringListingService', () => {
  it('fires once per day at the listing hour, never before, never twice', async () => {
    const { clock, service } = makeFixture();

    expect(await service.tick()).toEqual([]); // 18:00, too early
    clock.set('2026-09-02T21:05:00Z');
    const listed = await service.tick();
    expect(listed).toHaveLength(1);
    expect(listed[0]?.title).toBe('Evening Bakery Box');
    expect(listed[0]?.safeUntil).toEqual(new Date('2026-09-03T09:05:00Z'));

    clock.set('2026-09-02T23:00:00Z');
    expect(await service.tick()).toEqual([]); // same day, already fired

    clock.set('2026-09-03T21:00:00Z');
    expect(await service.tick()).toHaveLength(1); // next day fires again
  });

  it('"nothing left today" skips one day without touching the schedule', async () => {
    const { clock, service } = makeFixture();
    service.skipToday('bakery-close');

    clock.set('2026-09-02T21:30:00Z');
    expect(await service.tick()).toEqual([]);
    clock.set('2026-09-03T21:30:00Z');
    expect(await service.tick()).toHaveLength(1);
  });

  it('respects daysOfWeek and pause/resume', async () => {
    const { clock, service } = makeFixture();
    service.addSchedule({
      scheduleId: 'weekend-market',
      supplierId: 'farm-1',
      title: 'Market Leftovers',
      category: 'produce',
      quantity: 2,
      fmvCents: 3000,
      cogsCents: 1000,
      latitude: 40.7,
      longitude: -74.0,
      listAtHourUtc: 15,
      safeForHours: 24,
      daysOfWeek: [6], // Saturdays only
    });

    clock.set('2026-09-02T22:00:00Z'); // Wednesday
    const wednesday = await service.tick();
    expect(wednesday.map((i) => i.title)).toEqual(['Evening Bakery Box']);

    service.pause('bakery-close');
    clock.set('2026-09-05T22:00:00Z'); // Saturday
    const saturday = await service.tick();
    expect(saturday.map((i) => i.title)).toEqual(['Market Leftovers']);

    service.resume('bakery-close');
    clock.set('2026-09-06T22:00:00Z'); // Sunday
    expect((await service.tick()).map((i) => i.title)).toEqual(['Evening Bakery Box']);
  });

  it('validates schedule shape', () => {
    const { service } = makeFixture();
    expect(() =>
      service.addSchedule({
        scheduleId: 'bad',
        supplierId: 's',
        title: 't',
        category: 'c',
        quantity: 1,
        fmvCents: 100,
        cogsCents: 50,
        latitude: 0,
        longitude: 0,
        listAtHourUtc: 25,
        safeForHours: 4,
      }),
    ).toThrow(ValidationError);
  });
});
