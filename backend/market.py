import asyncio
import logging

import httpx

SNAPSHOT: dict = {"price": None, "eth": None, "candles": [], "rsi": None, "bids": [], "asks": [],
                  "change_pct": None, "source": "coinbase"}
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


async def refresh(c: httpx.AsyncClient):
    t, e, k, b = await asyncio.gather(
        c.get(f"{CB}/BTC-USD/ticker"), c.get(f"{CB}/ETH-USD/ticker"),
        c.get(f"{CB}/BTC-USD/candles", params={"granularity": 60}), c.get(f"{CB}/BTC-USD/book", params={"level": 2}))
    rows = sorted(k.json(), key=lambda r: r[0])[-60:]
    candles = [{"t": r[0] * 1000, "o": r[3], "h": r[2], "l": r[1], "c": r[4], "v": r[5]} for r in rows]
    book = b.json()
    SNAPSHOT.update({
        "price": float(t.json()["price"]), "eth": float(e.json()["price"]), "candles": candles,
        "rsi": rsi([x["c"] for x in candles]),
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
