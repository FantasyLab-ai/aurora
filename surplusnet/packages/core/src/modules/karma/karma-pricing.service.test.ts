import { describe, expect, it } from 'vitest';
import { KarmaPricingService } from './karma-pricing.service.js';
import { ValidationError } from '../../lib/errors.js';

const calm = {
  minutesUntilUnsafe: 240,
  distanceMeters: 800,
  weather: 'CLEAR' as const,
  hourOfDay: 14,
  activeCouriers: 5,
  openRescues: 2,
};

describe('KarmaPricingService', () => {
  const service = new KarmaPricingService(10);

  it('pays the base rate on a calm afternoon', () => {
    const quote = service.quote(calm);
    expect(quote.multiplier).toBe(1);
    expect(quote.karma).toBe(10);
  });

  it('surges for the rainy 9pm rescue with a closing window and no couriers', () => {
    const quote = service.quote({
      minutesUntilUnsafe: 20,
      distanceMeters: 2000,
      weather: 'RAIN',
      hourOfDay: 21,
      activeCouriers: 1,
      openRescues: 4,
    });
    // 1 + 0.8 + 0.3 + 0.3 + 0.2 + 0.5 = 3.1 → clamped to 3.0
    expect(quote.multiplier).toBe(3);
    expect(quote.karma).toBe(30);
    expect(quote.reasons).toContain('courier shortage in zone');
  });

  it('discounts a well-covered zone but never below the floor', () => {
    const quote = service.quote({ ...calm, activeCouriers: 10, openRescues: 2 });
    expect(quote.multiplier).toBe(0.8);
    expect(quote.karma).toBe(8);
  });

  it('adds moderate urgency inside the one-hour window', () => {
    const quote = service.quote({ ...calm, minutesUntilUnsafe: 50 });
    expect(quote.multiplier).toBe(1.4);
    expect(quote.karma).toBe(14);
  });

  it('validates inputs', () => {
    expect(() => service.quote({ ...calm, hourOfDay: 24 })).toThrow(ValidationError);
    expect(() => new KarmaPricingService(0)).toThrow(ValidationError);
  });
});
