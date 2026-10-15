import asyncio
import time

from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync

_CLIENTS: dict = {}


async def get_client(uid: str, ssid: str) -> PocketOptionAsync:
    c = _CLIENTS.get(uid)
    if c and c[0] == ssid:
        return c[1]
    client = PocketOptionAsync(ssid)
    await asyncio.sleep(5)
    _CLIENTS[uid] = (ssid, client)
    return client


def drop(uid: str):
    _CLIENTS.pop(uid, None)


async def connect(uid: str, ssid: str) -> float:
    drop(uid)
    client = await get_client(uid, ssid)
    return await asyncio.wait_for(client.balance(), 20)


async def mirror(uid: str, cfg: dict, side: str) -> dict:
    t0 = time.time()
    client = await get_client(uid, cfg["ssid"])
    fn = client.buy if side == "BUY" else client.sell
    trade_id, _ = await asyncio.wait_for(fn(cfg["asset"], float(cfg["amount"]), int(cfg["duration"])), 30)
    return {"ok": True, "last_ms": int((time.time() - t0) * 1000), "ts": int(time.time() * 1000), "trade_id": str(trade_id)}
