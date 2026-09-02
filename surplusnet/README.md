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
│               ├── compliance/ # Epic 1 — liability shield + SB 1383 recovery reports
│               ├── impact/     # The Impact Ledger — meals, lbs, CO2e, avoided cost
│               ├── inventory/  # Epic 2 — Two-Tiered Recipient Engine
│               ├── checkout/   # Epic 2 — dignity-parity purchase (cash/credits/karma)
│               ├── funding/    # Epic 2 — Community Fund + sponsorship engine
│               ├── recipient/  # Epic 2 — dietary/preference matching
│               ├── routing/    # Epic 3 — Courier dispatch + geo
│               ├── delivery/   # Epic 3 — lifecycle + cold-chain custody
│               ├── karma/      # Epic 3 — surge pricing, perks, teams, certifications
│               ├── growth/     # Multi-sided referral engine
│               └── wallet/     # Cash / Community / Karma tokens
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

### The incentive loop (`modules/checkout`, `modules/funding`, `modules/karma`)

The closed-loop economics that make every party win without "charity" as the
driver:

- **`checkout.service.ts`** — Phase-1 purchase with **dignity parity**: payment
  is any mix of cash and Community Credits (1 credit = 1 cent) through the same
  endpoint with the same receipt — nothing downstream can tell which mix was
  used. Per sale, a configurable share of the cash portion (default 20%) is
  contributed to the Community Fund and the rest is supplier COGS recovery;
  spent credits are paid out to the supplier by the fund at face value. Failed
  or raced payments are compensated with idempotent refunds. `claimDonation`
  is the $0 path for `DONATION_PHASE` items.
- **`community-fund.service.ts`** — the self-sustaining pool. Invariant:
  **every credit in circulation is backed 1:1 by money in the pool** (from the
  open-market tier's sales cut plus municipal/non-profit grants). Monthly
  allocation mints credits only up to the pool's headroom and is idempotent per
  recipient per month; settlement retires spent credits as the fund pays
  suppliers. Free food is never unfunded charity — suppliers get real money
  either way.
- **`karma/partner-redemption.service.ts`** — the Karma Token Utility System.
  Local merchants register perks (coffee, transit, groceries) priced in Karma
  Credits; couriers redeem them for single-use vouchers. Retried requests can't
  double-charge (wallet idempotency keys), and per-partner settlement tallies
  support sponsor reimbursement. Karma never converts to cash — it converts to
  local value, keeping the network a volunteer economy, not a sub-minimum-wage
  gig market.
- **`karma/engagement.service.ts`** — retention mechanics: consecutive-day
  streaks, lifetime milestone badges (`first-rescue` → `city-champion`), a
  rescue leaderboard, and **verified corporate volunteer hours**: couriers
  linked to an employer accrue minutes per completed delivery, exported as a
  monthly per-employer report for HR's paid-volunteer-time programs.

`delivery.completed` drives all courier rewards atomically-once: the idempotent
karma mint gates streaks, badges, volunteer minutes, and the ledger's
`DELIVERY_VERIFIED` record, so a replayed event changes nothing.

### The Impact Ledger (`modules/impact`)

The environmental twin of the financial ledger, and the primitive nearly every
other incentive surface consumes (ESG exports, city dashboards, sponsor meters,
impact receipts, team scoreboards, future carbon credits). Per verified rescue
it books meals rescued, pounds diverted, CO2e avoided, and hauling cost avoided
to the immutable ledger (`IMPACT_RECORDED`) — impact is only booked for food
that reached someone, exactly once per item — and aggregates by supplier, zone,
and month. Factors are transparent, documented estimates (USDA meal weight,
EPA WARM-style emission factors, typical hauling rates) and injectable per
jurisdiction.

### The liability shield & regulatory product (`modules/compliance`, `modules/delivery`)

- **`delivery.service.ts`** — the courier lifecycle (accept → pick up → temp
  readings → drop off) with compare-and-set acceptance (one courier wins per
  item), timestamped cold-chain custody, honest excursion flagging, and karma
  locked in at accept time so surge pricing can't be gamed. Cancelling returns
  the item to the donation pool for re-dispatch.
- **`compliance.service.ts`** — per-donation **compliance certificates**
  (custody chain, temp log, safe-until adherence, Bill Emerson good-faith
  statement) that answer the #1 donation blocker: lawsuit fear. Plus the
  **recovery report**: the SB 1383-style monthly filing with the CFO's three
  numbers stapled on — tax deduction, avoided hauling cost, and CO2e for ESG.

### The reliability & growth layer (`modules/karma`, `modules/funding`, `modules/growth`, `modules/recipient`)

- **`karma-pricing.service.ts`** — surge karma: urgency, distance, weather,
  off-peak, and zone courier-scarcity multipliers (clamped 0.8–3.0x) so the
  rainy 9pm rescue with a closing window actually gets picked up.
- **`team-competition.service.ts`** — zone-scoped monthly team leaderboards
  (workplaces, congregations, blocks): social identity for the 95% that
  individual leaderboards don't motivate.
- **`certification.service.ts`** — 10-minute micro-courses paying a one-time
  karma bonus + permanent badge; also the training half of the liability story.
- **`sponsorship.service.ts`** — sponsors buy visible, quantified local impact:
  direct grants, **matching campaigns** that auto-double organic sale
  contributions up to a cap (grants and matches are never re-matched, so
  campaigns can't chain-react), the **karma subsidy pool** that backs karma
  spent at checkout, and the live per-sponsor impact meter.
- **Karma at checkout (role fluidity)** — couriers eat what they rescue: karma
  pays for food at a configured cents rate, the sponsor pool makes the supplier
  whole in cash, and the purchase is rejected outright if the pool can't back
  it — unbacked value is never spent.
- **`growth/referral.service.ts`** — multi-sided referrals paying each referrer
  in their own currency (courier → karma, recipient → fund-backed credits or a
  reported skip when the pool is dry, supplier → stacking featured placement),
  and only on the new user's first real action — never at signup.
- **`recipient/preferences.service.ts`** — exclusion-based dietary matching
  (safe-by-default: untagged items are hidden from recipients with
  restrictions), because a box you can't eat helps nobody.

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
  exercised end-to-end in two integration tests:
  `pipeline.integration.test.ts` (list → 45-min rollover → dispatch → delivery
  → karma mint → monthly audit report) and `incentive-loop.integration.test.ts`
  (cash sale funds pool → monthly credit allocation → credit purchase pays
  supplier → delivery mints karma → karma redeems a real perk).

## Roadmap (next packages)

- `apps/api` — NestJS/Fastify HTTP + webhook layer (POS integrations: Square, Toast, Clover).
- `apps/courier` / `apps/recipient` — React Native (Expo) apps sharing `@surplusnet/core` types.
- `packages/vision` — photo → item category/volume extraction endpoint.
- Partner API for Karma Credit redemption at local merchants.
