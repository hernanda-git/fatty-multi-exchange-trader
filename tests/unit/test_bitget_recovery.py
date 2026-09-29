from __future__ import annotations

import pytest

from fatty_trader.execution.bitget_monitor import MonitorReport
from fatty_trader.execution.bitget_protection_watchdog import PROTECTION_STREAM_SCOPE
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


def test_protection_stream_scope_releases_with_approval() -> None:
    """The protection-stream latch must have a deliberate release path.

    A latch with no documented way to clear it is safe in one direction only,
    and strands the venue on the next restart. The release is still gated on
    an explicit approval reference and a clean monitor report.
    """
    repository = Repository()

    report = release_after_clean_monitor(
        MonitorReport("ok"),
        repository,
        scope=PROTECTION_STREAM_SCOPE,
        approval_reference="telegram-approval-20260929-protection-stream",
    )

    assert report.released is True
    assert repository.releases == [
        (PROTECTION_STREAM_SCOPE, "telegram-approval-20260929-protection-stream")
    ]


def test_protection_stream_scope_still_refuses_empty_approval() -> None:
    repository = Repository()

    with pytest.raises(ValueError):
        release_after_clean_monitor(
            MonitorReport("ok"),
            repository,
            scope=PROTECTION_STREAM_SCOPE,
            approval_reference="   ",
        )

    assert repository.releases == []
