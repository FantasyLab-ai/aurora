import { describe, expect, it } from 'vitest';
import { EngagementService } from './engagement.service.js';

const day = (d: string) => new Date(`${d}T12:00:00Z`);

describe('EngagementService', () => {
  it('builds streaks from consecutive days and resets after a gap', () => {
    const service = new EngagementService();

    service.recordDelivery('c1', 'd1', day('2026-09-01'));
    service.recordDelivery('c1', 'd2', day('2026-09-02'));
    service.recordDelivery('c1', 'd3', day('2026-09-02')); // same day: total up, streak flat
    service.recordDelivery('c1', 'd4', day('2026-09-03'));
    expect(service.engagement('c1')).toMatchObject({
      totalDeliveries: 4,
      currentStreakDays: 3,
      longestStreakDays: 3,
    });

    service.recordDelivery('c1', 'd5', day('2026-09-07')); // missed 3 days
    expect(service.engagement('c1')).toMatchObject({
      currentStreakDays: 1,
      longestStreakDays: 3,
    });
  });

  it('awards milestone badges exactly once', () => {
    const service = new EngagementService();

    const first = service.recordDelivery('c1', 'd1', day('2026-09-01'));
    expect(first).toEqual(['first-rescue']);

    let latest: string[] = [];
    for (let i = 2; i <= 10; i++) {
      latest = service.recordDelivery('c1', `d${i}`, day('2026-09-01'));
    }
    expect(latest).toEqual(['block-hero']);
    expect(service.engagement('c1')?.badges).toEqual(['first-rescue', 'block-hero']);
  });

  it('ranks the leaderboard by rescues, then streak', () => {
    const service = new EngagementService();
    service.recordDelivery('a', 'a1', day('2026-09-01'));
    service.recordDelivery('a', 'a2', day('2026-09-02'));
    service.recordDelivery('b', 'b1', day('2026-09-01'));
    service.recordDelivery('b', 'b2', day('2026-09-01'));
    service.recordDelivery('c', 'c1', day('2026-09-02'));

    const board = service.leaderboard();
    // a and b both have 2 rescues; a's 2-day streak beats b's 1-day streak
    expect(board.map((r) => r.courierId)).toEqual(['a', 'b', 'c']);
    expect(board[0]).toMatchObject({ rank: 1, currentStreakDays: 2 });
  });

  it('logs verified volunteer minutes per employer per month', () => {
    const service = new EngagementService({ minutesPerDelivery: 15 });
    service.linkEmployer('c1', 'acme');
    service.linkEmployer('c2', 'acme');

    service.recordDelivery('c1', 'd1', day('2026-09-01'));
    service.recordDelivery('c1', 'd2', day('2026-09-02'));
    service.recordDelivery('c2', 'd3', day('2026-09-02'));
    service.recordDelivery('c1', 'd4', day('2026-10-01')); // next month
    service.recordDelivery('c3', 'd5', day('2026-09-02')); // not enrolled

    expect(service.monthlyVolunteerReport('acme', 2026, 9)).toEqual([
      { courierId: 'c1', deliveries: 2, minutes: 30 },
      { courierId: 'c2', deliveries: 1, minutes: 15 },
    ]);
  });
});
