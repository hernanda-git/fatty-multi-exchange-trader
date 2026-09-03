import inspect

import pytest
from pydantic import ValidationError

from fatty_trader.config.bitget import BitgetVenueConfig, BitgetVenueState
from fatty_trader.exchanges.bitget.paper import BitgetPaperAdapter


def test_missing_bitget_credentials_disable_the_venue() -> None:
    config = BitgetVenueConfig()

    assert config.state is BitgetVenueState.DISABLED
    assert BitgetPaperAdapter(config).state is BitgetVenueState.DISABLED


def test_blank_bitget_credential_disables_the_venue() -> None:
    config = BitgetVenueConfig(api_key="   ", api_secret="secret", passphrase="passphrase")

    assert config.state is BitgetVenueState.DISABLED
    assert BitgetPaperAdapter(config).is_enabled is False


def test_complete_credentials_enable_only_the_paper_adapter() -> None:
    config = BitgetVenueConfig(
        mode="PAPER",
        api_key="test-api-key",
        api_secret="test-api-secret",
        passphrase="test-passphrase",
    )

    adapter = BitgetPaperAdapter(config)

    assert config.state is BitgetVenueState.PAPER_READY
    assert adapter.state is BitgetVenueState.PAPER_READY
    assert adapter.is_enabled is True


def test_live_mode_is_rejected() -> None:
    with pytest.raises(ValidationError, match="PAPER"):
        BitgetVenueConfig(mode="LIVE")


def test_bitget_config_masks_secrets_in_repr_and_string() -> None:
    config = BitgetVenueConfig(
        api_key="test-api-key", api_secret="test-api-secret", passphrase="test-passphrase"
    )

    rendered = f"{config!r}\n{config}"

    assert "test-api-key" not in rendered
    assert "test-api-secret" not in rendered
    assert "test-passphrase" not in rendered


def test_paper_adapter_exposes_no_network_signing_or_order_operations() -> None:
    public_members = {
        name
        for name, member in inspect.getmembers(BitgetPaperAdapter, predicate=callable)
        if not name.startswith("_")
    }

    assert public_members == set()
