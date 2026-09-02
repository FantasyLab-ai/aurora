import type { TokenKind, WalletBalances } from '../../domain/types.js';
import {
  DuplicateTransactionError,
  InsufficientBalanceError,
  NotFoundError,
  ValidationError,
} from '../../lib/errors.js';

/**
 * Sovereign tokenized wallet (Epic 2) + Karma ledger (Epic 3).
 *
 * Three token kinds share one wallet so checkout treats cash and credits
 * identically — the dignity-first requirement. Every mutation goes through
 * `apply`, which enforces:
 *   - idempotency (a delivery can never double-mint Karma Credits),
 *   - non-negative balances (no overdrafts on any token kind).
 *
 * The production implementation maps `apply` onto a single Prisma
 * interactive transaction (SERIALIZABLE), with the idempotency key backed by
 * the unique constraint on WalletTransaction.idempotencyKey — the invariants
 * here are the same ones the database enforces.
 */

export interface WalletRecord extends WalletBalances {
  walletId: string;
  userId: string;
}

export interface WalletTransactionRecord {
  walletId: string;
  kind: TokenKind;
  amount: number;
  reason: string;
  idempotencyKey: string;
  createdAt: Date;
}

export interface WalletStore {
  findByUserId(userId: string): Promise<WalletRecord | undefined>;
  /** Creates the user's wallet (used by onboarding); overwrites nothing when guarded by findByUserId first. */
  createWallet(userId: string, initial?: Partial<WalletBalances>): WalletRecord | Promise<WalletRecord>;
  /**
   * Atomically persist the balance change and the transaction row.
   * Must reject with DuplicateTransactionError on an idempotency-key replay.
   */
  applyTransaction(updated: WalletRecord, tx: WalletTransactionRecord): Promise<void>;
}

const BALANCE_FIELD: Record<TokenKind, keyof WalletBalances> = {
  CASH: 'cashBalanceCents',
  KARMA_CREDIT: 'karmaCreditBalance',
  COMMUNITY_CREDIT: 'communityCreditBalance',
};

export class WalletService {
  constructor(private readonly store: WalletStore) {}

  async balances(userId: string): Promise<WalletBalances> {
    const wallet = await this.requireWallet(userId);
    return {
      cashBalanceCents: wallet.cashBalanceCents,
      karmaCreditBalance: wallet.karmaCreditBalance,
      communityCreditBalance: wallet.communityCreditBalance,
    };
  }

  /** Positive amount credit — e.g. minting Karma Credits after a verified drop-off. */
  async credit(userId: string, kind: TokenKind, amount: number, reason: string, idempotencyKey: string): Promise<void> {
    if (!Number.isSafeInteger(amount) || amount <= 0) {
      throw new ValidationError(`credit amount must be a positive integer, got ${amount}`);
    }
    await this.apply(userId, kind, amount, reason, idempotencyKey);
  }

  /** Positive amount debit — e.g. spending Community Credits at checkout. */
  async debit(userId: string, kind: TokenKind, amount: number, reason: string, idempotencyKey: string): Promise<void> {
    if (!Number.isSafeInteger(amount) || amount <= 0) {
      throw new ValidationError(`debit amount must be a positive integer, got ${amount}`);
    }
    await this.apply(userId, kind, -amount, reason, idempotencyKey);
  }

  /**
   * Karma minting hook for the delivery pipeline: idempotency key is derived
   * from the delivery id, so replayed `delivery.completed` events are no-ops.
   */
  async mintKarmaForDelivery(courierId: string, deliveryId: string, credits: number): Promise<boolean> {
    try {
      await this.credit(courierId, 'KARMA_CREDIT', credits, `delivery:${deliveryId}`, `karma-${deliveryId}`);
      return true;
    } catch (err) {
      if (err instanceof DuplicateTransactionError) return false;
      throw err;
    }
  }

  private async requireWallet(userId: string): Promise<WalletRecord> {
    const wallet = await this.store.findByUserId(userId);
    if (!wallet) throw new NotFoundError(`no wallet for user ${userId}`);
    return wallet;
  }

  private async apply(
    userId: string,
    kind: TokenKind,
    signedAmount: number,
    reason: string,
    idempotencyKey: string,
  ): Promise<void> {
    if (!idempotencyKey) throw new ValidationError('idempotencyKey is required');
    const wallet = await this.requireWallet(userId);

    const field = BALANCE_FIELD[kind];
    const next = wallet[field] + signedAmount;
    if (next < 0) {
      throw new InsufficientBalanceError(
        `${kind} balance ${wallet[field]} cannot cover ${-signedAmount}`,
      );
    }

    await this.store.applyTransaction(
      { ...wallet, [field]: next },
      {
        walletId: wallet.walletId,
        kind,
        amount: signedAmount,
        reason,
        idempotencyKey,
        createdAt: new Date(),
      },
    );
  }
}

export class InMemoryWalletStore implements WalletStore {
  private wallets = new Map<string, WalletRecord>();
  private usedKeys = new Set<string>();
  readonly transactions: WalletTransactionRecord[] = [];

  createWallet(userId: string, initial: Partial<WalletBalances> = {}): WalletRecord {
    const record: WalletRecord = {
      walletId: `wallet-${userId}`,
      userId,
      cashBalanceCents: initial.cashBalanceCents ?? 0,
      karmaCreditBalance: initial.karmaCreditBalance ?? 0,
      communityCreditBalance: initial.communityCreditBalance ?? 0,
    };
    this.wallets.set(userId, record);
    return record;
  }

  async findByUserId(userId: string): Promise<WalletRecord | undefined> {
    const wallet = this.wallets.get(userId);
    return wallet ? { ...wallet } : undefined;
  }

  async applyTransaction(updated: WalletRecord, tx: WalletTransactionRecord): Promise<void> {
    if (this.usedKeys.has(tx.idempotencyKey)) {
      throw new DuplicateTransactionError(`idempotency key already used: ${tx.idempotencyKey}`);
    }
    this.usedKeys.add(tx.idempotencyKey);
    this.wallets.set(updated.userId, { ...updated });
    this.transactions.push(tx);
  }
}
