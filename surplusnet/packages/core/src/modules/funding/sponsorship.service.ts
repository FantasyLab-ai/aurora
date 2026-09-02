import { InsufficientBalanceError, NotFoundError, ValidationError } from '../../lib/errors.js';
import type { CommunityFundService } from './community-fund.service.js';
import type { ImpactAccountingService, ImpactTotals } from '../impact/impact-accounting.service.js';

/**
 * The sponsorship engine — local banks, employers, and brands buy visible,
 * quantified, hyper-local impact:
 *
 * - Direct grants land in the Community Fund under the sponsor's name.
 * - Matching campaigns ("Acme doubles every sale contribution this month,
 *   up to $5,000") hook the fund's contribution stream and auto-match the
 *   community's own purchases — turning every cash sale into a lever.
 * - The karma subsidy pool is real money a sponsor stakes so couriers can
 *   spend Karma Credits at checkout (role fluidity): karma stays non-cash
 *   for the courier, but the supplier is made whole in cash.
 * - The impact meter is the sponsor's receipt: the fridge placard, the
 *   zone banner, the live "meals funded" number — powered by the Impact
 *   Ledger, not marketing copy.
 */

export interface Sponsor {
  sponsorId: string;
  name: string;
}

export interface MatchCampaign {
  campaignId: string;
  sponsorId: string;
  /** "YYYY-MM" the campaign is active in. */
  month: string;
  /** 1 = match 100% of each qualifying contribution. */
  ratio: number;
  capCents: number;
  matchedCents: number;
  /** Zone branding, e.g. "downtown-east" fridge placard. Informational. */
  zoneId?: string;
}

export class SponsorshipService {
  private sponsors = new Map<string, Sponsor>();
  private campaigns = new Map<string, MatchCampaign>();
  private grantsBySponsors = new Map<string, number>();
  private karmaPoolCents = 0;
  private karmaPoolBySponsor = new Map<string, number>();

  constructor(
    private readonly fund: CommunityFundService,
    private readonly impact: ImpactAccountingService,
  ) {
    // Match only organic sale contributions — never grants or other matches,
    // so two campaigns can't chain-react into an infinite loop.
    this.fund.onContribution((record) => {
      if (!record.source.startsWith('sale:')) return;
      for (const campaign of this.campaigns.values()) {
        if (campaign.month !== record.at.toISOString().slice(0, 7)) continue;
        const desired = Math.floor(record.amountCents * campaign.ratio);
        const available = campaign.capCents - campaign.matchedCents;
        const match = Math.min(desired, available);
        if (match <= 0) continue;
        campaign.matchedCents += match;
        this.fund.contribute(`match:${campaign.sponsorId}:${campaign.campaignId}`, match, record.at);
      }
    });
  }

  registerSponsor(sponsor: Sponsor): void {
    if (!sponsor.sponsorId || !sponsor.name.trim()) {
      throw new ValidationError('sponsorId and name are required');
    }
    this.sponsors.set(sponsor.sponsorId, { ...sponsor });
  }

  /** A direct grant into the Community Fund, credited to the sponsor's meter. */
  grant(sponsorId: string, amountCents: number, at: Date = new Date()): void {
    this.requireSponsor(sponsorId);
    this.fund.contribute(`grant:${sponsorId}`, amountCents, at);
    this.grantsBySponsors.set(sponsorId, (this.grantsBySponsors.get(sponsorId) ?? 0) + amountCents);
  }

  createMatchCampaign(input: {
    campaignId: string;
    sponsorId: string;
    month: string;
    ratio: number;
    capCents: number;
    zoneId?: string;
  }): MatchCampaign {
    this.requireSponsor(input.sponsorId);
    if (!/^\d{4}-\d{2}$/.test(input.month)) {
      throw new ValidationError(`month must look like YYYY-MM, got ${input.month}`);
    }
    if (input.ratio <= 0 || input.ratio > 2) {
      throw new ValidationError('ratio must be in (0, 2]');
    }
    if (!Number.isSafeInteger(input.capCents) || input.capCents <= 0) {
      throw new ValidationError('capCents must be a positive integer');
    }
    if (this.campaigns.has(input.campaignId)) {
      throw new ValidationError(`campaign ${input.campaignId} already exists`);
    }
    const campaign: MatchCampaign = {
      campaignId: input.campaignId,
      sponsorId: input.sponsorId,
      month: input.month,
      ratio: input.ratio,
      capCents: input.capCents,
      matchedCents: 0,
      ...(input.zoneId !== undefined ? { zoneId: input.zoneId } : {}),
    };
    this.campaigns.set(campaign.campaignId, campaign);
    return { ...campaign };
  }

  /**
   * The campaign still matching this month, for point-of-purchase display —
   * "Acme is doubling every purchase" converts browsers into buyers, so the
   * matching money must be visible where the buying happens.
   */
  activeMatch(month: string): { sponsorName: string; ratio: number; remainingCents: number } | undefined {
    for (const campaign of this.campaigns.values()) {
      if (campaign.month !== month || campaign.matchedCents >= campaign.capCents) continue;
      const sponsor = this.sponsors.get(campaign.sponsorId);
      return {
        sponsorName: sponsor?.name ?? campaign.sponsorId,
        ratio: campaign.ratio,
        remainingCents: campaign.capCents - campaign.matchedCents,
      };
    }
    return undefined;
  }

  /** Sponsor stakes cash that backs karma spent at checkout. */
  fundKarmaSubsidy(sponsorId: string, amountCents: number): void {
    this.requireSponsor(sponsorId);
    if (!Number.isSafeInteger(amountCents) || amountCents <= 0) {
      throw new ValidationError('amountCents must be a positive integer');
    }
    this.karmaPoolCents += amountCents;
    this.karmaPoolBySponsor.set(sponsorId, (this.karmaPoolBySponsor.get(sponsorId) ?? 0) + amountCents);
  }

  karmaSubsidyAvailableCents(): number {
    return this.karmaPoolCents;
  }

  /**
   * Checkout draws on the pool when karma pays for food: the supplier gets
   * these cents in cash. Throws when the pool can't back the spend — the
   * checkout then rejects karma as a payment method rather than minting
   * unbacked value.
   */
  drawKarmaSubsidy(amountCents: number): void {
    if (!Number.isSafeInteger(amountCents) || amountCents <= 0) {
      throw new ValidationError('amountCents must be a positive integer');
    }
    if (amountCents > this.karmaPoolCents) {
      throw new InsufficientBalanceError(
        `karma subsidy pool has ${this.karmaPoolCents}, cannot draw ${amountCents}`,
      );
    }
    this.karmaPoolCents -= amountCents;
  }

  /** Refund path for raced checkouts. */
  returnKarmaSubsidy(amountCents: number): void {
    if (!Number.isSafeInteger(amountCents) || amountCents <= 0) {
      throw new ValidationError('amountCents must be a positive integer');
    }
    this.karmaPoolCents += amountCents;
  }

  /** The live placard: what this sponsor put in, and the impact around it. */
  impactMeter(sponsorId: string, zoneId?: string): {
    sponsorId: string;
    grantedCents: number;
    matchedCents: number;
    karmaSubsidyCents: number;
    impact: ImpactTotals;
  } {
    this.requireSponsor(sponsorId);
    const matchedCents = [...this.campaigns.values()]
      .filter((c) => c.sponsorId === sponsorId)
      .reduce((s, c) => s + c.matchedCents, 0);
    return {
      sponsorId,
      grantedCents: this.grantsBySponsors.get(sponsorId) ?? 0,
      matchedCents,
      karmaSubsidyCents: this.karmaPoolBySponsor.get(sponsorId) ?? 0,
      impact: this.impact.totals(zoneId !== undefined ? { zoneId } : {}),
    };
  }

  private requireSponsor(sponsorId: string): Sponsor {
    const sponsor = this.sponsors.get(sponsorId);
    if (!sponsor) throw new NotFoundError(`no sponsor ${sponsorId}`);
    return sponsor;
  }
}
