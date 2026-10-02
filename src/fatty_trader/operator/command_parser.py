import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_SYMBOL = re.compile(r"[A-Z0-9]{2,20}USDT\Z")
_TRADE_ARGUMENTS = {"margin", "leverage", "entry", "sl", "tp"}


def _parse_arguments(parts: list[str], *, allowed: set[str], required: set[str]) -> dict[str, str]:
    arguments: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            raise CommandError(f"invalid argument: {part}")
        key, value = part.split("=", 1)
        if key not in allowed:
            raise CommandError(f"unknown argument: {key}")
        if key in arguments:
            raise CommandError(f"duplicate argument: {key}")
        if not value:
            raise CommandError(f"argument value is required: {key}")
        arguments[key] = value
    missing = required - arguments.keys()
    if missing:
        raise CommandError(f"missing argument: {sorted(missing)[0]}")
    return arguments


def _parse_positive_decimal(value: str, label: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise CommandError(f"{label} must be a finite positive number") from exc
    if not result.is_finite() or result <= 0:
        raise CommandError(f"{label} must be a finite positive number")
    return result


def _validate_symbol(symbol: str) -> str:
    canonical = symbol.upper()
    if not _SYMBOL.fullmatch(canonical):
        raise CommandError("symbol must be a valid USDT futures pair, e.g. BTCUSDT")
    return canonical


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
    confirm_token: str | None = None


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
    confirm_token: str | None = None


@dataclass(frozen=True)
class CancelCommand:
    target: str  # "all", "SYM", or "order_id=ID"
    confirm_token: str | None = None


@dataclass(frozen=True)
class CloseCommand:
    target: str  # "all", "SYM", or "position_id=ID"
    confirm_token: str | None = None


@dataclass(frozen=True)
class SetProtectionCommand:
    symbol: str
    price: Decimal
    kind: str  # "SL" or "TP"
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


@dataclass(frozen=True)
class HealthCommand:
    pass


@dataclass(frozen=True)
class HelpCommand:
    pass


@dataclass(frozen=True)
class DiagnosticCommand:
    kind: str


def parse_trade(text: str) -> TradeCommand:
    parts = text.split()
    if len(parts) < 8 or parts[0].lower().split("@", 1)[0] != "/trade":
        raise CommandError(
            "expected /trade <exchange|all> <LONG|SHORT> <pair> with named arguments"
        )
    venue, direction, pair = parts[1].lower(), parts[2].upper(), parts[3]
    if venue not in {"binance", "bitget", "all"} or direction not in {"LONG", "SHORT"}:
        raise CommandError("exchange and direction must be explicit")
    arguments = _parse_arguments(
        parts[4:], allowed=_TRADE_ARGUMENTS | {"confirm"}, required=_TRADE_ARGUMENTS
    )
    if arguments["entry"] != "market":
        raise CommandError("margin, leverage, market entry, sl, and tp are required")
    try:
        leverage = None if arguments["leverage"] == "auto" else int(arguments["leverage"])
        take_profits = tuple(
            _parse_positive_decimal(item, "take profit") for item in arguments["tp"].split(",")
        )
        margin = _parse_positive_decimal(arguments["margin"], "margin")
        stop_loss = _parse_positive_decimal(arguments["sl"], "stop loss")
        if leverage is not None and leverage <= 0:
            raise CommandError("leverage must be positive")
        command = TradeCommand(
            exchanges=("binance", "bitget") if venue == "all" else (venue,),
            direction=direction,
            pair=_validate_symbol(pair),
            margin=margin,
            leverage=leverage,
            entry=arguments["entry"],
            stop_loss=stop_loss,
            take_profits=take_profits,
            confirm_token=arguments.get("confirm"),
        )
    except (InvalidOperation, ValueError) as exc:
        if isinstance(exc, CommandError):
            raise
        raise CommandError("trade values must be valid positive numbers") from exc
    return command


def _parse_decimal_or_auto(value: str) -> Decimal | str:
    if value == "auto":
        return "auto"
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise CommandError(f"invalid number: {value}") from exc
    if not result.is_finite() or result <= 0:
        raise CommandError(f"value must be positive: {value}")
    return result


def parse_operator_command(
    text: str,
) -> (
    PriceCommand
    | OpenCommand
    | CancelCommand
    | CloseCommand
    | SetProtectionCommand
    | PositionsCommand
    | OrdersCommand
    | BalanceCommand
    | HealthCommand
    | HelpCommand
    | DiagnosticCommand
    | TradeCommand
):
    if not text or not text.strip():
        raise CommandError("empty command")
    parts = text.strip().split()
    head = parts[0].lower().split("@", 1)[0]

    if head == "/trade":
        return parse_trade(text)

    if head == "/price":
        if len(parts) != 2:
            raise CommandError("/price requires a symbol")
        return PriceCommand(symbol=_validate_symbol(parts[1]))

    if head == "/open":
        if len(parts) < 5:
            raise CommandError("/open requires SYM DIRECTION and margin/leverage/entry/sl/tp")
        symbol = parts[1].upper()
        direction = parts[2].upper()
        if direction not in {"LONG", "SHORT"}:
            raise CommandError("direction must be LONG or SHORT")
        kwargs = _parse_arguments(
            parts[3:],
            allowed=_TRADE_ARGUMENTS | {"confirm"},
            required=_TRADE_ARGUMENTS,
        )
        entry = kwargs["entry"]
        if entry != "market" and not entry.startswith("limit:"):
            raise CommandError("entry must be 'market' or 'limit:PRICE'")
        if entry.startswith("limit:"):
            _parse_positive_decimal(entry.removeprefix("limit:"), "limit entry")
        symbol = _validate_symbol(symbol)
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
            entry=entry,
            stop_loss=stop_loss,
            take_profits=take_profits,
            confirm_token=kwargs.get("confirm"),
        )

    if head == "/cancel":
        if len(parts) < 2:
            raise CommandError("/cancel requires a target: all, SYM, or order_id=ID")
        target = _parse_target(parts[1], key="order_id")
        confirm_token = _parse_confirmation(parts[2:], command="/cancel")
        return CancelCommand(target=target, confirm_token=confirm_token)

    if head == "/close":
        if len(parts) < 2:
            raise CommandError("/close requires a target: all, SYM, or position_id=ID")
        target = _parse_target(parts[1], key="position_id")
        confirm_token = _parse_confirmation(parts[2:], command="/close")
        return CloseCommand(target=target, confirm_token=confirm_token)

    if head in {"/setsl", "/settp"}:
        if len(parts) not in {3, 4}:
            raise CommandError(f"{head} requires SYMBOL PRICE [confirm=TOKEN]")
        symbol = _validate_symbol(parts[1])
        price = _parse_positive_decimal(parts[2], "protection price")
        confirm_token = None
        if len(parts) == 4:
            if not parts[3].startswith("confirm="):
                raise CommandError(f"invalid {head} argument: {parts[3]}")
            confirm_token = parts[3].split("=", 1)[1]
            if not confirm_token:
                raise CommandError("confirmation token is required")
        return SetProtectionCommand(
            symbol=symbol,
            price=price,
            kind="SL" if head == "/setsl" else "TP",
            confirm_token=confirm_token,
        )

    if head == "/positions":
        if len(parts) != 1:
            raise CommandError("/positions takes no arguments")
        return PositionsCommand()

    if head == "/orders":
        if len(parts) != 1:
            raise CommandError("/orders takes no arguments")
        return OrdersCommand()

    if head == "/health":
        if len(parts) != 1:
            raise CommandError("/health takes no arguments")
        return HealthCommand()

    if head in {"/help", "/start"}:
        if len(parts) != 1:
            raise CommandError(f"{head} takes no arguments")
        return HelpCommand()

    diagnostic_commands = {
        "/status": "status",
        "/reconcile": "reconcile",
        "/protection": "protection",
        "/fills": "fills",
        "/intents": "intents",
        "/dispatches": "dispatches",
        "/signals": "signals",
    }
    if head in diagnostic_commands:
        if len(parts) != 1:
            raise CommandError(f"{head} takes no arguments")
        return DiagnosticCommand(diagnostic_commands[head])

    if head == "/balance":
        if len(parts) != 1:
            raise CommandError("/balance takes no arguments")
        return BalanceCommand()

    raise CommandError(f"unknown command: {head}")


def _parse_target(value: str, *, key: str) -> str:
    if value.lower() == "all":
        return "all"
    prefix = f"{key}="
    if value.startswith(prefix):
        identifier = value[len(prefix) :]
        if identifier and re.fullmatch(r"[A-Za-z0-9_.:-]+", identifier):
            return value
        raise CommandError(f"invalid {key} target")
    return _validate_symbol(value)


def _parse_confirmation(parts: list[str], *, command: str) -> str | None:
    if len(parts) > 1:
        raise CommandError(f"{command} accepts one confirmation token")
    if not parts:
        return None
    key, separator, token = parts[0].partition("=")
    if key != "confirm" or not separator or not token:
        raise CommandError(f"invalid {command} confirmation")
    return token
