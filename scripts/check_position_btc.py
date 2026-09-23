import asyncio, json, os, sys
sys.path.insert(0, "/app")
from fatty_trader.exchanges.bitget.client import BitgetRestClient

async def main():
    client = BitgetRestClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ.get("BITGET_API_PASSPHRASE",""),
        mode="LIVE"
    )
    pos = await client.get_all_positions()
    print("ALL_POS:", json.dumps(pos, default=str))
    acct = await client.get_account()
    print("ACCT:", json.dumps(acct, default=str))
    fills = await client.get_fills()
    if isinstance(fills, dict): fills = fills.get("fillList",[])
    import time
    now = int(time.time()*1000)
    recent = [f for f in fills if abs(now - int(f.get("cTime",0))) < 600000]
    print("RECENT_FILLS:", json.dumps(recent, default=str))
    await client.aclose()

asyncio.run(main())
