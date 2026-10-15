"""Isolated console command/fencing regressions; never contact a provider."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from fatty_trader.console import commands as module
from fatty_trader.console.commands import (
    ADAPTER_VERSION,
    POLICY_VERSION,
    CommandError,
    CommandService,
)
from fatty_trader.console.schema import CONSOLE_SCHEMA_SQL
from fatty_trader.console.store import (
    ALLOWED_TRANSITIONS,
    ConsoleStore,
    LostLease,
    canonical,
    digest,
    safe_json,
)
from fatty_trader.console.worker import process_job

NOW = datetime.now(UTC)
SCOPE = {"account_id": "account", "venue": "bitget", "environment": "LIVE"}
PRINCIPAL = {"id": "actor", "session_id": "session", "roles": ["owner"]}


class Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []
        self.rowcount = 1

    def execute(self, sql, params=()):
        assert sql.count("%s") == len(params), (sql, params)
        self.calls.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0)

    def fetchall(self):
        return self.rows.pop(0)


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = self.rollbacks = self.closed = 0

    def cursor(self, **kwargs):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


class Handler:
    def current_resource_version(self, *args):
        return "resource1"

    async def preview(self, *args):
        return {
            "resource_version": "resource1",
            "gates": [{"name": "mutations", "verdict": "pass"}],
            "effects": [],
        }


@pytest.fixture
def action_catalog(monkeypatch):
    schemas = {
        "position.close": {"type": "object", "properties": {}, "additionalProperties": False},
    }
    policies = {
        "position.close": {"role": "operator", "danger_tier": 2},
    }
    monkeypatch.setattr(module, "catalog", lambda: (schemas, policies))


def actor():
    return {
        "id": "actor",
        "roles": ["owner"],
        "scopes": [SCOPE],
        "enabled": True,
        "revoked_at": None,
        "expires_at": NOW + timedelta(hours=1),
        "absolute_expires_at": NOW + timedelta(hours=1),
        "principal_revision": 1,
        "revision": 1,
        "last_seen_at": NOW,
    }


def preview():
    return {
        "id": "preview",
        "actor_id": "actor",
        "action": "position.close",
        "arguments": {},
        "target_refs": [],
        "scope": SCOPE,
        "account_id": "account",
        "environment": "LIVE",
        "resource_version": "resource1",
        "preview_digest": "digest",
        "confirmation_phrase": "CLOSE LIVE exact",
        "policy_version": POLICY_VERSION,
        "expires_at": NOW + timedelta(hours=1),
        "consumed_by": None,
        "gates": [{"verdict": "pass"}],
        "danger_tier": 2,
    }


def job():
    return {
        "id": str(uuid4()),
        "preview_id": "preview",
        "actor_id": "actor",
        "session_id": "session",
        "action": "position.close",
        "arguments": {},
        "target_refs": [],
        "scope": SCOPE,
        "account_id": "account",
        "environment": "LIVE",
        "resource_version": "resource1",
        "revision": 1,
        "state": "queued",
        "phase": "authorization",
        "danger_tier": 2,
        "policy_version": POLICY_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "preview_digest": "digest",
        "effect_claimed": False,
        "lease_token": "lease",
        "claim_generation": 1,
        "cancellation_requested_at": None,
    }


def request():
    return {
        "preview_id": "preview",
        "preview_digest": "digest",
        "resource_version": "resource1",
        "policy_version": POLICY_VERSION,
        "confirmation_phrase": "CLOSE LIVE exact",
        "reason": "approved exact close",
        "approval_reference": "approval-1",
    }


def store_with(rows):
    cursor = Cursor(rows)
    connection = Connection(cursor)
    return ConsoleStore(lambda: connection), connection, cursor


def test_owned_connections_commit_rollback_and_close():
    store, connection, _ = store_with([{"value": 7}])
    assert store.one("SELECT %s AS value", (7,)) == {"value": 7}
    assert (connection.commits, connection.rollbacks, connection.closed) == (1, 0, 1)
    with pytest.raises(RuntimeError), store.connection():
        raise RuntimeError("read failed")
    assert (connection.commits, connection.rollbacks, connection.closed) == (1, 1, 2)


def test_canonical_values_redact_before_digest_and_persistence():
    value = {
        "quantity": Decimal("1.20"),
        "secret": "never",
        "nested": {"password": "never", "safe": 3},
    }
    assert safe_json(value) == {"quantity": "1.20", "nested": {"safe": 3}}
    assert "never" not in canonical(value)
    assert digest(value) == digest({"nested": {"safe": 3}, "quantity": "1.20"})


def test_enqueue_is_atomic_and_consumes_bound_grant(action_catalog):
    record = job()
    grant = {"id": "grant", "verified_at": NOW, "expires_at": NOW + timedelta(minutes=5)}
    store, connection, cursor = store_with(
        [preview(), actor(), None, record, grant, {"id": 1}, {"id": 2}, {"id": 3}]
    )
    service = CommandService(store, {"position.close": Handler()})
    result = service.enqueue(request(), PRINCIPAL, str(uuid4()))
    assert result["id"] == record["id"] and result["resource_version"] == "1"
    assert connection.commits == 1 and connection.closed == 1
    assert any("console_idempotency_receipts" in sql and "INSERT" in sql for sql, _ in cursor.calls)
    assert any("UPDATE console_step_up_grants" in sql for sql, _ in cursor.calls)
    assert "lease_token" not in result


def test_same_key_replay_does_not_consume_grant_again(action_catalog):
    body = request()
    receipt = {"payload_digest": digest(body), "job_id": "existing"}
    store, _, cursor = store_with([preview(), actor(), receipt, job()])
    result = CommandService(store, {"position.close": Handler()}).enqueue(
        body, PRINCIPAL, str(uuid4())
    )
    assert result["idempotency_replayed"] is True
    assert not any("console_step_up_grants" in sql for sql, _ in cursor.calls)


def test_same_key_different_payload_is_conflict(action_catalog):
    store, connection, _ = store_with(
        [preview(), actor(), {"payload_digest": "different", "job_id": "existing"}]
    )
    with pytest.raises(CommandError) as error:
        CommandService(store, {"position.close": Handler()}).enqueue(
            request(), PRINCIPAL, str(uuid4())
        )
    assert error.value.code == "idempotency_conflict"
    assert connection.rollbacks == 1


def test_mutation_gate_zero_cannot_enqueue(action_catalog):
    p = preview()
    p["gates"] = [{"name": "mutations", "verdict": "fail"}]
    store, _, cursor = store_with([p, actor(), None])
    with pytest.raises(CommandError) as error:
        CommandService(store, {"position.close": Handler()}).enqueue(
            request(), PRINCIPAL, str(uuid4())
        )
    assert error.value.code == "gate_closed"
    assert not any("INSERT INTO console_jobs" in sql for sql, _ in cursor.calls)


def test_missing_consent_rolls_back_job_and_receipt(action_catalog):
    store, connection, cursor = store_with([preview(), actor(), None, job(), None])
    with pytest.raises(CommandError) as error:
        CommandService(store, {"position.close": Handler()}).enqueue(
            request(), PRINCIPAL, str(uuid4())
        )
    assert error.value.code == "consent_required"
    assert connection.rollbacks == 1 and connection.commits == 0
    assert not any("INSERT INTO console_idempotency_receipts" in sql for sql, _ in cursor.calls)


def test_stale_resource_rejected_before_job_insert(action_catalog):
    class Changed(Handler):
        def current_resource_version(self, *args):
            return "changed"

    store, _, cursor = store_with([preview(), actor(), None])
    with pytest.raises(CommandError) as error:
        CommandService(store, {"position.close": Changed()}).enqueue(
            request(), PRINCIPAL, str(uuid4())
        )
    assert error.value.status == 412 and error.value.code == "resource_changed"
    assert not any("INSERT INTO console_jobs" in sql for sql, _ in cursor.calls)


def test_shared_password_catalog_exposes_no_identity_administration():
    schemas, policies = module.catalog()
    assert not any(action.startswith("access.") for action in schemas)
    assert not any(action.startswith("access.") for action in policies)
    assert "session.revoke" not in schemas  # Self-owned session revocation has a CSRF REST route.


def test_self_approval_refused_even_with_one_owner(action_catalog):
    record = job()
    record.update(state="awaiting_approval", danger_tier=3)
    store, _, cursor = store_with([record, actor(), actor()])
    with pytest.raises(CommandError) as error:
        CommandService(store, {"position.close": Handler()}).approve(
            record["id"],
            {"resource_version": "1", "preview_digest": "digest", "reason": "self approve"},
            PRINCIPAL,
        )
    assert error.value.code == "distinct_approver_required"
    assert not any("INSERT INTO console_approvals" in sql for sql, _ in cursor.calls)


def test_shared_password_owner_cannot_gain_distinct_worker_authority(action_catalog):
    record = job()
    record.update(actor_id="console-password-owner", danger_tier=3)
    with pytest.raises(CommandError) as error:
        CommandService(None, {"position.close": Handler()})._authority(None, record)
    assert error.value.code == "distinct_approver_required"


def test_shared_password_capabilities_keep_two_person_actions_unavailable(monkeypatch):
    schemas = {"risk.change": {"type": "object"}}
    policies = {"risk.change": {"role": "risk_owner", "danger_tier": 3}}
    monkeypatch.setattr(module, "catalog", lambda: (schemas, policies))
    items = CommandService(None, {"risk.change": Handler()}).capabilities(
        {"id": "console-password-owner", "roles": ["owner"]}, SCOPE
    )
    assert items[0]["enqueue_allowed"] is False
    assert items[0]["reasons"][0]["code"] == "second_approver_required"


def test_lost_lease_cannot_checkpoint_or_claim_effect():
    store, connection, cursor = store_with([None])
    with pytest.raises(LostLease):
        store.claim_effect(job(), "close", "close", {}, "stable")
    assert connection.rollbacks == 1
    assert not any("INSERT INTO console_job_effects" in sql for sql, _ in cursor.calls)


def test_allowed_phase_spine_and_additive_sql_safety():
    assert ALLOWED_TRANSITIONS["reconciliation_pending"] == {"running"}
    assert "queued" not in ALLOWED_TRANSITIONS["running"]
    assert "DROP TABLE" not in CONSOLE_SCHEMA_SQL
    assert "distinct approver required" in CONSOLE_SCHEMA_SQL
    assert "console_terminal_job_guard" in CONSOLE_SCHEMA_SQL
    assert "GRANT INSERT,UPDATE ON live_order_intents" not in CONSOLE_SCHEMA_SQL


class WorkerStore:
    def __init__(self):
        self.phases = []
        self.claims = 0
        self.outcome = None

    def checkpoint(self, job, phase, evidence):
        self.phases.append(phase)

    def begin_running(self, job):
        job["state"] = "running"

    def claim_effect(self, *args):
        self.claims += 1
        return True

    def finish_job(self, job, state, result=None, error=None):
        self.outcome = (state, result, error)
        return self.outcome

    def renew_lease(self, job):
        pass


class WorkerCommands:
    async def validate_worker_start(self, job):
        pass


class WorkerHandler:
    def __init__(self):
        self.posts = self.readbacks = 0

    async def execute(self, job):
        self.posts += 1
        return {"state": "succeeded", "result": {"evidence_id": "readback"}}

    async def reconcile(self, job):
        self.readbacks += 1
        return {
            "state": "reconciliation_pending",
            "result": {"reason": "provider evidence unavailable"},
        }


@pytest.mark.asyncio
async def test_worker_claim_precedes_execute_and_terminal_requires_result():
    store, handler = WorkerStore(), WorkerHandler()
    record = job()
    await process_job(store, WorkerCommands(), {"position.close": handler}, record)
    assert store.phases == ["prechecks", "submission"]
    assert store.claims == 1 and handler.posts == 1
    assert store.outcome[0] == "succeeded"


@pytest.mark.asyncio
async def test_worker_lease_takeover_is_get_only():
    store, handler = WorkerStore(), WorkerHandler()
    record = job()
    record.update(readback_only=True, effect_claimed=True, state="running")
    await process_job(store, WorkerCommands(), {"position.close": handler}, record)
    assert handler.posts == 0 and handler.readbacks == 1 and store.claims == 0
    assert store.outcome[0] == "reconciliation_pending"


@pytest.mark.asyncio
async def test_worker_timeout_keeps_ambiguous_claim_pending():
    class TimeoutHandler(WorkerHandler):
        async def execute(self, job):
            raise TimeoutError("must never expose provider token")

    store = WorkerStore()
    await process_job(store, WorkerCommands(), {"position.close": TimeoutHandler()}, job())
    assert store.outcome[0] == "reconciliation_pending"
    assert "token" not in str(store.outcome[2])


@pytest.mark.asyncio
async def test_worker_unapproved_job_cannot_call_handler():
    class Expired:
        async def validate_worker_start(self, job):
            raise CommandError("authorization_expired", "Expired")

    store, handler = WorkerStore(), WorkerHandler()
    await process_job(store, Expired(), {"position.close": handler}, job())
    assert store.outcome[0] == "expired"
    assert handler.posts == handler.readbacks == store.claims == 0


def test_lease_takeover_claim_sets_readback_only():
    previous = job()
    previous.update(state="running", effect_claimed=True)
    successor = {**previous, "lease_token": "successor", "claim_generation": 2, "phase": "readback"}
    store, connection, cursor = store_with(
        [previous, successor, None, {"id": 1}, {"id": 2}, {"id": 3}]
    )
    claimed = store.claim_job("worker2", {"position.close"})
    assert claimed["readback_only"] is True
    assert claimed["lease_token"] == "successor"
    claim_sql, claim_params = cursor.calls[1]
    assert claim_params[:2] == ("running", "readback")
    assert "SKIP LOCKED" in cursor.calls[0][0]
    assert any("pg_advisory_xact_lock" in sql for sql, _ in cursor.calls)
    assert connection.commits == 1


def test_preclaim_cancel_is_atomic_and_never_asserts_provider_cancel(action_catalog):
    record = job()
    cancelled = {**record, "state": "cancelled", "revision": 2, "cancellation_requested_at": NOW}
    store, _, cursor = store_with([record, actor(), cancelled, {"id": 1}, {"id": 2}, {"id": 3}])
    result = CommandService(store, {"position.close": Handler()}).cancel(
        record["id"],
        {"preview_digest": "digest", "resource_version": "1", "reason": "stop before claim"},
        PRINCIPAL,
    )
    assert result["state"] == "cancelled" and result["effect_claimed"] is False
    assert not any("live_order_intents" in sql for sql, _ in cursor.calls)


def test_running_cancel_only_requests_safe_stop(action_catalog):
    record = job()
    record.update(state="running", effect_claimed=True)
    pending = {**record, "revision": 2, "cancellation_requested_at": NOW}
    store, _, _ = store_with([record, actor(), pending, {"id": 1}, {"id": 2}, {"id": 3}])
    result = CommandService(store, {"position.close": Handler()}).cancel(
        record["id"],
        {"preview_digest": "digest", "resource_version": "1", "reason": "stop new children"},
        PRINCIPAL,
    )
    assert result["state"] == "running" and result["cancellation"]["accepted"]


def test_expired_preview_refuses_new_authority(action_catalog):
    p = preview()
    p["expires_at"] = NOW - timedelta(seconds=1)
    store, connection, cursor = store_with([p, actor(), None])
    with pytest.raises(CommandError) as error:
        CommandService(store, {"position.close": Handler()}).enqueue(
            request(), PRINCIPAL, str(uuid4())
        )
    assert error.value.code == "preview_expired" and connection.rollbacks == 1
    assert not any("INSERT INTO console_jobs" in sql for sql, _ in cursor.calls)


def test_idle_session_cannot_authorize_job(action_catalog):
    idle = actor()
    idle["last_seen_at"] = NOW - timedelta(minutes=31)
    store, _, cursor = store_with([preview(), idle])
    with pytest.raises(CommandError) as error:
        CommandService(store, {"position.close": Handler()}).enqueue(
            request(), PRINCIPAL, str(uuid4())
        )
    assert error.value.code == "session_expired"
    assert not any("INSERT INTO console_jobs" in sql for sql, _ in cursor.calls)


@pytest.mark.asyncio
async def test_cancel_race_after_running_before_outer_claim_is_terminal_cancelled():
    from fatty_trader.console.store import EffectCancelled

    class CancelledStore(WorkerStore):
        def claim_effect(self, *args):
            raise EffectCancelled("safe unclaimed cancellation")

    store, handler = CancelledStore(), WorkerHandler()
    await process_job(store, WorkerCommands(), {"position.close": handler}, job())
    assert store.outcome[0] == "cancelled"
    assert handler.posts == handler.readbacks == 0


def test_untrusted_reason_strings_redact_credentials_before_storage_digest():
    raw = {
        "reason": "operator note postgresql://owner:secret@host/db api_token=hidden Bearer hidden2",
        "pair_token": "economic-pair-identity",
    }
    normalized = safe_json(raw)
    assert normalized["reason"] == "operator note [redacted] [redacted] [redacted]"
    assert normalized["pair_token"] == "economic-pair-identity"
    assert digest(raw) == digest(normalized)
    assert "secret@host" not in canonical(raw) and "hidden" not in canonical(raw)


def test_enqueue_persists_redacted_reason_and_reference(action_catalog):
    record = job()
    grant = {"id": "grant", "verified_at": NOW, "expires_at": NOW + timedelta(minutes=5)}
    store, _, cursor = store_with(
        [preview(), actor(), None, record, grant, {"id": 1}, {"id": 2}, {"id": 3}]
    )
    body = {
        **request(),
        "reason": "review api_key=do-not-persist",
        "approval_reference": "postgresql://user:password@host/db",
    }
    CommandService(store, {"position.close": Handler()}).enqueue(body, PRINCIPAL, str(uuid4()))
    sql, params = next(
        (sql, params) for sql, params in cursor.calls if "INSERT INTO console_jobs" in sql
    )
    assert params[16] == "review [redacted]" and params[17] == "[redacted]"


def test_readback_and_identity_locks_do_not_conflict_with_financial_account():
    for action in (
        "intent.reconcile",
        "management.reconcile",
        "protection.observe",
        "report.export",
    ):
        record = {**job(), "action": action}
        store, _, cursor = store_with([])
        store._lock_resources(cursor, record, 120)
        assert cursor.calls == []
    record = {**job(), "action": "session.revoke", "arguments": {"session_id": "target-session"}}
    store, _, cursor = store_with([None, None])
    store._lock_resources(cursor, record, 120)
    keys = [params[0] for sql, params in cursor.calls if "pg_advisory_xact_lock" in sql]
    assert keys == sorted(keys)
    assert "account:LIVE:access" in keys
    assert "account:LIVE:account" not in keys
    assert all("FOR UPDATE OF l" in sql for sql, _ in cursor.calls if "JOIN console_jobs" in sql)


@pytest.mark.asyncio
async def test_cancellation_after_dispatch_claim_keeps_readback_pending():
    from fatty_trader.console.store import EffectCancelled

    class CancelAfterClaim(WorkerHandler):
        async def execute(self, job):
            raise EffectCancelled("next domain claim denied")

    store, handler = WorkerStore(), CancelAfterClaim()
    await process_job(store, WorkerCommands(), {"position.close": handler}, job())
    assert store.outcome[0] == "reconciliation_pending"
    assert store.outcome[1]["provider_effect_claimed"] is True


def test_effect_authority_denies_revoked_session_but_owned_readback_remains_available(
    action_catalog,
):
    record = job()
    record["effect_claimed"] = True
    revoked = {**actor(), "revoked_at": NOW}
    store, _, cursor = store_with([record, revoked])
    with pytest.raises(CommandError) as error:
        store.effect_authority(cursor, record)
    assert error.value.code == "session_expired"
    store, _, cursor = store_with([record])
    assert store.owned_job(cursor, record) == record


def test_effect_authority_requires_exact_session_action_scope_grant(action_catalog):
    record = {**job(), "effect_claimed": True}
    store, _, cursor = store_with([record, actor(), {"authority_now": NOW}, None])
    with pytest.raises(CommandError) as error:
        store.effect_authority(cursor, record)
    assert error.value.code == "step_up_required"
    sql, params = next(
        (sql, params) for sql, params in cursor.calls if "console_step_up_grants" in sql
    )
    assert "session_id=%s" in sql and "action=%s" in sql and "scope=%s" in sql
    assert params[:5] == ("actor", "session", record["id"], "digest", "position.close")


@pytest.mark.asyncio
async def test_pre_effect_takeover_accounts_for_no_post_without_replay():
    store, handler = WorkerStore(), WorkerHandler()
    record = {**job(), "readback_only": True, "state": "running"}
    await process_job(store, WorkerCommands(), {"position.close": handler}, record)
    assert handler.posts == handler.readbacks == store.claims == 0
    assert store.outcome[0] == "cancelled"
    assert store.outcome[1]["provider_effect_claimed"] is False


def test_bound_consent_is_consumed_once_and_cannot_be_replayed():
    cursor = Cursor([{"id": "consent-grant"}, None])
    service = CommandService(None, {})
    reviewed = preview()
    assert service._grant(cursor, PRINCIPAL, reviewed, "job-one")["id"] == "consent-grant"
    with pytest.raises(CommandError) as error:
        service._grant(cursor, PRINCIPAL, reviewed, "job-two")
    assert error.value.code == "consent_required"
    selects = [(sql, params) for sql, params in cursor.calls if sql.startswith("SELECT")]
    assert len(selects) == 2
    for sql, params in selects:
        assert "consumed_at IS NULL" in sql and "expires_at>now()" in sql
        assert "action=%s" in sql and "scope=%s" in sql and "session_id=%s" in sql
        assert params[:4] == ("actor", "session", "digest", "position.close")
    updates = [(sql, params) for sql, params in cursor.calls if sql.startswith("UPDATE")]
    assert len(updates) == 1 and updates[0][1] == ("job-one", "consent-grant")
