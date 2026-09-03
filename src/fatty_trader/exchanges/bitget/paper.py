"""PAPER-only Bitget venue selection adapter.

This skeleton intentionally contains no network, signing, account, or order functionality.
"""

from __future__ import annotations

from fatty_trader.config.bitget import BitgetVenueConfig, BitgetVenueState


class BitgetPaperAdapter:
    """Expose only Bitget PAPER availability derived from fail-closed configuration."""

    def __init__(self, config: BitgetVenueConfig) -> None:
        self._config = config

    @property
    def state(self) -> BitgetVenueState:
        """Return the configuration-derived venue selection state."""
        return self._config.state

    @property
    def is_enabled(self) -> bool:
        """Whether this PAPER-only venue is eligible for selection."""
        return self.state is BitgetVenueState.PAPER_READY
