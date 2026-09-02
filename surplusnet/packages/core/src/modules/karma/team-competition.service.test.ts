import { describe, expect, it } from 'vitest';
import { TeamCompetitionService } from './team-competition.service.js';
import { ValidationError } from '../../lib/errors.js';

const sept = (day: number) => new Date(`2026-09-${String(day).padStart(2, '0')}T12:00:00Z`);

describe('TeamCompetitionService', () => {
  it('tallies member rescues per team per month and ranks the leaderboard', () => {
    const service = new TeamCompetitionService();
    service.createTeam('acme', 'Acme Corp', 'downtown');
    service.createTeam('church', 'First Baptist', 'downtown');
    service.join('a1', 'acme');
    service.join('a2', 'acme');
    service.join('c1', 'church');

    service.recordDelivery('a1', sept(1));
    service.recordDelivery('a2', sept(2));
    service.recordDelivery('c1', sept(2));
    service.recordDelivery('c1', sept(3));
    service.recordDelivery('c1', sept(3));
    service.recordDelivery('a1', new Date('2026-10-01T12:00:00Z')); // next month

    const board = service.monthlyLeaderboard('2026-09');
    expect(board[0]).toMatchObject({ rank: 1, teamId: 'church', rescues: 3, activeMembers: 1 });
    expect(board[1]).toMatchObject({ rank: 2, teamId: 'acme', rescues: 2, activeMembers: 2 });
  });

  it('scopes the leaderboard by zone', () => {
    const service = new TeamCompetitionService();
    service.createTeam('down', 'Downtown Crew', 'downtown');
    service.createTeam('up', 'Uptown Crew', 'uptown');
    service.join('d1', 'down');
    service.join('u1', 'up');
    service.recordDelivery('d1', sept(1));
    service.recordDelivery('u1', sept(1));

    const board = service.monthlyLeaderboard('2026-09', 'uptown');
    expect(board.map((r) => r.teamId)).toEqual(['up']);
  });

  it('one team per courier: joining a new team moves the courier', () => {
    const service = new TeamCompetitionService();
    service.createTeam('t1', 'Team One');
    service.createTeam('t2', 'Team Two');
    service.join('x', 't1');
    service.join('x', 't2');

    expect(service.teamOf('x')?.teamId).toBe('t2');
    service.recordDelivery('x', sept(1));
    const board = service.monthlyLeaderboard('2026-09');
    expect(board.find((r) => r.teamId === 't1')?.rescues).toBe(0);
    expect(board.find((r) => r.teamId === 't2')?.rescues).toBe(1);
  });

  it('couriers without a team are a silent no-op, bad months are rejected', () => {
    const service = new TeamCompetitionService();
    service.recordDelivery('loner', sept(1));
    expect(() => service.monthlyLeaderboard('September')).toThrow(ValidationError);
  });
});
