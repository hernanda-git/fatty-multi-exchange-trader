import asyncio, json, os, sys, uuid
from decimal import Decimal
from fatty_trader.exchanges.bitget.client import BitgetRestClient, BitgetApiError

async def main():
    key = os.environ["BITGET_API_KEY"]
    secret = os.environ["BITGET_API_SECRET"]
    passphrase = os.environ.get("BITGET_API_PASSPHRASE","")

    symbol = "BTCUSDT"
    margin_usdt = 1.0
    leverage = 50
    sl_trigger = "81280"
    tp_trigger = "77043"

    client = BitgetRestClient(api_key=key, api_secret=secret, passphrase=passphrase, mode="LIVE")

    # 1. Set leverage
    await client.set_leverage(symbol=symbol, leverage=str(leverage), hold_side="short")
    print("SET_LEVERAGE_50: ok")

    # 2. Set margin mode to isolated
    await client.set_margin_mode(symbol=symbol, margin_mode="isolated")
    print("SET_MARGIN_MODE_ISOLATED: ok")

    # 3. Get mark price
    ticker = await client.get_ticker(symbol)
    mark = float(ticker.get("lastPr") or 0)
    print(f"MARK_PRICE: {mark}")

    # 4. Calculate quantity (contracts)
    size_mult = 0.0001
    notional = margin_usdt * leverage
    raw_qty = notional / mark
    qty = round(raw_qty / size_mult) * size_mult
    qty_str = f"{qty:.4f}"
    print(f"NOTIONAL={notional} QTY={qty_str}")

    # 5. Place short market entry
    entry_oid = str(uuid.uuid4())
    entry = await client.place_entry_order(
        symbol=symbol,
        side="sell",
        quantity=qty_str,
        client_oid=entry_oid,
        order_type="market",
        margin_mode="isolated",
    )
    print("ENTRY_ORDER:", json.dumps(entry, default=str))
    entry_order_id = entry.get("orderId") or entry.get("data",{}).get("orderId","")

    # 6. Place SL + TP with trigger as execute price
    sl_oid = str(uuid.uuid4())
    tp_oid = str(uuid.uuid4())
    payload = {
        "marginCoin": "USDT",
        "productType": "USDT-FUTURES",
        "symbol": symbol,
        "holdSide": "sell",
        "stopLossTriggerPrice": sl_trigger,
        "stopLossTriggerType": "mark_price",
        "stopLossExecutePrice": sl_trigger,
        "stopLossClientOid": sl_oid,
        "stopSurplusTriggerPrice": tp_trigger,
        "stopSurplusTriggerType": "mark_price",
        "stopSurplusExecutePrice": tp_trigger,
        "stopSurplusClientOid": tp_oid,
    }
    try:
        tpsl = await client._post("/api/v2/mix/order/place-pos-tpsl", payload)
        print("TPSL:", json.dumps(tpsl, default=str))
    except BitgetApiError as exc:
        print("TPSL_ERROR:", str(exc))

    # 7. Verify position
    pos = await client.get_single_position(symbol)
    print("POSITION:", json.dumps(pos, default=str))

    await client.aclose()

asyncio.run(main())
