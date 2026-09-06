from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.integration import analyze_with_fallback
from fatty_trader.domain.enums import Exchange
from fatty_trader.domain.models import CanonicalSignal
from fatty_trader.intake.persistence import RawTelegramMessage, revision_hash
from fatty_trader.storage.memory import Dispatch, InMemoryDispatchRepository


class PaperPipeline:
    """Idempotent RECEIVED -> analysis -> DEMO dispatch seam; never submits orders."""

    def __init__(
        self,
        *,
        runner: Callable[[str], CodexRunResult] | Any,
        dispatch_repository: InMemoryDispatchRepository | None = None,
    ) -> None:
        self._runner = runner
        self.dispatch_repository = dispatch_repository or InMemoryDispatchRepository()
        self._states: dict[tuple[int, int, str], str] = {}
        self._signals: dict[tuple[int, int, str], CanonicalSignal] = {}

    def process(self, message: RawTelegramMessage) -> tuple[Dispatch, ...]:
        key = (message.channel_id, message.message_id, message.revision_hash)
        state = self._states.get(key)
        if state in {"ANALYZED", "FAILED"}:
            signal = self._signals.get(key)
            return tuple(self.dispatch_repository.by_signal(signal)) if signal else ()
        self._states[key] = "RECEIVED"
        try:
            result = analyze_with_fallback(
                text=message.raw_text, message_id=message.message_id, codex_runner=self._run
            )
            self._states[key] = "ANALYZED"
            if result.signal is None:
                return ()
            self._signals[key] = result.signal
            return tuple(
                self.dispatch_repository.create(result.signal, exchange) for exchange in Exchange
            )
        except Exception:
            self._states[key] = "FAILED"
            return ()

    def _run(self, prompt: str) -> CodexRunResult:
        if callable(self._runner):
            result = self._runner(prompt)
            if not isinstance(result, CodexRunResult):
                raise TypeError("runner must return CodexRunResult")
            return result
        return cast(CodexRunResult, self._runner.run(prompt))

    def state(self, message: RawTelegramMessage) -> str:
        return self._states[(message.channel_id, message.message_id, message.revision_hash)]

    @property
    def canonical_signal_count(self) -> int:
        return len(self._signals)


def observed_messages() -> tuple[RawTelegramMessage, ...]:
    texts = (
        (16084, "$ETH 6% up"),
        (16083, "#PYTH $PYTH LONG TRADE ENTRY: 0.0568 TARGET: 0.0708 STOPLOSS: 0.05462"),
        (16082, "$GIGGLE tp1 booked"),
        (16081, "#GIGGLE $GIGGLE LONG TRADE ENTRY: 36.85 TARGET: 43 STOPLOSS: 35.45"),
        (16080, "$BTC todays move https://x.com/learnernoearner/status/2095142987630047675?s=46"),
    )
    now = datetime.now(UTC)
    return tuple(
        RawTelegramMessage(
            1,
            mid,
            revision_hash(raw_text=text, reply_to_message_id=None, has_media=False),
            text,
            now,
        )
        for mid, text in texts
    )
