import { NotFoundError, ValidationError } from '../../lib/errors.js';

/**
 * Team competitions — the retention layer for the other 95%.
 *
 * Individual leaderboards motivate the top few; social identity motivates
 * everyone else. Workplaces, schools, congregations, and blocks form teams
 * (optionally zone-scoped), every member's verified rescue counts for the
 * team, and the monthly zone leaderboard is the neighborhood scoreboard a
 * city or sponsor can put a prize behind.
 */

export interface Team {
  teamId: string;
  name: string;
  zoneId?: string;
  memberIds: string[];
}

export interface TeamStanding {
  rank: number;
  teamId: string;
  name: string;
  zoneId?: string;
  rescues: number;
  activeMembers: number;
}

export class TeamCompetitionService {
  private teams = new Map<string, Team>();
  private memberTeam = new Map<string, string>();
  /** month ("YYYY-MM") → teamId → courierId → rescues */
  private tallies = new Map<string, Map<string, Map<string, number>>>();

  createTeam(teamId: string, name: string, zoneId?: string): Team {
    if (!teamId || !name.trim()) throw new ValidationError('teamId and name are required');
    if (this.teams.has(teamId)) throw new ValidationError(`team ${teamId} already exists`);
    const team: Team = { teamId, name: name.trim(), ...(zoneId !== undefined ? { zoneId } : {}), memberIds: [] };
    this.teams.set(teamId, team);
    return { ...team, memberIds: [] };
  }

  /** One team per courier; joining a new team leaves the old one. */
  join(courierId: string, teamId: string): void {
    const team = this.teams.get(teamId);
    if (!team) throw new NotFoundError(`no team ${teamId}`);
    const current = this.memberTeam.get(courierId);
    if (current) {
      const old = this.teams.get(current);
      if (old) old.memberIds = old.memberIds.filter((id) => id !== courierId);
    }
    this.memberTeam.set(courierId, teamId);
    team.memberIds.push(courierId);
  }

  teamOf(courierId: string): Team | undefined {
    const teamId = this.memberTeam.get(courierId);
    const team = teamId ? this.teams.get(teamId) : undefined;
    return team ? { ...team, memberIds: [...team.memberIds] } : undefined;
  }

  /** Called on each verified rescue; couriers without a team are a no-op. */
  recordDelivery(courierId: string, at: Date): void {
    const teamId = this.memberTeam.get(courierId);
    if (!teamId) return;
    const month = at.toISOString().slice(0, 7);
    let byTeam = this.tallies.get(month);
    if (!byTeam) {
      byTeam = new Map();
      this.tallies.set(month, byTeam);
    }
    let byCourier = byTeam.get(teamId);
    if (!byCourier) {
      byCourier = new Map();
      byTeam.set(teamId, byCourier);
    }
    byCourier.set(courierId, (byCourier.get(courierId) ?? 0) + 1);
  }

  monthlyLeaderboard(month: string, zoneId?: string, limit = 10): TeamStanding[] {
    if (!/^\d{4}-\d{2}$/.test(month)) {
      throw new ValidationError(`month must look like YYYY-MM, got ${month}`);
    }
    const byTeam = this.tallies.get(month) ?? new Map<string, Map<string, number>>();

    return [...this.teams.values()]
      .filter((team) => zoneId === undefined || team.zoneId === zoneId)
      .map((team) => {
        const byCourier = byTeam.get(team.teamId);
        const rescues = byCourier ? [...byCourier.values()].reduce((s, n) => s + n, 0) : 0;
        return {
          teamId: team.teamId,
          name: team.name,
          ...(team.zoneId !== undefined ? { zoneId: team.zoneId } : {}),
          rescues,
          activeMembers: byCourier?.size ?? 0,
        };
      })
      .sort((a, b) => b.rescues - a.rescues || b.activeMembers - a.activeMembers)
      .slice(0, limit)
      .map((row, i) => ({ rank: i + 1, ...row }));
  }
}
