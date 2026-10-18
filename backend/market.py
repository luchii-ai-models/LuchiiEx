import asyncio
import logging

import httpx

ASSETS = ["BTC", "ETH", "SOL"]
SNAPSHOT: dict = {"price": None, "eth": None, "sol": None, "candles": [], "rsi": None, "bids": [], "asks": [],
                  "change_pct": None, "source": "coinbase", "markets": {}}
CB = "https://api.exchange.coinbase.com/products"
log = logging.getLogger("market")


def rsi(closes, n=14):
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0
    rs = gains / losses
    return round(100 - 100 / (1 + rs), 1)


def _candles(resp) -> list:
    rows = sorted(resp.json(), key=lambda r: r[0])[-300:]
    return [{"t": r[0] * 1000, "o": r[3], "h": r[2], "l": r[1], "c": r[4], "v": r[5]} for r in rows]


async def refresh(c: httpx.AsyncClient):
    import strategy
    step = strategy.TIMEFRAMES.get(strategy.ACTIVE_TF["tf"], 1)
    reqs = [c.get(f"{CB}/{a}-USD/ticker") for a in ASSETS] + \
           [c.get(f"{CB}/{a}-USD/candles", params={"granularity": 60}) for a in ASSETS] + \
           [c.get(f"{CB}/BTC-USD/book", params={"level": 2})] + \
           ([c.get(f"{CB}/{a}-USD/candles", params={"granularity": step * 60}) for a in ASSETS] if step > 1 else [])
    res = await asyncio.gather(*reqs)
    n = len(ASSETS)
    markets = {}
    for i, a in enumerate(ASSETS):
        candles = _candles(res[n + i])
        markets[a] = {"price": float(res[i].json()["price"]), "candles": candles, "rsi": rsi([x["c"] for x in candles])}
        if step > 1:
            tf = _candles(res[2 * n + 1 + i])
            markets[a]["tf_candles"], markets[a]["tf_rsi"], markets[a]["tf"] = tf, rsi([x["c"] for x in tf]), strategy.ACTIVE_TF["tf"]
    book = res[2 * n].json()
    SNAPSHOT.update({
        "price": markets["BTC"]["price"], "eth": markets["ETH"]["price"], "sol": markets["SOL"]["price"],
        "candles": markets["BTC"]["candles"], "rsi": markets["BTC"]["rsi"], "markets": markets,
        "bids": [{"price": float(p), "size": float(s)} for p, s, *_ in book["bids"][:12]],
        "asks": [{"price": float(p), "size": float(s)} for p, s, *_ in book["asks"][:12]],
    })


async def loop():
    async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "luchii"}) as c:
        while True:
            try:
                await refresh(c)
            except Exception as ex:
                log.warning("market refresh failed: %s", ex)
            await asyncio.sleep(4)
