/** Injectable clock so time-driven logic (rollover, staleness) is testable. */
export interface Clock {
  now(): Date;
}

export const systemClock: Clock = {
  now: () => new Date(),
};
