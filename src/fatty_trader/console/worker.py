"""Credential-owning worker; host execution is a separate allowlisted route."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from fatty_trader.console.commands import CommandError, CommandService
from fatty_trader.console.store import ConsoleStore, EffectCancelled, LostLease
from fatty_trader.execution.async_storage import _run_sync_storage

LOG = logging.getLogger(__name__)


def build_handlers(
    environ: Mapping[str, str], store: ConsoleStore, *, host: bool = False
) -> dict[str, Any]:
    from fatty_trader.console.operations import HOST_ACTIONS, OPERATIONS_ACTIONS, OperationsHandlers

    if host:
        from fatty_trader.console.runner import HostOperationsRunner

        operations = OperationsHandlers(environ, store, HostOperationsRunner(environ, store))
        return {action: operations for action in HOST_ACTIONS}
    from fatty_trader.console.trading import TRADING_ACTIONS, TradingHandlers

    operations = OperationsHandlers(environ, store)
    trading = TradingHandlers(environ, store)
    handlers: dict[str, Any] = {
        **{action: operations for action in OPERATIONS_ACTIONS - HOST_ACTIONS},
        **{action: trading for action in TRADING_ACTIONS},
    }
    from fatty_trader.console.auth import AuthHandlers

    access = AuthHandlers(environ, store)
    handlers.update(
        {
            action: access
            for action in (
                "access.invite",
                "access.change_roles",
                "access.disable",
                "session.revoke",
            )
        }
    )
    return handlers


def build_preview_handlers(environ: Mapping[str, str], store: ConsoleStore) -> dict[str, Any]:
    """Web construction creates no provider client and never imports a host runner."""
    from fatty_trader.console.auth import AuthHandlers
    from fatty_trader.console.operations import OPERATIONS_ACTIONS, OperationsHandlers
    from fatty_trader.console.trading import TRADING_ACTIONS, TradingHandlers

    operations = OperationsHandlers(environ, store)
    trading = TradingHandlers(environ, store)
    access = AuthHandlers(environ, store)
    return {
        **{action: operations for action in OPERATIONS_ACTIONS},
        **{action: trading for action in TRADING_ACTIONS},
        **{
            action: access
            for action in (
                "access.invite",
                "access.change_roles",
                "access.disable",
                "session.revoke",
            )
        },
    }


async def _renew(store: ConsoleStore, job: dict[str, Any], stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=20)
        except TimeoutError:
            await _run_sync_storage(store.renew_lease, job)


async def process_job(
    store: ConsoleStore, commands: CommandService, handlers: dict[str, Any], job: dict[str, Any]
) -> dict[str, Any] | None:
    stop = asyncio.Event()
    renewal = asyncio.create_task(_renew(store, job, stop))
    try:
        handler = handlers[job["action"]]
        if job.get("readback_only") and not job["effect_claimed"]:
            # Execute cannot begin until the outer claim commits. A successor
            # can therefore account for this pre-effect crash without a POST.
            return await _run_sync_storage(
                store.finish_job,
                job,
                "cancelled",
                {"code": "worker_lost_before_effect", "provider_effect_claimed": False},
            )
        if job.get("readback_only") or job["effect_claimed"]:
            await _run_sync_storage(
                store.checkpoint, job, "readback", {"post_replay_allowed": False}
            )
            outcome = await handler.reconcile(job)
        else:
            try:
                await commands.validate_worker_start(job)
            except CommandError as error:
                state = (
                    "cancelled"
                    if error.code == "cancelled"
                    else "expired"
                    if error.code in {"authorization_expired", "preview_expired"}
                    else "awaiting_approval"
                    if error.code in {"approval_expired", "step_up_required"}
                    else "rejected"
                )
                return await _run_sync_storage(
                    store.finish_job,
                    job,
                    state,
                    None,
                    {"code": error.code, "message": error.message},
                )
            await _run_sync_storage(store.checkpoint, job, "prechecks", {"validated": True})
            await _run_sync_storage(store.begin_running, job)
            # Domain adapters still persist their own canonical provider intents.
            # This outer fence guarantees a crashed dispatch is never re-executed.
            claimed = await _run_sync_storage(
                store.claim_effect,
                job,
                "adapter_dispatch",
                job["action"],
                job["arguments"],
                job["id"],
            )
            if not claimed:
                outcome = await handler.reconcile(job)
            else:
                job["effect_claimed"] = True
                await _run_sync_storage(
                    store.checkpoint, job, "submission", {"post_replay_allowed": False}
                )
                outcome = await handler.execute(job)
        if (
            not isinstance(outcome, dict)
            or outcome.get("state")
            not in {
                "succeeded",
                "failed",
                "reconciliation_pending",
                "partially_succeeded",
                "cancelled",
            }
            or not isinstance(outcome.get("result"), dict)
        ):
            outcome = {
                "state": "reconciliation_pending" if job["effect_claimed"] else "failed",
                "result": {
                    "code": "adapter_outcome_invalid",
                    "message": "Action completion evidence was not returned",
                },
            }
        # A stale worker never writes completion for its successor.
        return await _run_sync_storage(store.finish_job, job, outcome["state"], outcome["result"])
    except EffectCancelled:
        try:
            return await _run_sync_storage(
                store.finish_job,
                job,
                "reconciliation_pending" if job["effect_claimed"] else "cancelled",
                {
                    "code": "cancelled_at_effect_boundary",
                    "provider_effect_claimed": job["effect_claimed"],
                },
            )
        except LostLease:
            return None
    except CommandError as error:
        try:
            state = (
                "reconciliation_pending"
                if job["effect_claimed"]
                else "failed"
                if job["state"] == "running"
                else "expired"
                if error.code in {"authorization_expired", "preview_expired"}
                else "awaiting_approval"
                if error.code in {"approval_expired", "step_up_required"}
                else "rejected"
            )
            return await _run_sync_storage(
                store.finish_job, job, state, None, {"code": error.code, "message": error.message}
            )
        except LostLease:
            return None
    except LostLease:
        LOG.warning("Console job lease lost; no successor acknowledgment written")
        return None
    except Exception as error:
        LOG.warning(
            "Console action interrupted (%s); claimed effects require readback",
            type(error).__name__,
        )
        try:
            return await _run_sync_storage(
                store.finish_job,
                job,
                "reconciliation_pending"
                if job["effect_claimed"]
                else "rejected"
                if job["state"] == "queued"
                else "failed",
                None,
                {"code": "adapter_interrupted", "exception_type": type(error).__name__},
            )
        except LostLease:
            return None
    finally:
        stop.set()
        with suppress(LostLease):
            await renewal


async def run_console_worker(environ: Mapping[str, str], *, host: bool = False) -> None:
    if environ.get("FATTY_PRODUCTION_LIVE_ONLY") == "1":
        from fatty_trader.production_policy import validate_production_live_policy

        validate_production_live_policy(environ)
    store = ConsoleStore()
    handlers = build_handlers(environ, store, host=host)
    commands = CommandService(store, handlers)
    collector = None
    if not host:
        from fatty_trader.console.reads import SnapshotCollector

        collector = SnapshotCollector(environ, store)
    worker_id = ("console-ops-" if host else "console-") + str(uuid4())
    interval = max(1, float(environ.get("CONSOLE_POLL_SECONDS", "2")))
    collection_interval = max(5, float(environ.get("CONSOLE_SNAPSHOT_SECONDS", "10")))
    next_collection = 0.0
    heartbeat = Path(environ.get("CONSOLE_HEARTBEAT_FILE", "/tmp/console-worker-heartbeat"))
    while True:
        if host and time.monotonic() >= next_collection:
            runner = next(iter(handlers.values())).runner
            try:
                await asyncio.wait_for(_run_sync_storage(runner.observe), timeout=60)
            except Exception as error:
                await _run_sync_storage(
                    store.put_snapshot,
                    "services",
                    runner.scope,
                    None,
                    {
                        "code": "host_observation_unavailable",
                        "exception_type": type(error).__name__,
                    },
                )
                LOG.warning("Host observation unavailable (%s)", type(error).__name__)
            next_collection = time.monotonic() + collection_interval
        if collector is not None and time.monotonic() >= next_collection:
            try:
                await asyncio.wait_for(collector.collect_once(), timeout=60)
            except Exception as error:
                LOG.warning("Console observation unavailable (%s)", type(error).__name__)
            next_collection = time.monotonic() + collection_interval
        job = await _run_sync_storage(store.claim_job, worker_id, set(handlers))
        if job:
            await process_job(store, commands, handlers, job)
        # Written only after a successful durable queue poll; provider truth has
        # its separate timestamps/errors and is not implied by worker liveness.
        heartbeat.write_text(str(time.time()), encoding="ascii")
        await asyncio.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--host", action="store_true")
    args = parser.parse_args()
    if args.check:
        path = Path(os.environ.get("CONSOLE_HEARTBEAT_FILE", "/tmp/console-worker-heartbeat"))
        try:
            age = time.time() - float(path.read_text(encoding="ascii"))
            if not 0 <= age <= 90:
                raise ValueError("stale")
        except (OSError, ValueError):
            raise SystemExit(1) from None
        return
    asyncio.run(run_console_worker(os.environ, host=args.host))


if __name__ == "__main__":
    main()
