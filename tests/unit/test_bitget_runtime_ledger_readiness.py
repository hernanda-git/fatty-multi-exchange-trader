import pytest


def test_runtime_ledger_readiness_preserves_startup_ownership_veto():
    from fatty_trader.execution.bitget_runtime_readiness import ledger_readiness_issues

    class Repository:
        def inventory_issues(self, environment):
            assert environment == "LIVE"
            return ["orphan-entry-intent"]

        def unresolved_protection_issues(self):
            return ["recovery-missing-protection:owned-flat-close-fill-unproven"]

    assert ledger_readiness_issues(Repository(), "LIVE") == (
        "orphan-entry-intent",
        "recovery-missing-protection:owned-flat-close-fill-unproven",
    )


def test_runtime_ledger_readiness_requires_live_environment():
    from fatty_trader.execution.bitget_runtime_readiness import ledger_readiness_issues

    with pytest.raises(ValueError, match="LIVE"):
        ledger_readiness_issues(object(), "DEMO")


def test_clean_ledger_is_distinct_from_provider_execution_proof():
    from fatty_trader.execution.bitget_runtime_readiness import ledger_readiness_issues

    class Repository:
        def inventory_issues(self, environment):
            return []

        def unresolved_protection_issues(self):
            return []

    assert ledger_readiness_issues(Repository(), "LIVE") == ()
