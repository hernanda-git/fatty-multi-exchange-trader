"""Read-only durable admission checks; an empty result is not a live trade proof."""

from typing import Protocol


class LedgerReadinessRepository(Protocol):
    def inventory_issues(self, environment: str) -> list[str]: ...
    def unresolved_protection_issues(self) -> list[str]: ...


def ledger_readiness_issues(
    repository: LedgerReadinessRepository, environment: str
) -> tuple[str, ...]:
    if environment != "LIVE":
        raise ValueError("production ledger readiness requires LIVE")
    return tuple(
        sorted(
            set(repository.inventory_issues(environment))
            | set(repository.unresolved_protection_issues())
        )
    )
