from os import environ

from fatty_trader.web.app import create_app
from fatty_trader.web.runtime_probe import (
    create_entry_lane_reader,
    create_runtime_health_reader,
)

app = create_app(
    runtime_health_reader=create_runtime_health_reader(environ),
    entry_lane_reader=create_entry_lane_reader(),
)
