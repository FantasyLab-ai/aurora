import { ValidationError } from '../../lib/errors.js';

/**
 * Dynamic (surge) karma pricing — what makes the courier network *reliable*.
 *
 * A flat 10-karma mint fills sunny-Saturday runs and leaves the rainy 9pm
 * rescue with a 20-minute window unfilled, and a pantry stood up twice stops
 * listing. So karma responds to conditions the way rideshare pay does:
 *
 *   multiplier = 1
 *     + urgency   (safety window closing: ≤30min +0.8, ≤60min +0.4)
 *     + distance  (long haul for foot/bike: >1600m +0.3)
 *     + weather   (RAIN +0.3, SNOW/STORM +0.6)
 *     + off-peak  (before 7am / after 8pm +0.2)
 *     + scarcity  (fewer active couriers than open rescues +0.5;
 *                  3x oversupplied -0.2)
 *   clamped to [0.8, 3.0]
 *
 * The result is locked in when the courier accepts, so conditions changing
 * mid-run (or event replays) can never re-price a delivery.
 */

export type WeatherCondition = 'CLEAR' | 'RAIN' | 'SNOW' | 'STORM';

export interface KarmaPricingInput {
  /** Minutes until the item's cold-chain deadline. */
  minutesUntilUnsafe: number;
  distanceMeters: number;
  weather: WeatherCondition;
  /** Local hour of day, 0-23. */
  hourOfDay: number;
  /** Couriers active in the zone right now. */
  activeCouriers: number;
  /** Rescues currently waiting for a courier in the zone. */
  openRescues: number;
}

export interface KarmaQuote {
  baseKarma: number;
  multiplier: number;
  karma: number;
  reasons: string[];
}

export const KARMA_MULTIPLIER_MIN = 0.8;
export const KARMA_MULTIPLIER_MAX = 3.0;

export class KarmaPricingService {
  constructor(private readonly baseKarma: number = 10) {
    if (!Number.isSafeInteger(baseKarma) || baseKarma <= 0) {
      throw new ValidationError('baseKarma must be a positive integer');
    }
  }

  quote(input: KarmaPricingInput): KarmaQuote {
    if (input.hourOfDay < 0 || input.hourOfDay > 23) {
      throw new ValidationError(`hourOfDay must be 0-23, got ${input.hourOfDay}`);
    }
    let multiplier = 1;
    const reasons: string[] = [];

    if (input.minutesUntilUnsafe <= 30) {
      multiplier += 0.8;
      reasons.push('urgent: safety window under 30 minutes');
    } else if (input.minutesUntilUnsafe <= 60) {
      multiplier += 0.4;
      reasons.push('time-sensitive: safety window under an hour');
    }

    if (input.distanceMeters > 1600) {
      multiplier += 0.3;
      reasons.push('long haul');
    }

    if (input.weather === 'RAIN') {
      multiplier += 0.3;
      reasons.push('rain');
    } else if (input.weather === 'SNOW' || input.weather === 'STORM') {
      multiplier += 0.6;
      reasons.push('severe weather');
    }

    if (input.hourOfDay < 7 || input.hourOfDay >= 20) {
      multiplier += 0.2;
      reasons.push('off-peak hours');
    }

    if (input.activeCouriers < input.openRescues) {
      multiplier += 0.5;
      reasons.push('courier shortage in zone');
    } else if (input.openRescues > 0 && input.activeCouriers >= 3 * input.openRescues) {
      multiplier -= 0.2;
      reasons.push('zone well covered');
    }

    multiplier = Math.min(KARMA_MULTIPLIER_MAX, Math.max(KARMA_MULTIPLIER_MIN, multiplier));
    return {
      baseKarma: this.baseKarma,
      multiplier: Math.round(multiplier * 100) / 100,
      karma: Math.max(1, Math.round(this.baseKarma * multiplier)),
      reasons,
    };
  }
}
