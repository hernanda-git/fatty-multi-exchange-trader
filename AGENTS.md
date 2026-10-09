# Coding-agent workflow

## Trading contract

Read [docs/TRADING-RULES.md](docs/TRADING-RULES.md) before changing order routing, source management, reconciliation, protection or reporting. Respect [the production LIVE-only policy](docs/PRODUCTION-LIVE-POLICY.md). Source changes are not authorization to deploy, trade, clear a safety latch, change risk limits or mutate a provider account.

## Work from evidence

- Scope the actual repository and read affected production callers, durable storage, provider adapters and existing regressions before editing. A pure helper or passing fixture is not an integrated production feature.
- Treat reported provider failures and operator observations as facts. Distinguish source inspection, local verification, runtime observations and deployment evidence; never claim one proves another.
- Keep financial calculations in Decimal and preserve account-baseline exclusion, immutable admission evidence, stable client order IDs and intent-before-mutation fences.
- Resolve every caller at a clean cutover. Do not add silent fallback orders, compatibility aliases, synthetic fills or fabricated reconciliation success.

## Parallel work

- Parallelize only independent, substantive slices with explicit file ownership and shared interfaces. Assign one integration owner for shared service wiring/schema and final verification.
- Workers research and edit their assigned slice, update corresponding regressions and documentation, and return the exact changed behavior, remaining dependencies and checks to run. Coordinate before touching another worker's files.
- Do not run builds, tests, linters or formatters against half-integrated changes. After all edits land, the integration owner runs the agreed local checks once and reports actual results. Local fixtures must not invoke live provider mutations.

## Durable mutation safety

Persist each entry, management close and exact owned-entry cancellation claim before its POST. A timeout, malformed acknowledgment, crash or existing claim requires readback/reconciliation, not a new POST. Working LIMIT entries must be represented by durable ownership; do not bypass admission by globally ignoring pending orders. Never use cancel-all to implement source-management cancellation.

## Delivery

Complete production wiring, affected callers, storage migrations, regressions and relevant documentation. Separate unexercised checks and unverified deployment status from observed evidence. Live deployment and provider-side acceptance require separately authorized operations and read-only runtime evidence; documentation must not imply either occurred merely because code changed.
