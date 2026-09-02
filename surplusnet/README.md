# SurplusNet

A decentralized, hyper-local cold-chain routing engine that converts commercial food
waste into tax write-offs for businesses, premium affordable meals for consumers, and
tokenized utility rewards for micro-couriers.

```
[Supplier POS / Web Portal]
            │
            ▼ (Triggers Real-Time Tax Logic)
  [SurplusNet Core Engine] ───(Matches Route)───► [Courier Mobile App]
            ▲                                              │
            │                                      (Verifies Temp & Drop)
    (Lists Blind Box)                                      ▼
            │                                    [Community Hub / Fridge]
  [Recipient Mobile App] ──────────────────────────────────┘
```

## Monorepo layout

```
surplusnet/
├── docker-compose.yml          # PostgreSQL (PostGIS) + Redis for local dev
├── packages/
│   └── core/                   # @surplusnet/core — the matching engine
│       ├── prisma/schema.prisma  # Users, SurplusItems, Wallets, Deliveries, DonationLedger
│       └── src/
│           ├── domain/         # Shared domain types
│           ├── lib/            # Clock, typed EventBus, domain errors
│           └── modules/
│               ├── tax/        # Epic 1 — Automated Supplier Tax-Shield
│               ├── inventory/  # Epic 2 — Two-Tiered Recipient Engine
│               ├── routing/    # Epic 3 — Courier dispatch + geo
│               └── wallet/     # Epics 2+3 — Cash / Community / Karma tokens
```

## How the epics map to code

### Epic 1 — Automated Supplier Tax-Shield (`modules/tax`)

- **`tax-valuation.service.ts`** — the Real-Time Valuation Engine. Implements the
  IRS §170(e)(3) enhanced deduction: `COGS + (FMV − COGS) / 2`, capped at `2 × COGS`,
  limited to FMV for depreciated goods. All math in integer cents, rounded down.
- **`donation-ledger.ts`** — append-only, SHA-256 hash-chained ledger. Any
  after-the-fact edit to a donation record breaks the chain and is caught by
  `verifyChain()`.
- **`audit-export.service.ts`** — compiles the ledger into the end-of-month,
  audit-ready summary (totals + line items + chain-integrity verdict) that a thin
  presentation layer renders to PDF for the supplier's accountant.

Listing an item (one POS webhook or one photo) triggers valuation and both ledger
writes automatically — zero added labor for restaurant staff.

### Epic 2 — Two-Tiered Recipient Engine (`modules/inventory`, `modules/wallet`)

- **`surplus-item.service.ts`** — lists a Blind Box in `SALES_PHASE` at a default
  70% discount, computes the deduction, refuses food past its cold-chain deadline.
- **`phase-rollover.worker.ts`** — the Dynamic Countdown Rotator. A background
  sweep (interval loop locally; the same `tick()` runs from cron or a Redis
  keyspace-notification trigger in production) moves anything unsold after 45
  minutes to `DONATION_PHASE` at $0 and emits `donation.available`. Transitions are
  compare-and-set, so concurrent workers can't double-donate an item. Items past
  `safeUntil` are expired, never donated.
- **`wallet.service.ts`** — one wallet holds CASH, COMMUNITY_CREDIT, and
  KARMA_CREDIT balances; checkout treats them identically (the dignity-first
  requirement). Idempotency keys + non-negative balance invariants mirror the
  unique constraint and SERIALIZABLE transaction the Prisma adapter uses.

### Epic 3 — Karma Ledger & Route Optimization (`modules/routing`, `modules/wallet`)

- **`courier-dispatch.service.ts`** — rideshare-style dispatch: on
  `donation.available`, finds active couriers inside a 1.5-mile radius, ranks by
  ETA (foot / bike / e-bike speeds), and fans the pickup offer out to the top 3.
  Straight-line ETA is a stand-in until an OSRM isochrone backend is wired into
  `estimateEtaSeconds`; courier positions come from a pluggable
  `CourierLocationSource` (Redis `GEOSEARCH` in production).
- **`delivery.completed` → `WalletService.mintKarmaForDelivery`** — mints Karma
  Credits exactly once per delivery (replayed events are no-ops) and records
  `DELIVERY_VERIFIED` on the immutable ledger.

## Getting started

```bash
cd surplusnet
npm install

# Unit + integration tests (no database needed — services run against
# in-memory adapters behind the same repository interfaces Prisma implements)
npm test

# Typecheck / build
npm run typecheck
npm run build

# Local infra + Prisma client (optional, for the persistence layer)
docker compose up -d
cp .env.example .env
npm run prisma:generate -w @surplusnet/core
npm run prisma:migrate -w @surplusnet/core
```

## Architecture decisions

- **Cents, not floats** — every monetary column and function uses integer cents;
  tax rounding always floors (the conservative direction under audit).
- **Repository interfaces over direct ORM calls** — business logic (tax math,
  rollover, dispatch ranking, wallet invariants) is fully unit-testable without a
  database; the Prisma schema in `prisma/schema.prisma` is the production shape.
- **Typed event bus** — `donation.available`, `delivery.completed`, and
  `item.expired` decouple inventory from routing from wallets; the bus swaps for
  Redis pub/sub without touching publishers.
- **PostGIS-ready** — coordinates are plain doubles for portability; the schema
  documents the raw migration that adds a generated `geography` column + GiST
  index for radius queries at scale.
- **`createSurplusNet()`** in `src/index.ts` wires the whole pipeline and is
  exercised end-to-end in `pipeline.integration.test.ts`:
  list → 45-min rollover → dispatch → delivery → karma mint → monthly audit report.

## Roadmap (next packages)

- `apps/api` — NestJS/Fastify HTTP + webhook layer (POS integrations: Square, Toast, Clover).
- `apps/courier` / `apps/recipient` — React Native (Expo) apps sharing `@surplusnet/core` types.
- `packages/vision` — photo → item category/volume extraction endpoint.
- Partner API for Karma Credit redemption at local merchants.
