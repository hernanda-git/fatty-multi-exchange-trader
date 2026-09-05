from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fatty_trader.config.bitget import BitgetLiveConfig, live_canary_allowed
from fatty_trader.execution.service import submit_live_entry

if TYPE_CHECKING:
    from fatty_trader.exchanges.bitget.live import (
        BitgetLiveClientProtocol,
        LiveEntryRequest,
        LiveEntryResult,
        LiveIntentStoreProtocol,
    )


class LiveDispatchDisabled(RuntimeError):
    """Raised before any provider mutation when the explicit cutover gate is closed."""


@dataclass(frozen=True)
class LiveDispatchExecutor:
    """The only live-dispatch seam; it remains non-mutating while the gate is closed."""

    client: BitgetLiveClientProtocol
    store: LiveIntentStoreProtocol
    config: BitgetLiveConfig
    authenticated_read_passed: bool
    implementation_enabled: bool
    safety_checks_passed: bool
    approval_token: str | None

    @property
    def enabled(self) -> bool:
        return live_canary_allowed(
            self.config,
            authenticated_read_passed=self.authenticated_read_passed,
            implementation_enabled=self.implementation_enabled,
            safety_checks_passed=self.safety_checks_passed,
            approval_token=self.approval_token,
        )

    def submit(self, request: LiveEntryRequest) -> LiveEntryResult:
        if not self.enabled:
            raise LiveDispatchDisabled("live dispatch rejected: explicit cutover gate is closed")
        return submit_live_entry(self.client, self.store, request)
