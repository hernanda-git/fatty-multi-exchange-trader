"""Keep synchronous storage off the loop without abandoning in-flight operations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")


async def _run_sync_storage(operation: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """Drain storage before acknowledging cancellation, including cycle timeouts.

    Repeated cancellation cannot cancel the thread wrapper. The first cancellation
    is preserved, with any storage failure chained as its cause. Uncancelled
    storage failures propagate unchanged.
    """
    pending = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(pending)
        except asyncio.CancelledError as exc:
            if pending.cancelled():
                raise
            if cancellation is None:
                cancellation = exc
            continue
        except Exception as exc:
            if cancellation is not None:
                raise cancellation from exc
            raise
        if cancellation is not None:
            raise cancellation
        return result
