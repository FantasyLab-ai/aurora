import { describe, expect, it } from 'vitest';
import type { Clock } from '../../lib/clock.js';
import { EventBus } from '../../lib/event-bus.js';
import { DonationLedger, InMemoryLedgerStore } from '../tax/donation-ledger.js';
import { InMemorySurplusItemRepository } from './surplus-item.repository.js';
import { SurplusItemService } from './surplus-item.service.js';
import { PhaseRolloverWorker } from './phase-rollover.worker.js';

class FakeClock implements Clock {
  constructor(private current: Date) {}
  now(): Date {
    return new Date(this.current);
  }
  advanceMinutes(minutes: number): void {
    this.current = new Date(this.current.getTime() + minutes * 60_000);
  }
}

function makeFixture(safeForHours = 6) {
  const clock = new FakeClock(new Date('2026-09-02T10:00:00Z'));
  const repo = new InMemorySurplusItemRepository();
  const ledger = new DonationLedger(new InMemoryLedgerStore(), clock);
  const service = new SurplusItemService(repo, ledger, clock);
  const bus = new EventBus();
  const worker = new PhaseRolloverWorker(repo, bus, {}, clock);
  const listItem = () =>
    service.listSurplus({
      supplierId: 'supplier-1',
      title: 'Artisan Bakery Bundle',
      category: 'bakery',
      fmvCents: 2400,
      cogsCents: 900,
      latitude: 40.7128,
      longitude: -74.006,
      safeUntil: new Date(clock.now().getTime() + safeForHours * 3_600_000),
    });
  return { clock, repo, service, bus, worker, listItem };
}

describe('PhaseRolloverWorker', () => {
  it('leaves items alone while the sales window is open', async () => {
    const { clock, worker, listItem } = makeFixture();
    await listItem();
    clock.advanceMinutes(44);
    const result = await worker.tick();
    expect(result.rolledOver).toEqual([]);
    expect(result.expired).toEqual([]);
  });

  it('rolls unsold items to DONATION_PHASE at $0 after 45 minutes and emits donation.available', async () => {
    const { clock, repo, bus, worker, listItem } = makeFixture();
    const item = await listItem();

    const events: Array<{ itemId: string }> = [];
    bus.on('donation.available', (e) => {
      events.push(e);
    });

    clock.advanceMinutes(45);
    const result = await worker.tick();

    expect(result.rolledOver).toEqual([item.id]);
    const updated = await repo.findById(item.id);
    expect(updated?.currentState).toBe('DONATION_PHASE');
    expect(updated?.salePriceCents).toBe(0);
    expect(updated?.rolledOverAt).toEqual(clock.now());
    expect(events).toEqual([{ itemId: item.id, latitude: 40.7128, longitude: -74.006 }]);
  });

  it('is idempotent: a second sweep does not re-roll or re-emit', async () => {
    const { clock, bus, worker, listItem } = makeFixture();
    await listItem();
    let emissions = 0;
    bus.on('donation.available', () => {
      emissions += 1;
    });

    clock.advanceMinutes(50);
    await worker.tick();
    await worker.tick();
    expect(emissions).toBe(1);
  });

  it('expires items past their cold-chain deadline instead of donating them', async () => {
    const { clock, repo, bus, worker, listItem } = makeFixture(1);
    const item = await listItem();
    let donations = 0;
    bus.on('donation.available', () => {
      donations += 1;
    });

    clock.advanceMinutes(90);
    const result = await worker.tick();

    expect(result.expired).toEqual([item.id]);
    expect(donations).toBe(0);
    expect((await repo.findById(item.id))?.currentState).toBe('EXPIRED');
  });
});
