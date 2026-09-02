import type { SurplusItem, SurplusItemState } from '../../domain/types.js';

/**
 * Persistence boundary for surplus items. Business logic depends on this
 * interface; a Prisma-backed implementation satisfies it in production and
 * the in-memory implementation below backs the unit tests.
 */
export interface SurplusItemRepository {
  create(item: SurplusItem): Promise<SurplusItem>;
  findById(id: string): Promise<SurplusItem | undefined>;
  findBySupplier(supplierId: string): Promise<SurplusItem[]>;
  findInState(state: SurplusItemState): Promise<SurplusItem[]>;
  findByRecipient(recipientId: string): Promise<SurplusItem[]>;
  /**
   * Items still in SALES_PHASE whose sales window elapsed at or before `now`.
   */
  findExpiredSalesPhase(now: Date): Promise<SurplusItem[]>;
  /**
   * Compare-and-set state transition. Returns the updated item, or undefined
   * when the item is no longer in `fromState` (lost race) — callers must
   * treat that as "someone else already handled it", not an error.
   */
  transitionState(
    id: string,
    fromState: SurplusItemState,
    toState: SurplusItemState,
    patch?: Partial<SurplusItem>,
  ): Promise<SurplusItem | undefined>;
}

export class InMemorySurplusItemRepository implements SurplusItemRepository {
  private items = new Map<string, SurplusItem>();

  async create(item: SurplusItem): Promise<SurplusItem> {
    this.items.set(item.id, { ...item });
    return { ...item };
  }

  async findById(id: string): Promise<SurplusItem | undefined> {
    const item = this.items.get(id);
    return item ? { ...item } : undefined;
  }

  async findBySupplier(supplierId: string): Promise<SurplusItem[]> {
    return [...this.items.values()]
      .filter((item) => item.supplierId === supplierId)
      .map((item) => ({ ...item }));
  }

  async findInState(state: SurplusItemState): Promise<SurplusItem[]> {
    return [...this.items.values()]
      .filter((item) => item.currentState === state)
      .map((item) => ({ ...item }));
  }

  async findByRecipient(recipientId: string): Promise<SurplusItem[]> {
    return [...this.items.values()]
      .filter((item) => item.recipientId === recipientId)
      .map((item) => ({ ...item }));
  }

  async findExpiredSalesPhase(now: Date): Promise<SurplusItem[]> {
    return [...this.items.values()]
      .filter(
        (item) =>
          item.currentState === 'SALES_PHASE' &&
          item.listedAt.getTime() + item.salesWindowMinutes * 60_000 <= now.getTime(),
      )
      .map((item) => ({ ...item }));
  }

  async transitionState(
    id: string,
    fromState: SurplusItemState,
    toState: SurplusItemState,
    patch: Partial<SurplusItem> = {},
  ): Promise<SurplusItem | undefined> {
    const item = this.items.get(id);
    if (!item || item.currentState !== fromState) return undefined;
    const updated = { ...item, ...patch, currentState: toState };
    this.items.set(id, updated);
    return { ...updated };
  }
}
