import { describe, expect, it } from 'vitest';
import { ZoneHealthService } from './zone-health.service.js';

function seedParties(service: ZoneHealthService, zone: string, suppliers = 5, couriers = 30, hubs = 2) {
  for (let i = 0; i < suppliers; i++) service.registerSupplier(zone, `s${i}`);
  for (let i = 0; i < couriers; i++) service.registerCourier(zone, `c${i}`);
  for (let i = 0; i < hubs; i++) service.registerHub(zone, `h${i}`);
}

describe('ZoneHealthService', () => {
  it('stays SEED with named gaps until the launch playbook thresholds are met', () => {
    const service = new ZoneHealthService();
    seedParties(service, 'downtown', 3, 10, 1);

    const m = service.metrics('downtown');
    expect(m.status).toBe('SEED');
    expect(m.gaps).toEqual(['suppliers 3/5', 'couriers 10/30', 'hubs 1/2']);
  });

  it('advances to READY_TO_LAUNCH at density, LIVE_HEALTHY at fill rate', () => {
    const service = new ZoneHealthService();
    seedParties(service, 'downtown');
    expect(service.metrics('downtown').status).toBe('READY_TO_LAUNCH');

    for (let i = 0; i < 9; i++) service.recordRescue('downtown', 12);
    service.recordExpiry('downtown');
    const m = service.metrics('downtown');
    expect(m.status).toBe('LIVE_HEALTHY'); // 9/10 = 90%
    expect(m.fillRate).toBe(0.9);
    expect(m.medianMinutesToRescue).toBe(12);
    expect(m.gaps).toEqual([]);
  });

  it('flags AT_RISK when the fill rate collapses at meaningful volume', () => {
    const service = new ZoneHealthService();
    seedParties(service, 'downtown');
    for (let i = 0; i < 4; i++) service.recordRescue('downtown', 20);
    for (let i = 0; i < 6; i++) service.recordExpiry('downtown');

    const m = service.metrics('downtown');
    expect(m.status).toBe('AT_RISK'); // 40%
    expect(m.gaps[0]).toContain('fix reliability');
  });

  it('does not judge fill rate before minOutcomes', () => {
    const service = new ZoneHealthService();
    seedParties(service, 'downtown');
    service.recordExpiry('downtown'); // 0% but only 1 outcome
    expect(service.metrics('downtown').status).toBe('READY_TO_LAUNCH');
    expect(service.metrics('downtown').fillRate).toBeUndefined();
  });

  it('lists all zones for the ops dashboard', () => {
    const service = new ZoneHealthService();
    service.registerSupplier('a', 's1');
    service.registerCourier('b', 'c1');
    expect(service.allZones().map((z) => z.zoneId).sort()).toEqual(['a', 'b']);
  });
});
