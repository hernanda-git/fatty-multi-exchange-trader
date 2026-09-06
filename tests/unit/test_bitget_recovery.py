from __future__ import annotations

from fatty_trader.execution.bitget_monitor import MonitorReport
from fatty_trader.execution.bitget_recovery import release_after_clean_monitor


class Repository:
    def __init__(self) -> None:
        self.releases: list[tuple[str, str]] = []

    def release_kill_switch(self, scope: str, approval_reference: str) -> None:
        self.releases.append((scope, approval_reference))


def test_clean_monitor_releases_with_approval() -> None:
    repository = Repository()

    report = release_after_clean_monitor(
        MonitorReport("ok"),
        repository,
        scope="bitget",
        approval_reference="telegram-approval-20260906",
    )

    assert report.released is True
    assert repository.releases == [("bitget", "telegram-approval-20260906")]


def test_unclean_monitor_does_not_release() -> None:
    repository = Repository()

    report = release_after_clean_monitor(
        MonitorReport("kill-switch-latched", ("provider-orders-invalid",)),
        repository,
        scope="bitget",
        approval_reference="telegram-approval-20260906",
    )

    assert report.released is False
    assert repository.releases == []
