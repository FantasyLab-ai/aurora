import { describe, expect, it } from 'vitest';
import { PartnerRedemptionService } from './partner-redemption.service.js';
import { InMemoryWalletStore, WalletService } from '../wallet/wallet.service.js';
import {
  DuplicateTransactionError,
  InsufficientBalanceError,
  ValidationError,
} from '../../lib/errors.js';

function makeFixture(karma = 50) {
  const store = new InMemoryWalletStore();
  store.createWallet('courier-1', { karmaCreditBalance: karma });
  const wallets = new WalletService(store);
  const service = new PartnerRedemptionService(wallets);
  service.registerPartner({ partnerId: 'cafe-1', name: 'Corner Cafe', category: 'CAFE' });
  service.addPerk({ perkId: 'coffee', partnerId: 'cafe-1', title: 'Free coffee', costKarma: 20, inventory: 2 });
  return { wallets, service };
}

describe('PartnerRedemptionService', () => {
  it('debits karma, issues a single-use voucher, and tallies partner settlement', async () => {
    const { wallets, service } = makeFixture();

    const voucher = await service.redeem('courier-1', 'coffee', 'r1');
    expect((await wallets.balances('courier-1')).karmaCreditBalance).toBe(30);

    const used = service.markVoucherUsed(voucher.voucherCode);
    expect(used.redeemedAt).toBeInstanceOf(Date);
    expect(() => service.markVoucherUsed(voucher.voucherCode)).toThrow(ValidationError);

    expect(service.partnerSettlement('cafe-1')).toEqual({
      partnerId: 'cafe-1',
      vouchersIssued: 1,
      karmaRedeemed: 20,
    });
  });

  it('a retried redemption request cannot double-charge or double-issue', async () => {
    const { wallets, service } = makeFixture();

    await service.redeem('courier-1', 'coffee', 'r1');
    await expect(service.redeem('courier-1', 'coffee', 'r1')).rejects.toThrow(DuplicateTransactionError);

    expect((await wallets.balances('courier-1')).karmaCreditBalance).toBe(30);
    expect(service.partnerSettlement('cafe-1').vouchersIssued).toBe(1);
  });

  it('refuses redemption without enough karma, leaving inventory intact', async () => {
    const { service } = makeFixture(10);
    await expect(service.redeem('courier-1', 'coffee', 'r1')).rejects.toThrow(InsufficientBalanceError);
    expect(service.listPerks().find((p) => p.perkId === 'coffee')?.inventory).toBe(2);
  });

  it('sells out: out-of-stock perks disappear from the list and reject redemption', async () => {
    const { service } = makeFixture(100);
    await service.redeem('courier-1', 'coffee', 'r1');
    await service.redeem('courier-1', 'coffee', 'r2');

    expect(service.listPerks()).toEqual([]);
    await expect(service.redeem('courier-1', 'coffee', 'r3')).rejects.toThrow(ValidationError);
  });
});
