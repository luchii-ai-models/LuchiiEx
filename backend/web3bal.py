import httpx

ETH_RPC = "https://ethereum-rpc.publicnode.com"


async def balance(chain: str, address: str):
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            if chain == "btc":
                d = (await c.get(f"https://mempool.space/api/address/{address}")).json()
                sats = sum(d[k]["funded_txo_sum"] - d[k]["spent_txo_sum"] for k in ("chain_stats", "mempool_stats"))
                return round(sats / 1e8, 8)
            r = await c.post(ETH_RPC, json={"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [address, "latest"]})
            return round(int(r.json()["result"], 16) / 1e18, 6)
    except Exception:
        return None
