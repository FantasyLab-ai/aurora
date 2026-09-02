import { describe, expect, it } from 'vitest';
import { CertificationService } from './certification.service.js';
import { EngagementService } from './engagement.service.js';
import { InMemoryWalletStore, WalletService } from '../wallet/wallet.service.js';
import { NotFoundError, ValidationError } from '../../lib/errors.js';

function makeFixture() {
  const store = new InMemoryWalletStore();
  store.createWallet('courier-1');
  const wallets = new WalletService(store);
  const engagement = new EngagementService();
  return { wallets, engagement, service: new CertificationService(wallets, engagement) };
}

describe('CertificationService', () => {
  it('pays the karma bonus and awards the badge, one time only', async () => {
    const { wallets, engagement, service } = makeFixture();

    const result = await service.complete('courier-1', 'food-handler-101');
    expect(result).toEqual({ badge: 'cert:food-handler-101', karmaBonus: 15 });
    expect((await wallets.balances('courier-1')).karmaCreditBalance).toBe(15);
    expect(engagement.engagement('courier-1')?.badges).toContain('cert:food-handler-101');
    expect(service.isCertified('courier-1', 'food-handler-101')).toBe(true);

    await expect(service.complete('courier-1', 'food-handler-101')).rejects.toThrow(ValidationError);
    expect((await wallets.balances('courier-1')).karmaCreditBalance).toBe(15);
  });

  it('rejects unknown courses', async () => {
    const { service } = makeFixture();
    await expect(service.complete('courier-1', 'nonsense')).rejects.toThrow(NotFoundError);
  });
});
