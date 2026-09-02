import { describe, expect, it } from 'vitest';
import { CommunityFundService } from './community-fund.service.js';
import { InMemoryWalletStore, WalletService } from '../wallet/wallet.service.js';
import { InsufficientBalanceError, ValidationError } from '../../lib/errors.js';

function makeFund(recipients: string[] = []) {
  const store = new InMemoryWalletStore();
  recipients.forEach((id) => store.createWallet(id));
  const wallets = new WalletService(store);
  return { fund: new CommunityFundService(wallets), wallets };
}

describe('CommunityFundService', () => {
  it('never mints credits beyond the pool balance', async () => {
    const { fund } = makeFund(['a', 'b', 'c']);
    fund.contribute('grant:city', 1100);

    const result = await fund.allocateMonthly('2026-09', ['a', 'b', 'c'], 500);

    // Pool of 1100 backs two 500-credit allocations; the third is skipped.
    expect(result.funded).toEqual(['a', 'b']);
    expect(result.unfunded).toEqual(['c']);
    expect(fund.state()).toEqual({ poolCents: 1100, outstandingCredits: 1000, headroomCredits: 100 });
  });

  it('is idempotent per recipient per month, and a new month allocates again', async () => {
    const { fund, wallets } = makeFund(['a']);
    fund.contribute('grant:city', 10_000);

    await fund.allocateMonthly('2026-09', ['a'], 500);
    const replay = await fund.allocateMonthly('2026-09', ['a'], 500);
    expect(replay.alreadyAllocated).toEqual(['a']);
    expect((await wallets.balances('a')).communityCreditBalance).toBe(500);

    const october = await fund.allocateMonthly('2026-10', ['a'], 500);
    expect(october.funded).toEqual(['a']);
    expect((await wallets.balances('a')).communityCreditBalance).toBe(1000);
  });

  it('settling spent credits shrinks both the pool and outstanding supply', async () => {
    const { fund } = makeFund(['a']);
    fund.contribute('grant:city', 1000);
    await fund.allocateMonthly('2026-09', ['a'], 600);

    fund.settleSpentCredits(600);
    expect(fund.state()).toEqual({ poolCents: 400, outstandingCredits: 0, headroomCredits: 400 });
  });

  it('refuses to settle more credits than are outstanding', () => {
    const { fund } = makeFund();
    fund.contribute('grant:city', 1000);
    expect(() => fund.settleSpentCredits(1)).toThrow(InsufficientBalanceError);
  });

  it('validates contributions and month format', () => {
    const { fund } = makeFund();
    expect(() => fund.contribute('x', 0)).toThrow(ValidationError);
    expect(() => fund.contribute('x', -5)).toThrow(ValidationError);
    return expect(fund.allocateMonthly('Sept 2026', [], 100)).rejects.toThrow(ValidationError);
  });
});
