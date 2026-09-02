import { createHash } from 'node:crypto';
import type { Clock } from '../../lib/clock.js';
import { systemClock } from '../../lib/clock.js';
import { ValidationError } from '../../lib/errors.js';

/**
 * Append-only donation ledger. Each entry's hash commits to the previous
 * entry's hash plus this entry's payload, so any retroactive edit to a row
 * breaks the chain and is detectable by `verifyChain`. This backs the
 * audit-ready monthly export: accountants get records that provably were
 * not massaged after the fact.
 */

export type LedgerEntryKind =
  | 'DONATION_RECORDED'
  | 'TAX_DEDUCTION_CALCULATED'
  | 'DELIVERY_VERIFIED';

export interface LedgerEntry {
  sequence: number;
  surplusItemId: string;
  kind: LedgerEntryKind;
  payload: Record<string, unknown>;
  prevHash: string;
  entryHash: string;
  recordedAt: Date;
}

export interface LedgerStore {
  /** Latest entry by sequence, or undefined for an empty ledger. */
  last(): Promise<LedgerEntry | undefined>;
  append(entry: LedgerEntry): Promise<void>;
  /** All entries ordered by sequence ascending. */
  all(): Promise<LedgerEntry[]>;
}

export const GENESIS_HASH = '0'.repeat(64);

function hashEntry(entry: Omit<LedgerEntry, 'entryHash'>): string {
  const material = JSON.stringify([
    entry.sequence,
    entry.surplusItemId,
    entry.kind,
    entry.payload,
    entry.prevHash,
    entry.recordedAt.toISOString(),
  ]);
  return createHash('sha256').update(material).digest('hex');
}

export class DonationLedger {
  constructor(
    private readonly store: LedgerStore,
    private readonly clock: Clock = systemClock,
  ) {}

  async record(
    surplusItemId: string,
    kind: LedgerEntryKind,
    payload: Record<string, unknown>,
  ): Promise<LedgerEntry> {
    if (!surplusItemId) throw new ValidationError('surplusItemId is required');

    const last = await this.store.last();
    const partial = {
      sequence: (last?.sequence ?? 0) + 1,
      surplusItemId,
      kind,
      payload,
      prevHash: last?.entryHash ?? GENESIS_HASH,
      recordedAt: this.clock.now(),
    };
    const entry: LedgerEntry = { ...partial, entryHash: hashEntry(partial) };
    await this.store.append(entry);
    return entry;
  }

  /** Returns the sequence numbers of entries whose hashes no longer verify. */
  async verifyChain(): Promise<number[]> {
    const entries = await this.store.all();
    const broken: number[] = [];
    let prevHash = GENESIS_HASH;
    for (const entry of entries) {
      const { entryHash, ...rest } = entry;
      if (entry.prevHash !== prevHash || hashEntry(rest) !== entryHash) {
        broken.push(entry.sequence);
      }
      prevHash = entryHash;
    }
    return broken;
  }
}

export class InMemoryLedgerStore implements LedgerStore {
  private entries: LedgerEntry[] = [];

  async last(): Promise<LedgerEntry | undefined> {
    return this.entries[this.entries.length - 1];
  }

  async append(entry: LedgerEntry): Promise<void> {
    this.entries.push(entry);
  }

  async all(): Promise<LedgerEntry[]> {
    return [...this.entries];
  }
}
