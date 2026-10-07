# Owner-approved operational LIVE baseline: definitive API

Ledger module: `fatty_trader.storage.operational_baseline`. Schema migration 24 in `operational_baseline_schema.py`. NEW operational accounting ONLY: historical records remain UNVERIFIED, FILLED/consumed literally preserved. No historical close ownership/retirement is claimed.

## Typed contract (parent / ops / monitor integration)

```
BaselineContext(account_identity: str, api_key_sha256: str)
FlatAccountProof(context: BaselineContext, environment: str, authenticated: bool,
 observed_at: datetime, positions: int, ordinary_orders: int, conditional_orders: int,
 conditional_inventory_complete: bool, clock_safe: bool)
MonitorReadinessProof(context: BaselineContext, process_generation: str,
 observed_at: datetime, private_pong_at: datetime, clean_cycle_at: datetime,
 login_authenticated: bool, private_subscriptions_ready: bool, connected: bool)
IncidentLatch(scope: str, reason: str, latched_at: datetime|None, updated_at: datetime)
PostgresOperationalBaselineRepository(connection_factory)
.prepare(*, context: BaselineContext, approval_reference: str,
 entry_ids: tuple[UUID,...], reservation_ids: tuple[UUID,...]) -> UUID
.activate(*, baseline_id: UUID, context: BaselineContext,
 collect_proof: Callable[[], FlatAccountProof],
 monitor_ready: Callable[[Any, BaselineContext], MonitorReadinessProof],
 expected_incidents: tuple[IncidentLatch,...],
 tests_reference: str, review_reference: str) -> UUID
```

`collect_proof` is invoked INSIDE the locked repository transaction. Only authentic GET-only collectors may implement it, never saved JSON. Counts mean complete empty current inventories across all ordinary/conditional plan families/account symbols, established pagination semantics. Unknown/incomplete/errors fail closed. Clock safety means the unchanged clock-offset interval threshold. UID authenticated by provider and SHA256(API key) bind exactly; never print UID/key/hash. Key rotation requires separately approved new binding (no automatic inheritance).

`monitor_ready` reads actual monitor-owned published/durable evidence, using passed cursor when database-backed. Publisher adapter is monitor-agent owned; no second socket. Repository re-ages observation/pong/clean-cycle timestamps after callback (maximum 30s, future skew 1s). Require actual process lifetime/login/private ACKs/pong/clean provider cycle. Process UUID alone is not authentication; trusted writer adapter remains the boundary.

Expected incidents MUST be exactly `bitget` + `bitget-protection-stream`, with exact observed reason/latched_at/updated_at. Only owner-approved clock-skew/socket incidents may be released. Repository locks/rereads them and global switch, verifies fingerprints unchanged, and atomically appends activation/release evidence. Global must be explicitly present and inactive. Review/test references are trusted internal caller attestations; CLI must verify full integrated tests and independent review before invoking activation. A repository API is not deployment authorization.

## Filters / parent assembly seams

`PostgresBitgetDispatchRepository(connection_factory, *, baseline_context: BaselineContext|None = None)` and `PostgresBitgetMarginReservationRepository(..., *, baseline_context: BaselineContext|None = None)` receive authenticated identity context. DEFAULT NONE EXCLUDES NOTHING. Parent must wire context in live worker/preflight assembly (not ledger agent service edits). `bind_baseline_context(context: BaselineContext|None) -> None` on BOTH repositories validates the type and supports post-authentication runtime binding before recovery/dispatch starts. Constructors also validate context. Authenticate UID using the current LIVE provider client (never infer it from env/DB) and bind execution dispatch repository, reservation repository, and outer dispatcher repository together. `set_baseline_context(cursor, context)` sets transaction-local bound settings (NONE explicitly clears both); exclusions view requires BOTH UID+key fingerprint exact match and LIVE.

17 distinct explicitly owner-approved FILLED ENTRY UUIDs + 4 distinct legacy NULL-environment consumed reservation UUIDs are captured; four associated dispatches are captured, too. Preparation captures full semantic row JSON minus volatile updated_at ONLY. SQL-generated JSON text is preserved verbatim until the JSONB insert, never decoded through float/re-encoded; Python Decimal parsing is validation-only. Activation and runtime fingerprints compare JSONB entirely in PostgreSQL, including every numeric digit. PREPARED excludes nothing. ACTIVE ignores captured unchanged graph ONLY; any actual state/financial/evidence change invalidates ALL exclusions. History is never deleted/released/rebound.

Active epoch UUID/activation DB-clock cutoff is immutable. Source cutoff applies conservatively even if exclusions invalidate; source receive time must be strictly newer than activation. Activation terminalizes every proven-unsent preactivation QUEUED row with an audited baseline expiration, zero attempts/no lease/no durable intent/reservation. Ambiguous queued evidence rejects transaction; no replay, no raw SQL cleanup. New pending/unknown intents retain all vetoes. Historical FILLED reservation resolve is a no-op for captured unchanged active rows.

Activation uses shared admission advisory lock, row locks and conservative table fences against insertion races; invokes proof collectors after locks; locks the affected audit/outbox tables before collectors as well, and re-ages all evidence before append/release AND after final audit writes before commit. Stale final evidence rolls back the complete activation, expirations, notifications, and incident releases. Full pre-ENTRY guards remain unchanged.

No scripts/service files owned here. Parent source integration and separate agents' collector/publisher remain required. No LIVE DB/provider mutations or deployment performed by ledger agent.
