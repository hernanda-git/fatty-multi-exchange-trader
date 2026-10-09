"""Recovery must never turn flat inventory into broad release authority."""

import asyncio
import copy
import importlib.util
import json
import time
from pathlib import Path
from uuid import UUID

import pytest

from fatty_trader.storage import bitget_live_latch_recovery as recovery
from fatty_trader.storage.bitget_baseline import BaselineRefused

BASELINE_ID = UUID("00000000-0000-0000-0000-000000000001")
ENV = {
    "TRADER_MODE": "LIVE",
    "BITGET_MODE": "LIVE",
    "BITGET_EXECUTION_ENABLED": "1",
    "BITGET_PROTECTION_STREAM_ENABLED": "1",
}
LATCHES = [
    {
        "scope": scope,
        "reason": reason,
        "active": True,
        "latched_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
    }
    for scope, reason in recovery.ALLOWED_REASONS.items()
]
RECEIPT = {
    "id": str(BASELINE_ID),
    "exchange": "bitget",
    "account_id": "1234",
    "environment": "LIVE",
    "approval_reference": "baseline-approval",
    "uncertainty_policy": "unresolved-history-not-verified-closure",
    "provider_evidence": {"account_id": "1234", "environment": "LIVE"},
}


class Client:
    environment = "LIVE"

    def __init__(self, *, nonflat=False):
        self.calls = []
        self.nonflat = nonflat

    async def _get(self, path, params=None):
        self.calls.append((path, params))
        if path.endswith("/time"):
            return {"serverTime": str(int(time.time() * 1000))}
        if path.endswith("/info"):
            return {"userId": "1234"}
        if path.endswith("/all-position"):
            return [{"symbol": "BTCUSDT", "total": "1"}] if self.nonflat else []
        return {"entrustedList": None, "endId": None}


class Socket:
    public_endpoint = recovery.V2_PUBLIC_WS_URL
    private_endpoint = recovery.V2_PRIVATE_WS_URL
    state = "CONNECTED"

    def __init__(self, *, authenticated=True):
        self.private_authenticated = authenticated
        self.last_public_pong_at = None
        self.last_private_pong_at = None
        self.closed = False

    async def connect(self):
        pass

    async def send_heartbeat_if_due(self):
        self.last_public_pong_at = time.monotonic()
        self.last_private_pong_at = time.monotonic()
        return True

    async def receive_once(self):
        await asyncio.sleep(0.01)
        return []

    def check_freshness(self):
        return self.private_authenticated

    def last_mark_event_age(self, symbol):
        return 0.01

    async def close(self):
        self.closed = True


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.rowcount = 1

    def execute(self, statement, params=()):
        db = self.connection
        db.statements.append((statement, params))
        if statement.startswith("LOCK TABLE"):
            if db.changed:
                db.latches[0]["updated_at"] = "2026-10-09T00:00:00Z"
            if db.future_on_lock:
                db.future_unresolved = True
        elif statement.startswith("SELECT to_jsonb(k)"):
            self.rows = [(copy.deepcopy(latch),) for latch in db.latches]
        elif statement.startswith("SELECT to_jsonb(b)"):
            self.rows = [(copy.deepcopy(db.receipt),)] if db.receipt else []
        elif "account_id IS DISTINCT" in statement:
            self.rows = [(db.binding_mismatch,)]
        elif "FROM live_order_intents i" in statement and "LEFT JOIN" in statement:
            self.rows = [(db.orphan, False)]
        elif "lower(d.exchange)" in statement:
            self.rows = [(False,)]
        elif statement.startswith("SELECT EXISTS(SELECT 1 FROM live_order_intents"):
            self.rows = [(db.future_unresolved, False, False, False)]
        elif statement.startswith("UPDATE venue_kill_switches"):
            db.updates.append(params)
            self.rowcount = 0 if db.update_fails else 1
        elif statement.startswith("INSERT INTO notifications_outbox"):
            if db.outbox_fails:
                raise RuntimeError("outbox unavailable")
            db.events.append(json.loads(params[2]))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self):
        self.latches = copy.deepcopy(LATCHES)
        self.receipt = copy.deepcopy(RECEIPT)
        self.binding_mismatch = False
        self.changed = False
        self.orphan = False
        self.future_unresolved = False
        self.future_on_lock = False
        self.update_fails = False
        self.outbox_fails = False
        self.statements = []
        self.updates = []
        self.events = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


async def run(db, client=None, socket=None, **kwargs):
    return await recovery.PostgresBitgetLiveLatchRecovery(lambda: db).run(
        client or Client(),
        socket or Socket(),
        environ=ENV,
        confirmed=True,
        account_id="1234",
        baseline_id=BASELINE_ID,
        approval_reference="recovery-approval",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_default_proof_queries_every_product_family_twice_without_writes():
    db, client, socket = Connection(), Client(), Socket()
    result = await run(db, client, socket)
    assert result["applied"] is False
    assert len(result["recovery_digest"]) == 64
    assert db.updates == db.events == []
    assert db.commits == 0
    assert db.closed and socket.closed
    for product in ("USDT-FUTURES", "USDC-FUTURES", "COIN-FUTURES"):
        pending = [
            params
            for path, params in client.calls
            if "pending" in path and params["productType"] == product
        ]
        assert len(pending) == 8
        assert {params.get("planType", "ordinary") for params in pending} == {
            "ordinary",
            "normal_plan",
            "profit_loss",
            "track_plan",
        }
    lock_index = next(i for i, (sql, _) in enumerate(db.statements) if sql.startswith("LOCK TABLE"))
    assert "venue_kill_switches" in db.statements[lock_index][0]


@pytest.mark.asyncio
async def test_apply_is_one_commit_with_exact_latches_and_audited_outbox():
    db = Connection()
    proof = await run(db)
    result = await run(db, apply=True, expected_digest=proof["recovery_digest"])
    assert result["applied"] is True
    assert db.commits == 1
    assert {(p[1], p[2]) for p in db.updates} == set(recovery.ALLOWED_REASONS.items())
    assert len(db.events) == 2
    for event in db.events:
        assert event["kind"] == "kill-switch-release"
        assert event["baseline_id"] == str(BASELINE_ID)
        assert event["baseline_approval_reference"] == "baseline-approval"
        assert event["prior_latch"]["active"] is True
        assert event["provider_evidence"]["account_id"] == "1234"
        assert event["stream"]["private_authenticated"] is True


@pytest.mark.parametrize(
    "scope,reason",
    [
        ("global", "clock-skew-exceeded"),
        ("bitget", "unexpected-order:foreign"),
        ("bitget-protection-stream", "protection-read-failed"),
        ("other-venue", "socket-not-connected"),
    ],
)
@pytest.mark.asyncio
async def test_other_scope_or_reason_never_releases(scope, reason):
    db = Connection()
    db.latches.append({**LATCHES[0], "scope": scope, "reason": reason})
    with pytest.raises(BaselineRefused, match="unsafe reason"):
        await run(db)
    assert db.commits == 0 and not db.updates


@pytest.mark.asyncio
async def test_latch_changed_under_lock_refuses_even_same_reason():
    db = Connection()
    db.changed = True
    with pytest.raises(BaselineRefused, match="latch rows changed"):
        await run(db)
    assert db.commits == 0 and not db.updates


@pytest.mark.parametrize("failure", ["missing", "account", "database-binding"])
@pytest.mark.asyncio
async def test_missing_or_misbound_baseline_refuses(failure):
    db = Connection()
    if failure == "missing":
        db.receipt = None
    elif failure == "account":
        db.receipt["provider_evidence"]["account_id"] = "9999"
    else:
        db.binding_mismatch = True
    with pytest.raises(BaselineRefused, match="baseline"):
        await run(db)
    assert db.commits == 0 and not db.updates


@pytest.mark.asyncio
async def test_nonflat_provider_refuses_before_database_access():
    db = Connection()
    with pytest.raises(BaselineRefused, match="exposure"):
        await run(db, Client(nonflat=True))
    assert db.statements == [] and not db.updates


@pytest.mark.asyncio
async def test_unauthenticated_stream_refuses(monkeypatch):
    monkeypatch.setattr(recovery, "STREAM_PROOF_TIMEOUT", 0.01)
    db, socket = Connection(), Socket(authenticated=False)
    with pytest.raises(BaselineRefused, match="liveness proof failed"):
        await run(db, socket=socket)
    assert socket.closed and not db.updates and db.commits == 0


@pytest.mark.parametrize("missing_leg", ["public", "private"])
def test_one_leg_pong_or_login_alone_is_not_liveness(missing_leg):
    socket = Socket()
    socket.last_public_pong_at = time.monotonic()
    socket.last_private_pong_at = time.monotonic()
    setattr(socket, f"last_{missing_leg}_pong_at", None)
    with pytest.raises(BaselineRefused, match="both-leg pong"):
        recovery.require_healthy_stream(socket)


@pytest.mark.parametrize("orphan", [False, True])
@pytest.mark.asyncio
async def test_unresolved_future_ownership_refuses_even_if_bound(orphan):
    db = Connection()
    db.orphan = orphan
    db.future_unresolved = not orphan
    with pytest.raises(BaselineRefused, match="ledger is not ready|future/unbaselined"):
        await run(db)
    assert db.commits == 0 and not db.updates


@pytest.mark.asyncio
async def test_new_unresolved_commitment_after_initial_read_refuses_under_lock():
    db = Connection()
    db.future_on_lock = True
    with pytest.raises(BaselineRefused, match="future/unbaselined"):
        await run(db)
    assert db.commits == 0 and not db.updates


@pytest.mark.asyncio
async def test_flatness_is_collected_again_after_lock_and_must_still_hold():
    class ChangesAfterInitialInventory(Client):
        async def _get(self, path, params=None):
            if path.endswith("/time") and self.calls:
                self.nonflat = True
            return await super()._get(path, params)

    db = Connection()
    with pytest.raises(BaselineRefused, match="exposure"):
        await run(db, ChangesAfterInitialInventory())
    assert any(sql.startswith("LOCK TABLE") for sql, _ in db.statements)
    assert db.commits == 0 and not db.updates


@pytest.mark.asyncio
async def test_reviewed_snapshot_digest_is_required_and_exact():
    db = Connection()
    with pytest.raises(BaselineRefused, match="reviewed dry-run"):
        await run(db, apply=True)
    with pytest.raises(BaselineRefused, match="reviewed dry-run"):
        await run(db, apply=True, expected_digest="0" * 64)
    assert not db.updates and db.commits == 0


@pytest.mark.parametrize("failure", ["update_fails", "outbox_fails"])
@pytest.mark.asyncio
async def test_atomic_release_rolls_back_on_update_or_notification_failure(failure):
    db = Connection()
    proof = await run(db)
    setattr(db, failure, True)
    before = db.rollbacks
    with pytest.raises((BaselineRefused, RuntimeError)):
        await run(db, apply=True, expected_digest=proof["recovery_digest"])
    assert db.commits == 0 and db.rollbacks > before


@pytest.mark.parametrize(
    "key,value",
    [
        ("TRADER_MODE", "DEMO"),
        ("BITGET_MODE", "DEMO"),
        ("BITGET_EXECUTION_ENABLED", "0"),
        ("BITGET_PROTECTION_STREAM_ENABLED", "0"),
        ("BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED", "1"),
    ],
)
def test_settings_never_changed_or_relaxed(key, value):
    env = {**ENV, key: value}
    before = env.copy()
    with pytest.raises(BaselineRefused):
        recovery.require_production_settings(env)
    assert env == before


def test_cli_refuses_without_confirmation_before_network(monkeypatch, capsys):
    path = Path(__file__).resolve().parents[2] / "scripts" / "recover_bitget_live_latches.py"
    spec = importlib.util.spec_from_file_location("recover_bitget_live_latches", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "BitgetRestClient", lambda *a, **kw: pytest.fail("client created"))
    assert (
        module.main(
            [
                "--approval-reference",
                "approved",
                "--account-id",
                "1234",
                "--baseline-id",
                str(BASELINE_ID),
            ]
        )
        == 2
    )
    assert "--confirm" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_real_socket_tracks_actual_pongs_per_leg_not_seeded_login():
    from fatty_trader.exchanges.bitget.websocket_v2 import BitgetV2WebSocket
    from tests.unit.ws_v2_fakes import FakeTransport

    now = [100.0]
    transport = FakeTransport(private_script=['{"event":"login","code":"0"}'])
    socket = BitgetV2WebSocket(
        api_key="k",
        api_secret="s",
        passphrase="p",
        symbols=[],
        transport=transport,
        clock=lambda: now[0],
    )
    try:
        await socket.connect()
        assert socket.private_authenticated
        assert socket.last_pong_at == 100.0
        assert socket.last_public_pong_at is None
        assert socket.last_private_pong_at is None
        transport.add_public("pong")
        for _ in range(10):
            await asyncio.sleep(0)
            if socket.last_public_pong_at is not None:
                break
        assert socket.last_public_pong_at == 100.0
        assert socket.last_private_pong_at is None
        now[0] = 101.0
        transport.add_private("pong")
        for _ in range(10):
            await asyncio.sleep(0)
            if socket.last_private_pong_at is not None:
                break
        assert socket.last_public_pong_at == 100.0
        assert socket.last_private_pong_at == 101.0
    finally:
        await socket.close()
