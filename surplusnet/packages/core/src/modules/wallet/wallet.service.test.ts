import { describe, expect, it } from 'vitest';
import { InMemoryWalletStore, WalletService } from './wallet.service.js';
import { InsufficientBalanceError, NotFoundError, ValidationError } from '../../lib/errors.js';

function makeWallet(initial = {}) {
  const store = new InMemoryWalletStore();
  store.createWallet('user-1', initial);
  return { store, service: new WalletService(store) };
}

describe('WalletService', () => {
  it('credits and debits per token kind independently', async () => {
    const { service } = makeWallet({ cashBalanceCents: 1000 });

    await service.credit('user-1', 'COMMUNITY_CREDIT', 500, 'monthly allocation', 'alloc-2026-09');
    await service.debit('user-1', 'CASH', 250, 'blind box purchase', 'order-1');

    expect(await service.balances('user-1')).toEqual({
      cashBalanceCents: 750,
      karmaCreditBalance: 0,
      communityCreditBalance: 500,
    });
  });

  it('rejects overdrafts on any token kind', async () => {
    const { service } = makeWallet({ communityCreditBalance: 100 });
    await expect(
      service.debit('user-1', 'COMMUNITY_CREDIT', 101, 'checkout', 'order-2'),
    ).rejects.toThrow(InsufficientBalanceError);
    // Balance untouched after the failed debit
    expect((await service.balances('user-1')).communityCreditBalance).toBe(100);
  });

  it('never double-mints karma credits for the same delivery', async () => {
    const { service } = makeWallet();

    const first = await service.mintKarmaForDelivery('user-1', 'delivery-42', 10);
    const replay = await service.mintKarmaForDelivery('user-1', 'delivery-42', 10);

    expect(first).toBe(true);
    expect(replay).toBe(false);
    expect((await service.balances('user-1')).karmaCreditBalance).toBe(10);
  });

  it('rejects non-positive and non-integer amounts', async () => {
    const { service } = makeWallet();
    await expect(service.credit('user-1', 'CASH', 0, 'x', 'k1')).rejects.toThrow(ValidationError);
    await expect(service.credit('user-1', 'CASH', -5, 'x', 'k2')).rejects.toThrow(ValidationError);
    await expect(service.credit('user-1', 'CASH', 1.5, 'x', 'k3')).rejects.toThrow(ValidationError);
  });

  it('fails clearly for unknown users', async () => {
    const { service } = makeWallet();
    await expect(service.balances('ghost')).rejects.toThrow(NotFoundError);
  });
});
