/**
 * Minimal typed pub/sub used to decouple modules: the inventory rollover
 * worker publishes `donation.available`, the routing module subscribes and
 * dispatches couriers. Swappable for Redis pub/sub or a message queue in
 * production without touching the publishers.
 */

export interface SurplusEvents {
  'donation.available': { itemId: string; latitude: number; longitude: number };
  'delivery.completed': {
    deliveryId: string;
    itemId: string;
    courierId: string;
    /** Surge-priced karma locked in at accept time; omitted = network default. */
    karmaCredits?: number;
  };
  'item.expired': { itemId: string };
}

type Handler<T> = (payload: T) => void | Promise<void>;

export class EventBus {
  private handlers = new Map<keyof SurplusEvents, Set<Handler<never>>>();

  on<K extends keyof SurplusEvents>(event: K, handler: Handler<SurplusEvents[K]>): () => void {
    let set = this.handlers.get(event);
    if (!set) {
      set = new Set();
      this.handlers.set(event, set);
    }
    set.add(handler as Handler<never>);
    return () => set?.delete(handler as Handler<never>);
  }

  async emit<K extends keyof SurplusEvents>(event: K, payload: SurplusEvents[K]): Promise<void> {
    const set = this.handlers.get(event);
    if (!set) return;
    for (const handler of set) {
      await (handler as Handler<SurplusEvents[K]>)(payload);
    }
  }
}
