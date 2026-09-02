import { describe, expect, it } from 'vitest';
import { ReferralService } from './referral.service.js';
import { CommunityFundService } from '../funding/community-fund.service.js';
import { InMemoryWalletStore, WalletService } from '../wallet/wallet.service.js';
import { ValidationError } from '../../lib/errors.js';

function makeFixture(poolCents = 0) {
  const store = new InMemoryWalletStore();
  store.createWallet('courier-ref', { karmaCreditBalance: 0 });
  store.createWallet('recipient-ref');
  const wallets = new WalletService(store);
  const fund = new CommunityFundService(wallets);
  if (poolCents > 0) fund.contribute('grant:city', poolCents);
  const referrals = new ReferralService(wallets, {
    tryMint: (id, credits, reason, key) => fund.tryMintBacked(id, credits, reason, key),
  });
  return { wallets, fund, referrals };
}

describe('ReferralService', () => {
  it('pays a courier referrer in karma, only on the first qualification', async () => {
    const { wallets, referrals } = makeFixture();
    referrals.register('courier-ref', 'COURIER', 'newbie');

    expect(await referrals.qualify('newbie')).toBe('20 karma');
    expect(await referrals.qualify('newbie')).toBeUndefined();
    expect((await wallets.balances('courier-ref')).karmaCreditBalance).toBe(20);
  });

  it('pays a recipient referrer in backed credits, or skips when the pool is dry', async () => {
    const funded = makeFixture(1000);
    funded.referrals.register('recipient-ref', 'RECIPIENT', 'newbie');
    expect(await funded.referrals.qualify('newbie')).toBe('300 community credits');
    expect((await funded.wallets.balances('recipient-ref')).communityCreditBalance).toBe(300);

    const dry = makeFixture(0);
    dry.referrals.register('recipient-ref', 'RECIPIENT', 'newbie');
    expect(await dry.referrals.qualify('newbie')).toBe('skipped: community fund lacks headroom');
    expect((await dry.wallets.balances('recipient-ref')).communityCreditBalance).toBe(0);
  });

  it('gives supplier referrers featured placement that stacks', async () => {
    const { referrals } = makeFixture();
    const now = new Date('2026-09-01T00:00:00Z');
    referrals.register('grocer-1', 'SUPPLIER', 'newbie-1');
    referrals.register('grocer-1', 'SUPPLIER', 'newbie-2');

    await referrals.qualify('newbie-1', now);
    await referrals.qualify('newbie-2', now);

    expect(referrals.isFeatured('grocer-1', new Date('2026-09-13T00:00:00Z'))).toBe(true); // 14 days stacked
    expect(referrals.isFeatured('grocer-1', new Date('2026-09-16T00:00:00Z'))).toBe(false);
  });

  it('blocks self-referrals and double referrer claims on one user', () => {
    const { referrals } = makeFixture();
    expect(() => referrals.register('x', 'COURIER', 'x')).toThrow(ValidationError);
    referrals.register('a', 'COURIER', 'newbie');
    expect(() => referrals.register('b', 'COURIER', 'newbie')).toThrow(ValidationError);
  });
});
