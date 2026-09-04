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


@dataclass(frozen=True)
class PriceCommand:
    symbol: str


@dataclass(frozen=True)
class OpenCommand:
    symbol: str
    direction: str
    margin: Decimal | str  # "auto" or Decimal
    leverage: int
    entry: str  # "market" or "limit:PRICE"
    stop_loss: Decimal | str  # "auto" or Decimal
    take_profits: tuple[Decimal | str, ...]  # each "auto" or Decimal


@dataclass(frozen=True)
class CancelCommand:
    target: str  # "all", "SYM", or "order_id=ID"
    confirm_token: str | None = None


@dataclass(frozen=True)
class CloseCommand:
    target: str  # "all", "SYM", or "position_id=ID"
    confirm_token: str | None = None


@dataclass(frozen=True)
class PositionsCommand:
    pass


@dataclass(frozen=True)
class OrdersCommand:
    pass


@dataclass(frozen=True)
class BalanceCommand:
    pass


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


def _parse_decimal_or_auto(value: str) -> Decimal | str:
    if value == "auto":
        return "auto"
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise CommandError(f"invalid number: {value}") from exc
    if result <= 0:
        raise CommandError(f"value must be positive: {value}")
    return result


def parse_operator_command(
    text: str,
) -> (
    PriceCommand
    | OpenCommand
    | CancelCommand
    | CloseCommand
    | PositionsCommand
    | OrdersCommand
    | BalanceCommand
):
    if not text or not text.strip():
        raise CommandError("empty command")
    parts = text.strip().split()
    head = parts[0].lower()

    if head == "/price":
        if len(parts) != 2:
            raise CommandError("/price requires a symbol")
        return PriceCommand(symbol=parts[1].upper())

    if head == "/open":
        if len(parts) < 5:
            raise CommandError("/open requires SYM DIRECTION and margin/leverage/entry/sl/tp")
        symbol = parts[1].upper()
        direction = parts[2].upper()
        if direction not in {"LONG", "SHORT"}:
            raise CommandError("direction must be LONG or SHORT")
        kwargs = {}
        for part in parts[3:]:
            if "=" not in part:
                raise CommandError(f"invalid argument: {part}")
            key, value = part.split("=", 1)
            kwargs[key] = value
        for needed in ("margin", "leverage", "entry", "sl", "tp"):
            if needed not in kwargs:
                raise CommandError(f"missing argument: {needed}")
        if kwargs["entry"] != "market" and not kwargs["entry"].startswith("limit:"):
            raise CommandError("entry must be 'market' or 'limit:PRICE'")
        margin = _parse_decimal_or_auto(kwargs["margin"])
        try:
            leverage = int(kwargs["leverage"])
        except ValueError as exc:
            raise CommandError("leverage must be an integer") from exc
        if leverage <= 0:
            raise CommandError("leverage must be positive")
        stop_loss = _parse_decimal_or_auto(kwargs["sl"])
        tp_raw = kwargs["tp"]
        take_profits = tuple(_parse_decimal_or_auto(v) for v in tp_raw.split(","))
        return OpenCommand(
            symbol=symbol,
            direction=direction,
            margin=margin,
            leverage=leverage,
            entry=kwargs["entry"],
            stop_loss=stop_loss,
            take_profits=take_profits,
        )

    if head == "/cancel":
        if len(parts) < 2:
            raise CommandError("/cancel requires a target: all, SYM, or order_id=ID")
        target = parts[1]
        confirm_token = None
        if len(parts) > 2:
            for part in parts[2:]:
                if part.startswith("confirm="):
                    confirm_token = part.split("=", 1)[1]
        _valid_symbol = (
            target == target.upper() and not target.startswith("order_id=")
        ) or target.startswith("order_id=")
        if target == "all" or _valid_symbol:
            return CancelCommand(target=target, confirm_token=confirm_token)
        raise CommandError("invalid /cancel target")

    if head == "/close":
        if len(parts) < 2:
            raise CommandError("/close requires a target: all, SYM, or position_id=ID")
        target = parts[1]
        confirm_token = None
        if len(parts) > 2:
            for part in parts[2:]:
                if part.startswith("confirm="):
                    confirm_token = part.split("=", 1)[1]
        _valid_position = (
            target == target.upper() and not target.startswith("position_id=")
        ) or target.startswith("position_id=")
        if target == "all" or _valid_position:
            return CloseCommand(target=target, confirm_token=confirm_token)
        raise CommandError("invalid /close target")

    if head == "/positions":
        if len(parts) != 1:
            raise CommandError("/positions takes no arguments")
        return PositionsCommand()

    if head == "/orders":
        if len(parts) != 1:
            raise CommandError("/orders takes no arguments")
        return OrdersCommand()

    if head == "/balance":
        if len(parts) != 1:
            raise CommandError("/balance takes no arguments")
        return BalanceCommand()

    raise CommandError(f"unknown command: {head}")
