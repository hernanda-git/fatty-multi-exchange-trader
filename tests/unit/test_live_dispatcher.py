from decimal import Decimal

import pytest

from fatty_trader.config.bitget import BitgetLiveConfig
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveEntryRequest
from fatty_trader.execution.live_dispatcher import LiveDispatchDisabled, LiveDispatchExecutor


class Client:
    def __init__(self) -> None:
        self.post_count = 0


def test_disabled_dispatcher_rejects_before_provider_mutation() -> None:
    client = Client()
    executor = LiveDispatchExecutor(
        client=client,  # type: ignore[arg-type]
        store=InMemoryLiveIntentStore(),
        config=BitgetLiveConfig(api_key="key", api_secret="secret", passphrase="passphrase"),
        authenticated_read_passed=True,
        implementation_enabled=True,
        safety_checks_passed=True,
        approval_token=None,
    )
    with pytest.raises(LiveDispatchDisabled, match="cutover gate"):
        executor.submit(
            LiveEntryRequest(
                symbol="BTCUSDT",
                side="BUY",
                quantity=Decimal("0.001"),
                leverage=20,
                stop_loss=Decimal("50000"),
            )
        )
    assert client.post_count == 0
