"""Production deployment admission only; never releases any trade safety gate."""

from collections.abc import Mapping

PRODUCTION_BITGET_SERVICES = (
    "dispatcher-bitget",
    "monitor-bitget",
    "operator-bot",
    "source-management",
)
PRODUCTION_VALUES = {
    "FATTY_PRODUCTION_LIVE_ONLY": "1",
    "TRADER_MODE": "LIVE",
    "BITGET_MODE": "LIVE",
    "BITGET_EXECUTION_ENABLED": "1",
}


def validate_production_live_policy(environ: Mapping[str, str]) -> None:
    for field, expected in PRODUCTION_VALUES.items():
        if environ.get(field) != expected:
            raise ValueError(
                f"production LIVE-only policy requires {field}={expected}; "
                "reject deployment, do not bypass kill switches or protection"
            )
