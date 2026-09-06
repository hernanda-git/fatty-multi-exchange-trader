"""Evidence-gated release of a Bitget DEMO kill switch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fatty_trader.execution.bitget_monitor import MonitorReport


class KillSwitchRecoveryRepository(Protocol):
    def release_kill_switch(self, scope: str, approval_reference: str) -> None: ...


@dataclass(frozen=True)
class RecoveryReport:
    released: bool
    reason: str


def release_after_clean_monitor(
    monitor_report: MonitorReport,
    repository: KillSwitchRecoveryRepository,
    *,
    scope: str,
    approval_reference: str,
) -> RecoveryReport:
    """Release only after a clean GET-only reconciliation report.

    The caller must enforce DEMO mode and obtain an explicit non-secret approval
    reference before invoking this boundary.
    """
    if not approval_reference.strip():
        raise ValueError("non-empty approval reference is required")
    if monitor_report.status != "ok" or monitor_report.reasons:
        return RecoveryReport(False, "monitor-not-clean")
    repository.release_kill_switch(scope, approval_reference.strip())
    return RecoveryReport(True, "released-after-clean-reconciliation")
