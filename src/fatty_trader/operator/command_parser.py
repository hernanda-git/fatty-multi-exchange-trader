from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class CommandError(ValueError):
    """Raised for unambiguous command grammar violations before dispatch creation."""


@dataclass(frozen=True)
class TradeCommand:
    exchanges: tuple[str, ...]
    direction: str
    pair: str
    margin: Decimal
    leverage: int | None
    entry: str
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]


def parse_trade(text: str) -> TradeCommand:
    parts = text.split()
    if len(parts) < 8 or parts[:2] != ["/trade", parts[1]]:
        raise CommandError(
            "expected /trade <exchange|all> <LONG|SHORT> <pair> with named arguments"
        )
    venue, direction, pair = parts[1:4]
    if venue not in {"binance", "bitget", "all"} or direction not in {"LONG", "SHORT"}:
        raise CommandError("exchange and direction must be explicit")
    arguments = dict(part.split("=", 1) for part in parts[4:] if "=" in part)
    required = {"margin", "leverage", "entry", "sl", "tp"}
    if not required <= arguments.keys() or arguments["entry"] != "market":
        raise CommandError("margin, leverage, market entry, sl, and tp are required")
    try:
        leverage = None if arguments["leverage"] == "auto" else int(arguments["leverage"])
        take_profits = tuple(Decimal(item) for item in arguments["tp"].split(","))
        command = TradeCommand(
            exchanges=("binance", "bitget") if venue == "all" else (venue,),
            direction=direction,
            pair=pair.upper(),
            margin=Decimal(arguments["margin"]),
            leverage=leverage,
            entry=arguments["entry"],
            stop_loss=Decimal(arguments["sl"]),
            take_profits=take_profits,
        )
    except (InvalidOperation, ValueError) as exc:
        raise CommandError("trade values must be valid positive numbers") from exc
    if command.margin <= 0 or command.stop_loss <= 0 or not command.take_profits:
        raise CommandError("margin, stop loss, and take profit must be positive")
    return command
