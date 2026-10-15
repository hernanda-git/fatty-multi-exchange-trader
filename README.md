# Fatty Multi-Exchange Trader

Telegram signal intake, one canonical interpretation, and isolated Binance USDⓈ-M / Bitget USDT futures dispatches.

## Runtime safety status

Production is permanently LIVE-only with execution enabled; see
[production policy](docs/PRODUCTION-LIVE-POLICY.md). Repository code alone does not prove the
state of a running container or provider account. Read the effective environment,
Compose state, database migrations, and authenticated provider reads before
claiming readiness.

- Production Bitget execution requires LIVE/LIVE/1; conflicting overrides fail
  deployment admission. Capability, fallback, stream and operator mutation flags
  retain independent default-closed gates.
- Native protection is the primary design; mark-price WebSocket and REST watchdog
  are additive, feature-gated layers.
- Missing protection is symbol-local admission failure, not an automatic global
  kill-switch latch.
- No LIVE canary or historical signal replay is part of a code deployment.

## Documentation

- [2026-10-09 full audit, repairs and evidence boundaries](docs/AUDIT-20261009.md)
- [Owner-selected trading and management rules](docs/TRADING-RULES.md)
- [Coding-agent workflow](AGENTS.md)
- [Bitget protection architecture and contracts](docs/BITGET-PROTECTION-HARDENING.md)
- [Bitget protection operations and controlled deployment](docs/BITGET-PROTECTION-OPERATIONS.md)
- [Architecture](ARCHITECTURE.md)
- [Security](SECURITY.md)
- [Operations](docs/OPERATIONS.md)
- [Approved Bot Management Console master plan](docs/BOT-CONSOLE-MASTER-PLAN.md)
- [Console phase progress and evidence tracker](docs/BOT-CONSOLE-PROGRESS.md)
- [Console capability and safety inventory](docs/BOT-CONSOLE-CAPABILITIES.md)
- [Console UI/API interaction specification](docs/BOT-CONSOLE-UI-API-SPEC.md)
- [Console operator/trader/recovery/admin user guide](docs/BOT-CONSOLE-USER-GUIDE.md)

## Management console access

The console is private by default. After its approved deployment, establish the
existing host tunnel with `ssh -N -L 18081:127.0.0.1:18081 fspmi-hostinger`, then
open `http://localhost:18081`. Use the exact configured origin; an IP address or
different port is not interchangeable for passkey verification.

Initial enrollment uses a short-lived one-use token delivered privately to the
configured Telegram owner. Enter it in the enrollment form and register a
user-verified passkey; no enrollment token belongs in a URL or browser storage.
Subsequent login and action-specific consent use that passkey. Two-person actions
require a distinct enrolled scoped owner and cannot be self-approved.

All 15 screens share authenticated observations and durable action jobs. A preview
is not execution; only provider or host readback proves an outcome. A blocked
entry latch is displayed independently from application health and is not cleared
by deployment. Read [implementation decisions](docs/BOT-CONSOLE-IMPLEMENTATION.md),
[host operations](docs/OPERATIONS.md#console-host-operations-boundary), and the
[evidence tracker](docs/BOT-CONSOLE-PROGRESS.md) for actual acceptance and rollout
status.


## Safety invariants

- Every executable entry requires a geometrically valid stop loss and durable
  intent claim.
- Native SL/TP is accepted only after provider read-back proves the exact plan,
  side, trigger, quantity, mode, ID, and client OID.
- Fallback close is reduce-only, quantity-clamped, and atomically fenced against
  duplicate provider submissions.
- Provider-only/system liquidation is reconciled by provider fill ID; it is never
  replayed as a new signal.
- Manual operator mutations remain disabled unless separately approved.
