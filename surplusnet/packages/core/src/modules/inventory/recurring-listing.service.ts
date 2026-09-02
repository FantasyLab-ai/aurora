import type { Clock } from '../../lib/clock.js';
import { systemClock } from '../../lib/clock.js';
import { NotFoundError, ValidationError } from '../../lib/errors.js';
import type { SurplusItemService } from './surplus-item.service.js';
import type { SurplusItem } from '../../domain/types.js';

/**
 * Standing surplus schedules — the fix for the #1 real supplier barrier.
 *
 * Research on why businesses don't donate is unambiguous: liability fear is
 * the stated reason, but the operative one is that disposal is the path of
 * least resistance — donating costs labor *today*, the tax benefit lands
 * *next April*. So donating has to become MORE automatic than the dumpster.
 *
 * A supplier describes their rhythm once ("the bakery closes at 21:00, about
 * one assortment box left, most days") and the engine lists it every
 * scheduled day with no further action. Staff put the box in the bin at
 * close — the same motion as throwing it away, pointed somewhere better.
 * Standing schedules are also what make supply *predictable*, which the
 * courier network and pantries need far more than sporadic windfalls
 * (80%+ of retail surplus is fresh; predictability is the cold chain's
 * best friend).
 *
 * A day's auto-listing is skipped, not failed, when the supplier marks
 * "nothing left today" before the fire time.
 */

export interface RecurringSchedule {
  scheduleId: string;
  supplierId: string;
  title: string;
  category: string;
  quantity: number;
  weightGrams?: number;
  zoneId?: string;
  dietaryTags?: string[];
  fmvCents: number;
  cogsCents: number;
  salePriceCents?: number;
  latitude: number;
  longitude: number;
  /** UTC hour (0-23) the listing fires each active day. */
  listAtHourUtc: number;
  /** Hours after listing until the food is unsafe. */
  safeForHours: number;
  salesWindowMinutes?: number;
  /** 0 (Sunday) .. 6 (Saturday); defaults to every day. */
  daysOfWeek?: number[];
  paused: boolean;
}

export type RecurringScheduleInput = Omit<RecurringSchedule, 'paused'>;

export class RecurringListingService {
  private schedules = new Map<string, RecurringSchedule>();
  /** scheduleId → "YYYY-MM-DD" last handled (listed or skipped). */
  private lastFired = new Map<string, string>();
  private skips = new Map<string, Set<string>>();

  constructor(
    private readonly items: SurplusItemService,
    private readonly clock: Clock = systemClock,
  ) {}

  addSchedule(input: RecurringScheduleInput): RecurringSchedule {
    if (this.schedules.has(input.scheduleId)) {
      throw new ValidationError(`schedule ${input.scheduleId} already exists`);
    }
    if (input.listAtHourUtc < 0 || input.listAtHourUtc > 23) {
      throw new ValidationError('listAtHourUtc must be 0-23');
    }
    if (input.safeForHours <= 0) {
      throw new ValidationError('safeForHours must be positive');
    }
    if (input.daysOfWeek?.some((d) => d < 0 || d > 6)) {
      throw new ValidationError('daysOfWeek entries must be 0-6');
    }
    const schedule: RecurringSchedule = { ...input, paused: false };
    this.schedules.set(schedule.scheduleId, schedule);
    return { ...schedule };
  }

  pause(scheduleId: string): void {
    this.require(scheduleId).paused = true;
  }

  resume(scheduleId: string): void {
    this.require(scheduleId).paused = false;
  }

  /** "Nothing left today" — skips today's firing without touching the schedule. */
  skipToday(scheduleId: string): void {
    this.require(scheduleId);
    const day = this.clock.now().toISOString().slice(0, 10);
    const set = this.skips.get(scheduleId) ?? new Set<string>();
    set.add(day);
    this.skips.set(scheduleId, set);
  }

  schedulesOf(supplierId: string): RecurringSchedule[] {
    return [...this.schedules.values()]
      .filter((s) => s.supplierId === supplierId)
      .map((s) => ({ ...s }));
  }

  /**
   * One sweep: fires every schedule whose time has come today. Idempotent
   * per schedule per day — run it from cron as often as you like.
   */
  async tick(): Promise<SurplusItem[]> {
    const now = this.clock.now();
    const day = now.toISOString().slice(0, 10);
    const listed: SurplusItem[] = [];

    for (const schedule of this.schedules.values()) {
      if (schedule.paused) continue;
      if (this.lastFired.get(schedule.scheduleId) === day) continue;
      const activeDays = schedule.daysOfWeek ?? [0, 1, 2, 3, 4, 5, 6];
      if (!activeDays.includes(now.getUTCDay())) continue;
      if (now.getUTCHours() < schedule.listAtHourUtc) continue;

      this.lastFired.set(schedule.scheduleId, day);
      if (this.skips.get(schedule.scheduleId)?.has(day)) continue;

      const item = await this.items.listSurplus({
        supplierId: schedule.supplierId,
        title: schedule.title,
        category: schedule.category,
        quantity: schedule.quantity,
        ...(schedule.weightGrams !== undefined ? { weightGrams: schedule.weightGrams } : {}),
        ...(schedule.zoneId !== undefined ? { zoneId: schedule.zoneId } : {}),
        ...(schedule.dietaryTags !== undefined ? { dietaryTags: schedule.dietaryTags } : {}),
        fmvCents: schedule.fmvCents,
        cogsCents: schedule.cogsCents,
        ...(schedule.salePriceCents !== undefined ? { salePriceCents: schedule.salePriceCents } : {}),
        latitude: schedule.latitude,
        longitude: schedule.longitude,
        safeUntil: new Date(now.getTime() + schedule.safeForHours * 3_600_000),
        ...(schedule.salesWindowMinutes !== undefined
          ? { salesWindowMinutes: schedule.salesWindowMinutes }
          : {}),
      });
      listed.push(item);
    }
    return listed;
  }

  private require(scheduleId: string): RecurringSchedule {
    const schedule = this.schedules.get(scheduleId);
    if (!schedule) throw new NotFoundError(`no schedule ${scheduleId}`);
    return schedule;
  }
}
