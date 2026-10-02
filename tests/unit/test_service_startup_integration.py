"""Behavioral startup assembly; no provider or Telegram I/O."""

from types import SimpleNamespace

import pytest

from fatty_trader import service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ready,error", [(False, None), (False, RuntimeError("DB unavailable")), (True, None)]
)
async def test_dispatch_loop_waits_for_full_recovery_and_always_closes(monkeypatch, ready, error):
    from fatty_trader.execution import bitget_dispatcher as module

    events = []

    class Execution:
        recovery_ready = False

        async def recover_entry_lifecycles(self):
            events.append("recover")
            if error:
                raise error
            self.recovery_ready = ready

        async def reconcile_active_reservations(self):
            events.append("legacy")

    class Client:
        async def aclose(self):
            events.append("close")

    class Stop(Exception):
        pass

    class Dispatcher:
        def __init__(self, *args, **kwargs):
            pass

        async def run_once(self, *args):
            events.append("dispatch")
            raise Stop

    monkeypatch.setattr(
        service,
        "build_bitget_execution_runtime",
        lambda _: SimpleNamespace(execution=Execution(), client=Client(), preflight=lambda _: None),
    )
    monkeypatch.setattr(service, "build_bitget_protection_admission", lambda _: None)
    monkeypatch.setattr(module, "BitgetDispatcher", Dispatcher)
    with pytest.raises(Stop if ready else RuntimeError):
        await service.run_bitget_dispatcher({})
    assert events == (["recover", "dispatch", "close"] if ready else ["recover", "close"])


@pytest.mark.asyncio
async def test_operator_assembly_uses_shared_snapshot_reader(monkeypatch):
    import psycopg

    from fatty_trader.exchanges.bitget import client
    from fatty_trader.operator import bitget_gateway, health, health_report_format, telegram_polling

    snapshot = dict(
        positions=None,
        pending_orders=[],
        sltp={},
        pnl={},
        messages=[],
        metrics={},
        account={},
        modes={},
        codex={},
        services={},
    )
    received = []
    monkeypatch.setattr(client, "BitgetRestClient", lambda *a, **k: object())
    gateway = object()
    monkeypatch.setattr(bitget_gateway, "BitgetOperatorGateway", lambda *a: gateway)

    def load(actual_gateway, connection_factory, **kwargs):
        assert actual_gateway is gateway
        assert connection_factory is psycopg.connect
        assert kwargs == dict(mode="DEMO", venue_mode="DEMO", execution_enabled=False)
        return snapshot

    monkeypatch.setattr(health, "load_operator_health_snapshot", load)
    monkeypatch.setattr(
        health_report_format,
        "format_report",
        lambda **data: received.append(data) or "shared health",
    )

    class Api:
        def __init__(self, *a):
            pass

        def set_my_commands(self):
            pass

        def fetch_updates(self):
            raise AssertionError("no Telegram I/O")

        def send_reply(self, *a):
            raise AssertionError("no Telegram I/O")

    def poller(**kwargs):
        assert kwargs["command_service"]._on_health() == "shared health"
        return object()

    async def no_thread(fn):
        pass

    monkeypatch.setattr(telegram_polling, "TelegramBotApi", Api)
    monkeypatch.setattr(telegram_polling, "TelegramCommandPoller", poller)
    monkeypatch.setattr(service.asyncio, "to_thread", no_thread)
    await service.run_operator_bot(
        dict(
            TG_BOT_TOKEN="fake",
            TG_OPERATOR_ID="42",
            BITGET_API_KEY="fake",
            BITGET_API_SECRET="fake",
            BITGET_API_PASSPHRASE="fake",
        )
    )
    assert received == [snapshot]


def _paper_intake_connection(monkeypatch, image, source, timeline):
    import psycopg
    from psycopg.rows import dict_row

    class Cursor:
        def execute(self, query, parameters):
            assert query == (
                "SELECT raw_text, revision_hash, media_sha256 FROM telegram_messages "
                "WHERE channel_id = %s AND message_id = %s AND media_path = %s"
            )
            assert parameters == (-1003763643270, 7, str(image))
            timeline.append("lookup")

        def fetchone(self):
            timeline.append("identity")
            return source

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            timeline.append("close")

    def connect(*args, **kwargs):
        assert args == ()
        assert kwargs == {"row_factory": dict_row}
        return Connection()

    monkeypatch.setattr(psycopg, "connect", connect)


@pytest.mark.asyncio
async def test_paper_image_assembly_uses_keyword_only_runner(monkeypatch, tmp_path):
    import hashlib
    import json
    from decimal import Decimal

    from fatty_trader.intake.persistence import revision_hash
    from fatty_trader.kaka import worker
    from fatty_trader.kaka.parser import KakaEventType

    image = tmp_path / "chart.png"
    image.write_bytes(b"offline fixture")
    text = "BTC chart with marked entry, stop and target"
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    revision = revision_hash(
        raw_text=text,
        reply_to_message_id=None,
        has_media=True,
        media_identity="offline-chart-7",
    )
    source = {"raw_text": text, "revision_hash": revision, "media_sha256": digest}
    calls = []
    events = []
    timeline = []
    _paper_intake_connection(monkeypatch, image, source, timeline)

    class Runner:
        def run(self, prompt, *, image_paths=()):
            assert prompt.endswith("CAPTION:\n" + text)
            timeline.append("runner")
            calls.append(image_paths)
            return SimpleNamespace(
                succeeded=True,
                stdout=(
                    '{"message":"chart","setup":{"action":"ENTER","asset":"BTC","side":"LON'
                    'G","entry":"100","stop_loss":"90","take_profits":["110"]}}'
                ),
            )

    class Done(BaseException):
        pass

    def batch(connection_factory, **kwargs):
        assert kwargs["channel_id"] == -1003763643270
        events.append(kwargs["image_analyzer"](str(image), message_id=7))
        raise Done

    def build_runner(environ):
        assert environ == {"PAPER_KAKA_IMAGE_ANALYSIS": "1"}
        timeline.append("build_runner")
        return Runner()

    monkeypatch.setattr(service, "build_codex_runner", build_runner)
    monkeypatch.setattr(worker, "process_paper_batch", batch)
    with pytest.raises(Done):
        await service.run_paper_kaka({"PAPER_KAKA_IMAGE_ANALYSIS": "1"})
    assert timeline == ["lookup", "identity", "close", "build_runner", "runner"]
    assert calls == [(str(image),)]
    assert len(events) == 1
    event = events[0]
    assert event.type is KakaEventType.OPEN
    assert event.symbol == "BTCUSDT"
    assert event.side == "LONG"
    assert event.entry == Decimal("100")
    assert event.stop_loss == Decimal("90")
    assert event.take_profit == Decimal("110")
    assert event.message_id == 7
    audit = {"kind": "image", "source_revision": revision, "media_sha256": digest}
    assert json.loads(event.raw) == audit
    assert event.raw == json.dumps(audit, separators=(",", ":"))


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["missing", "hash-mismatch", "invalid-revision"])
async def test_paper_image_assembly_refuses_untrusted_identity(monkeypatch, tmp_path, identity):
    import hashlib

    from fatty_trader.intake.persistence import revision_hash
    from fatty_trader.kaka import worker

    image = tmp_path / "chart.png"
    image.write_bytes(b"offline fixture")
    source = {
        "raw_text": "chart",
        "revision_hash": revision_hash(
            raw_text="chart",
            reply_to_message_id=None,
            has_media=True,
            media_identity="offline-chart-7",
        ),
        "media_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
    }
    if identity == "missing":
        source = None
    elif identity == "hash-mismatch":
        source["media_sha256"] = hashlib.sha256(b"different media").hexdigest()
    else:
        source["revision_hash"] = "image:7"
    timeline = []
    _paper_intake_connection(monkeypatch, image, source, timeline)

    def forbidden_runner(environ):
        raise AssertionError("untrusted intake must not reach the runner")

    class Done(BaseException):
        pass

    def batch(connection_factory, **kwargs):
        analyzer = kwargs["image_analyzer"]
        if identity == "missing":
            assert analyzer(str(image), message_id=7) is None
        else:
            reason = (
                "media does not match intake hash"
                if identity == "hash-mismatch"
                else "invalid canonical source revision"
            )
            with pytest.raises(ValueError, match=reason):
                analyzer(str(image), message_id=7)
        raise Done

    monkeypatch.setattr(service, "build_codex_runner", forbidden_runner)
    monkeypatch.setattr(worker, "process_paper_batch", batch)
    with pytest.raises(Done):
        await service.run_paper_kaka({"PAPER_KAKA_IMAGE_ANALYSIS": "1"})
    assert timeline == ["lookup", "identity", "close"]


@pytest.mark.asyncio
async def test_digest_window_is_exactly_24_hours(monkeypatch):
    from datetime import UTC, datetime, timedelta

    import psycopg

    from fatty_trader.kaka import worker

    now = datetime(2026, 10, 2, 18, 37, tzinfo=UTC)

    class Clock:
        @staticmethod
        def now(zone):
            assert zone is UTC
            return now

    calls = []

    class Connection:
        def cursor(self):
            return object()

        def commit(self):
            calls.append("commit")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(service, "datetime", Clock)
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: Connection())
    monkeypatch.setattr(
        worker, "build_digest_text", lambda cursor, **kwargs: calls.append(kwargs) or "digest"
    )
    monkeypatch.setattr(worker, "enqueue_digest", lambda cursor, **kwargs: True)
    await service._maybe_enqueue_digest(18)
    assert calls == [{"day_start": (now - timedelta(hours=24)).isoformat()}, "commit", "close"]


def test_runtime_injects_durable_dispatch_repository(monkeypatch):
    from fatty_trader.execution import bitget_dispatch_repository

    expected = object()
    monkeypatch.setattr(
        bitget_dispatch_repository, "PostgresBitgetDispatchRepository", lambda factory: expected
    )
    runtime = service.build_bitget_execution_runtime(
        dict(
            TRADER_MODE="DEMO",
            BITGET_MODE="DEMO",
            BITGET_EXECUTION_ENABLED="1",
            BITGET_API_KEY="fake",
            BITGET_API_SECRET="fake",
            BITGET_API_PASSPHRASE="fake",
            BITGET_CANARY_MAX_ORDERS="1",
            BITGET_CANARY_SYMBOL="BTCUSDT",
            BITGET_APPROVAL_REFERENCE="offline",
            BITGET_MAX_CLOCK_SKEW_MS="5000",
            BITGET_MAX_MARGIN_PER_TRADE_USDT="1",
        ),
        client_factory=lambda *a, **k: object(),
        intent_store_factory=lambda: object(),
    )
    assert runtime.execution._dispatch_repository is expected
    assert runtime.execution.recovery_ready is False
