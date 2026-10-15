"""Real isolated PostgreSQL proofs for the durable console command boundary."""

# ruff: noqa: F401, F811
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_postgres_migration_and_margin_admission import _connect, _migrate, postgres_schema

from fatty_trader.console import commands as command_module
from fatty_trader.console.commands import POLICY_VERSION, CommandError, CommandService
from fatty_trader.console.store import ConsoleStore, LostLease

SCOPE = {"account_id": "isolated-account", "venue": "bitget", "environment": "LIVE"}


class Handler:
    def current_resource_version(self, *args):
        return "resource-1"

    async def preview(self, *args):
        return {
            "resource_version": "resource-1",
            "normalized_arguments": {},
            "effects": [{"summary": "isolated test only"}],
            "gates": [{"name": "fixture-gate", "verdict": "pass"}],
            "warnings": [],
        }


@pytest.fixture
def console_db(postgres_schema, monkeypatch):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    store = ConsoleStore(lambda: _connect(dsn, schema))
    schemas = {
        name: {"type": "object", "properties": {}, "additionalProperties": False}
        for name in ("position.close", "release.deploy", "intent.reconcile", "session.revoke")
    }
    policies = {
        "position.close": {"role": "operator", "danger_tier": 2},
        "release.deploy": {"role": "owner", "danger_tier": 3},
        "intent.reconcile": {"role": "operator", "danger_tier": 0},
        "session.revoke": {"role": "access_admin", "danger_tier": 2},
    }
    monkeypatch.setattr(command_module, "catalog", lambda: (schemas, policies))
    service = CommandService(store, {name: Handler() for name in schemas})
    principals = []
    for number in range(2):
        principal = {
            "id": f"fixture-owner-{number}",
            "session_id": f"fixture-session-{number}",
            "roles": ["owner"],
        }
        with store.connection() as connection:
            cursor = store.cursor(connection)
            cursor.execute(
                "INSERT INTO console_principals(id,display_name,roles,scopes) VALUES(%s,%s,%s,%s)",
                (principal["id"], "isolated fixture", Jsonb(["owner"]), Jsonb([SCOPE])),
            )
            cursor.execute(
                (
                    "INSERT INTO console_sessions\n"
                    "                           "
                    "(id,token_hash,principal_id,csrf_token,authenticated_at,expires_at,ab"
                    "solute_expires_at)\n"
                    "                           "
                    "VALUES(%s,%s,%s,'fixture-csrf',now(),now()+interval '1 "
                    "hour',now()+interval '1 hour')"
                ),
                (principal["session_id"], str(uuid4()), principal["id"]),
            )
            cursor.execute(
                "INSERT INTO console_credentials(id,principal_id,public_key) VALUES(%s,%s,%s)",
                (str(uuid4()), principal["id"], b"isolated fixture not a real credential"),
            )
        principals.append(principal)
    return store, service, principals, dsn, schema


def grant(store, principal, preview):
    store.execute(
        """INSERT INTO console_step_up_grants
                  (id,principal_id,session_id,preview_digest,action,scope,verified_at,expires_at)
                  VALUES(%s,%s,%s,%s,%s,%s,now(),now()+interval '5 minutes')""",
        (
            str(uuid4()),
            principal["id"],
            principal["session_id"],
            preview["preview_digest"],
            preview["action"],
            Jsonb(SCOPE),
        ),
    )


async def prepared(console_db, action="position.close"):
    store, service, principals, *_ = console_db
    p = await service.preview(
        {
            "action": action,
            "arguments": {},
            "target_refs": [],
            "reason": "isolated fixture approval",
        },
        principals[0],
        SCOPE,
    )
    grant(store, principals[0], p)
    request = {
        "preview_id": p["id"],
        "preview_digest": p["preview_digest"],
        "resource_version": p["resource_version"],
        "policy_version": POLICY_VERSION,
        "reason": "isolated fixture approval",
        "approval_reference": "fixture-approval",
        "confirmation_phrase": p["confirmation_phrase"],
    }
    return p, request


@pytest.mark.asyncio
async def test_concurrent_same_key_commits_one_job_and_one_grant(console_db):
    store, service, principals, *_ = console_db
    _, request = await prepared(console_db)
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.enqueue, request, principals[0], key) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert results[0]["id"] == results[1]["id"]
    assert store.one("SELECT count(*) AS count FROM console_jobs")["count"] == 1
    assert store.one("SELECT count(*) AS count FROM console_idempotency_receipts")["count"] == 1
    assert (
        store.one(
            "SELECT count(*) AS count FROM console_step_up_grants WHERE consumed_at IS NOT NULL"
        )["count"]
        == 1
    )
    changed = {**request, "reason": "changed economic request"}
    with pytest.raises(CommandError, match="different request"):
        service.enqueue(changed, principals[0], key)


@pytest.mark.asyncio
async def test_two_person_approval_and_database_binding(console_db):
    store, service, principals, *_ = console_db
    p, request = await prepared(console_db, "release.deploy")
    job = service.enqueue(request, principals[0], str(uuid4()))
    assert job["state"] == "awaiting_approval"
    control = {
        "preview_digest": p["preview_digest"],
        "resource_version": job["resource_version"],
        "reason": "distinct owner reviewed",
    }
    with pytest.raises(CommandError) as refused:
        service.approve(job["id"], control, principals[0])
    assert refused.value.code == "distinct_approver_required"
    grant(store, principals[1], p)
    approved = service.approve(job["id"], control, principals[1])
    assert approved["state"] == "queued"
    with pytest.raises(psycopg.errors.RaiseException):
        store.execute(
            (
                "INSERT INTO console_approvals\n"
                "                      "
                "(id,job_id,principal_id,session_id,preview_digest,policy_version,reso"
                "urce_version,\n"
                "                      decision,reason,step_up_at,expires_at)\n"
                "                      "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,'approved','forbidden self "
                "approval',now(),now()+interval '1 minute')"
            ),
            (
                str(uuid4()),
                job["id"],
                principals[0]["id"],
                principals[0]["session_id"],
                p["preview_digest"],
                POLICY_VERSION,
                p["resource_version"],
            ),
        )


@pytest.mark.asyncio
async def test_worker_takeover_token_fence_and_ambiguous_account_lock(console_db):
    store, service, principals, *_ = console_db
    _, request = await prepared(console_db)
    first = service.enqueue(request, principals[0], str(uuid4()))
    claimed = store.claim_job("worker-one", {"position.close"})
    assert claimed["id"] == first["id"]
    await service.validate_worker_start(claimed)
    store.begin_running(claimed)
    assert store.claim_effect(claimed, "close", "owned-close", {}, "stable-fixture-close")
    store.execute(
        "UPDATE console_jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (first["id"],)
    )
    successor = store.claim_job("worker-two", {"position.close"})
    assert (
        successor["readback_only"]
        and successor["claim_generation"] == claimed["claim_generation"] + 1
    )
    with pytest.raises(LostLease):
        store.finish_job(claimed, "succeeded", {"fake": "must not be persisted"})
    store.finish_job(
        successor, "reconciliation_pending", {"reason": "real fixture GET unavailable"}
    )
    _, next_request = await prepared(console_db)
    service.enqueue(next_request, principals[0], str(uuid4()))
    assert store.claim_job("worker-three", {"position.close"}) is None
    assert store.one("SELECT count(*) AS count FROM console_job_effects")["count"] == 1


@pytest.mark.asyncio
async def test_immutable_audit_preview_terminal_job_and_web_privilege_boundary(console_db):
    store, service, principals, dsn, schema = console_db
    _, request = await prepared(console_db)
    job = service.enqueue(request, principals[0], str(uuid4()))
    cancelled = service.cancel(
        job["id"],
        {
            "preview_digest": job["preview_digest"],
            "resource_version": job["resource_version"],
            "reason": "cancel isolated unclaimed fixture",
        },
        principals[0],
    )
    assert cancelled["state"] == "cancelled"
    for sql, params in [
        ("UPDATE console_audit_events SET outcome=%s", ("fabricated",)),
        ("UPDATE console_previews SET arguments=%s", (Jsonb({"forbidden": "override"}),)),
        ("UPDATE console_jobs SET state=%s WHERE id=%s", ("queued", job["id"])),
    ]:
        with pytest.raises(psycopg.errors.RaiseException):
            store.execute(sql, params)
    with _connect(dsn, schema) as connection:
        assert (
            connection.execute(
                "SELECT has_table_privilege('fatty_console_web','live_order_intents','UPDATE')"
            ).fetchone()[0]
            is False
        )
        assert (
            connection.execute(
                "SELECT has_table_privilege('fatty_console_web','console_jobs','INSERT')"
            ).fetchone()[0]
            is True
        )
        connection.execute("SET ROLE fatty_console_web")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("UPDATE live_order_intents SET state=state")


@pytest.mark.asyncio
async def test_snapshot_errors_do_not_become_zero_or_success(console_db):
    store, *_ = console_db
    successful = store.put_snapshot(
        "account", SCOPE, {"values": {"equity": "12.34"}}, observed_at=datetime.now(UTC)
    )
    failed = store.put_snapshot("account", SCOPE, None, error={"code": "provider_read_unavailable"})
    latest = store.latest_snapshot("account", SCOPE)
    assert latest["data"] is None and latest["observed_at"] is None
    assert failed["revision"] > successful["revision"]
    assert store.event_cursor() > 0
    assert store.events(0, SCOPE)


@pytest.mark.asyncio
async def test_same_key_across_distinct_previews_returns_conflict_not_unique_failure(console_db):
    store, service, principals, *_ = console_db
    _, first = await prepared(console_db)
    _, second = await prepared(console_db)
    key = str(uuid4())

    def enqueue(body):
        try:
            return service.enqueue(body, principals[0], key)
        except CommandError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(enqueue, (first, second)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "idempotency_conflict" in results
    assert store.one("SELECT count(*) AS count FROM console_jobs")["count"] == 1
    assert (
        store.one(
            "SELECT count(*) AS count FROM console_step_up_grants WHERE consumed_at IS NOT NULL"
        )["count"]
        == 1
    )


@pytest.mark.asyncio
async def test_ambiguous_financial_job_allows_authorized_readback_and_access_recovery(console_db):
    store, service, principals, *_ = console_db
    _, body = await prepared(console_db)
    original = service.enqueue(body, principals[0], str(uuid4()))
    claimed = store.claim_job("financial-worker", {"position.close"})
    await service.validate_worker_start(claimed)
    store.begin_running(claimed)
    store.claim_effect(claimed, "fixture-close", "fixture-only", {}, "stable-fixture-close")
    store.finish_job(claimed, "reconciliation_pending", {"code": "readback_unavailable"})
    _, read_body = await prepared(console_db, "intent.reconcile")
    read_job = service.enqueue(read_body, principals[0], str(uuid4()))
    read_claim = store.claim_job("readback-worker", {"intent.reconcile"})
    assert read_claim["id"] == read_job["id"]
    _, access_body = await prepared(console_db, "session.revoke")
    access_job = service.enqueue(access_body, principals[0], str(uuid4()))
    access_claim = store.claim_job("identity-worker", {"session.revoke"})
    assert access_claim["id"] == access_job["id"]
    _, mutation_body = await prepared(console_db)
    service.enqueue(mutation_body, principals[0], str(uuid4()))
    assert store.claim_job("blocked-financial-worker", {"position.close"}) is None
    assert (
        store.one("SELECT state FROM console_jobs WHERE id=%s", (original["id"],))["state"]
        == "reconciliation_pending"
    )


@pytest.mark.asyncio
async def test_consumed_consent_and_job_authority_cannot_be_rewritten(console_db):
    store, service, principals, *_ = console_db
    p, body = await prepared(console_db, "release.deploy")
    created = service.enqueue(body, principals[0], str(uuid4()))
    grant(store, principals[1], p)
    service.approve(
        created["id"],
        {
            "preview_digest": p["preview_digest"],
            "resource_version": created["resource_version"],
            "reason": "distinct review",
        },
        principals[1],
    )
    claimed = store.claim_job("fixture-worker", {"release.deploy"})
    await service.validate_worker_start(claimed)
    for sql, params in (
        ("UPDATE console_approvals SET consumed_at=NULL WHERE job_id=%s", (created["id"],)),
        (
            "UPDATE console_step_up_grants SET consumed_at=NULL,job_id=NULL WHERE job_id=%s",
            (created["id"],),
        ),
        ("UPDATE console_previews SET consumed_by=NULL WHERE id=%s", (p["id"],)),
        (
            "UPDATE console_jobs SET arguments=%s WHERE id=%s",
            (Jsonb({"changed": True}), created["id"]),
        ),
        (
            "UPDATE console_idempotency_receipts SET payload_digest=%s WHERE job_id=%s",
            ("changed", created["id"]),
        ),
    ):
        with pytest.raises(psycopg.errors.RaiseException):
            store.execute(sql, params)


@pytest.mark.asyncio
async def test_session_revocation_denies_new_effect_without_blocking_old_readback(console_db):
    store, service, principals, *_ = console_db
    _, body = await prepared(console_db)
    service.enqueue(body, principals[0], str(uuid4()))
    claimed = store.claim_job("fixture-worker", {"position.close"})
    await service.validate_worker_start(claimed)
    store.begin_running(claimed)
    store.claim_effect(claimed, "dispatch", "fixture-only", {}, "stable-fixture")
    store.execute(
        "UPDATE console_sessions SET revoked_at=now() WHERE id=%s", (principals[0]["session_id"],)
    )
    with store.connection() as connection:
        cursor = store.cursor(connection)
        assert store.owned_job(cursor, claimed)["id"] == claimed["id"]
        with pytest.raises(CommandError) as refused:
            store.effect_authority(cursor, claimed)
        assert refused.value.code == "session_expired"
    store.finish_job(claimed, "reconciliation_pending", {"code": "GET_readback_required"})


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["signal.annotate", "incident.acknowledge"])
async def test_operator_notes_preserve_source_and_latch_and_refuse_stale_evidence(
    console_db, monkeypatch, action
):
    from fatty_trader.console.operations import (
        ACTION_POLICIES,
        ACTION_SCHEMAS,
        OperationRefused,
        OperationsHandlers,
    )
    from fatty_trader.console.worker import process_job

    store, _, principals, *_ = console_db
    source_id = str(uuid4())
    store.execute(
        "INSERT INTO telegram_messages"
        "(id,channel_id,message_id,revision_hash,raw_text,intake_state) "
        "VALUES(%s,-100123,42,%s,'historical source','EXPIRED')",
        (source_id, "a" * 64),
    )
    store.execute(
        "INSERT INTO venue_kill_switches(scope,active,reason) "
        "VALUES('bitget',true,'clock-skew-unavailable')"
    )
    source_before = store.one("SELECT * FROM telegram_messages WHERE id=%s", (source_id,))
    latch_before = store.one("SELECT * FROM venue_kill_switches WHERE scope='bitget'")
    handler = OperationsHandlers({"CONSOLE_ACCOUNT_ID": SCOPE["account_id"]}, store)
    monkeypatch.setattr(command_module, "catalog", lambda: (ACTION_SCHEMAS, ACTION_POLICIES))
    handlers = {action: handler}
    service = CommandService(store, handlers)
    arguments = (
        {"message_id": source_id, "revision": "a" * 64, "note": "Reviewed; no replay approved"}
        if action == "signal.annotate"
        else {"latch_scope": "bitget", "expected_reason": "clock-skew-unavailable"}
    )
    preview = await service.preview(
        {"action": action, "arguments": arguments, "target_refs": [], "reason": "Review evidence"},
        principals[0],
        SCOPE,
    )
    request = {
        "preview_id": preview["id"],
        "preview_digest": preview["preview_digest"],
        "resource_version": preview["resource_version"],
        "policy_version": POLICY_VERSION,
        "reason": "Review evidence",
    }
    service.enqueue(request, principals[0], str(uuid4()))
    claimed = store.claim_job("metadata-proof", {action})
    result = await process_job(store, service, handlers, claimed)
    assert result["state"] == "succeeded"
    assert store.one("SELECT * FROM telegram_messages WHERE id=%s", (source_id,)) == source_before
    assert store.one("SELECT * FROM venue_kill_switches WHERE scope='bitget'") == latch_before
    receipt = store.one(
        "SELECT evidence FROM console_operation_receipts WHERE job_id=%s", (claimed["id"],)
    )["evidence"]
    assert receipt.items() >= arguments.items()
    stale = (
        {**arguments, "revision": "b" * 64}
        if action == "signal.annotate"
        else {**arguments, "expected_reason": "different incident"}
    )
    with pytest.raises(OperationRefused):
        await handler.preview(action, stale, [], SCOPE)
