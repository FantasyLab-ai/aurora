import { describe, expect, it } from 'vitest';
import { DonationLedger, GENESIS_HASH, InMemoryLedgerStore } from './donation-ledger.js';

describe('DonationLedger', () => {
  it('chains each entry to the previous hash', async () => {
    const store = new InMemoryLedgerStore();
    const ledger = new DonationLedger(store);

    const first = await ledger.record('item-1', 'DONATION_RECORDED', { a: 1 });
    const second = await ledger.record('item-1', 'TAX_DEDUCTION_CALCULATED', { b: 2 });

    expect(first.sequence).toBe(1);
    expect(first.prevHash).toBe(GENESIS_HASH);
    expect(second.sequence).toBe(2);
    expect(second.prevHash).toBe(first.entryHash);
    expect(await ledger.verifyChain()).toEqual([]);
  });

  it('detects tampering with a recorded payload', async () => {
    const store = new InMemoryLedgerStore();
    const ledger = new DonationLedger(store);
    await ledger.record('item-1', 'DONATION_RECORDED', { fmvCents: 1000 });
    await ledger.record('item-1', 'TAX_DEDUCTION_CALCULATED', { deductionCents: 700 });

    const entries = await store.all();
    // Simulate someone inflating a recorded deduction after the fact
    (entries[1]!.payload as Record<string, unknown>)['deductionCents'] = 999_900;
    const tamperedStore = new InMemoryLedgerStore();
    for (const e of entries) await tamperedStore.append(e);

    const tamperedLedger = new DonationLedger(tamperedStore);
    expect(await tamperedLedger.verifyChain()).toEqual([2]);
  });
});
