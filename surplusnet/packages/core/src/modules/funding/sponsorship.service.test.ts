import { describe, expect, it } from 'vitest';
import { SponsorshipService } from './sponsorship.service.js';
import { CommunityFundService } from './community-fund.service.js';
import { ImpactAccountingService } from '../impact/impact-accounting.service.js';
import { DonationLedger, InMemoryLedgerStore } from '../tax/donation-ledger.js';
import { InMemoryWalletStore, WalletService } from '../wallet/wallet.service.js';
import { InsufficientBalanceError, NotFoundError } from '../../lib/errors.js';

function makeFixture() {
  const wallets = new WalletService(new InMemoryWalletStore());
  const fund = new CommunityFundService(wallets);
  const impact = new ImpactAccountingService(new DonationLedger(new InMemoryLedgerStore()));
  const sponsorship = new SponsorshipService(fund, impact);
  sponsorship.registerSponsor({ sponsorId: 'acme-bank', name: 'Acme Community Bank' });
  return { fund, sponsorship };
}

describe('SponsorshipService', () => {
  it('matching campaigns double sale contributions up to the cap, never grants', () => {
    const { fund, sponsorship } = makeFixture();
    sponsorship.createMatchCampaign({
      campaignId: 'sept-match',
      sponsorId: 'acme-bank',
      month: '2026-09',
      ratio: 1,
      capCents: 500,
    });

    const at = new Date('2026-09-10T12:00:00Z');
    fund.contribute('sale:o1', 300, at); // matched 300
    fund.contribute('grant:city', 1000, at); // grants are never matched
    fund.contribute('sale:o2', 400, at); // cap leaves only 200

    // 300 + 300 + 1000 + 400 + 200
    expect(fund.state().poolCents).toBe(2200);
    expect(sponsorship.impactMeter('acme-bank').matchedCents).toBe(500);
  });

  it('campaigns only match inside their month', () => {
    const { fund, sponsorship } = makeFixture();
    sponsorship.createMatchCampaign({
      campaignId: 'sept-match',
      sponsorId: 'acme-bank',
      month: '2026-09',
      ratio: 1,
      capCents: 10_000,
    });

    fund.contribute('sale:o1', 300, new Date('2026-10-01T12:00:00Z'));
    expect(fund.state().poolCents).toBe(300);
  });

  it('the karma subsidy pool draws and returns exactly, and never overdraws', () => {
    const { sponsorship } = makeFixture();
    sponsorship.fundKarmaSubsidy('acme-bank', 100);

    sponsorship.drawKarmaSubsidy(60);
    expect(sponsorship.karmaSubsidyAvailableCents()).toBe(40);
    expect(() => sponsorship.drawKarmaSubsidy(50)).toThrow(InsufficientBalanceError);

    sponsorship.returnKarmaSubsidy(60);
    expect(sponsorship.karmaSubsidyAvailableCents()).toBe(100);
  });

  it('the impact meter reports grants, matches, and subsidies per sponsor', () => {
    const { sponsorship } = makeFixture();
    sponsorship.grant('acme-bank', 5000);
    sponsorship.fundKarmaSubsidy('acme-bank', 2000);

    const meter = sponsorship.impactMeter('acme-bank');
    expect(meter).toMatchObject({ grantedCents: 5000, matchedCents: 0, karmaSubsidyCents: 2000 });
    expect(() => sponsorship.impactMeter('ghost')).toThrow(NotFoundError);
  });
});
