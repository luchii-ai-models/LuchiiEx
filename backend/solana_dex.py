import asyncio
import base64
import json
import os
import time

import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from db import db, now_ms
from exchange_service import decrypt, encrypt

SOL = "So11111111111111111111111111111111111111112"
JUP = "https://lite-api.jup.ag"
TOKEN_PROGRAMS = ["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"]
LAMPORTS = 1_000_000_000


def rpc_url() -> str:
    return os.environ["SOLANA_RPC_URL"]


def parse_key(secret: str) -> Keypair:
    s = secret.strip()
    if s.startswith("["):
        return Keypair.from_bytes(bytes(json.loads(s)))
    return Keypair.from_base58_string(s)


async def rpc(method: str, params: list):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(rpc_url(), json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    j = r.json()
    if "error" in j:
        raise RuntimeError(j["error"].get("message", "RPC error"))
    return j["result"]


async def get_wallet(uid: str):
    return await db.solana_wallets.find_one({"user_id": uid}, {"_id": 0})


def keypair_of(w: dict) -> Keypair:
    return parse_key(decrypt(w["secret_enc"]))


async def import_wallet(uid: str, secret: str) -> str:
    kp = parse_key(secret)
    pub = str(kp.pubkey())
    await db.solana_wallets.update_one({"user_id": uid}, {"$set": {
        "user_id": uid, "pubkey": pub, "secret_enc": encrypt(secret.strip()), "created_at": now_ms()},
        "$setOnInsert": {"tokens": [], "amount_sol": 0.05, "enabled": False, "slippage_bps": 300}}, upsert=True)
    return pub


async def balances(pubkey: str) -> dict:
    sol, *acct_sets = await asyncio.gather(
        rpc("getBalance", [pubkey]),
        *[rpc("getTokenAccountsByOwner", [pubkey, {"programId": p}, {"encoding": "jsonParsed"}]) for p in TOKEN_PROGRAMS])
    toks = {}
    for a in [x for s in acct_sets for x in s["value"]]:
        info = a["account"]["data"]["parsed"]["info"]
        amt = info["tokenAmount"]
        toks[info["mint"]] = {"raw": int(amt["amount"]), "ui": float(amt["uiAmount"] or 0), "decimals": amt["decimals"]}
    return {"sol": sol["value"] / LAMPORTS, "tokens": toks}


async def search(q: str) -> list:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{JUP}/tokens/v2/search", params={"query": q})
    return [{"mint": t["id"], "symbol": t["symbol"], "name": t["name"], "decimals": t.get("decimals", 6),
             "price": t.get("usdPrice"), "liquidity": t.get("liquidity"), "mcap": t.get("mcap"), "icon": t.get("icon")}
            for t in r.json()[:8]]


async def token_info(mints: list) -> dict:
    if not mints:
        return {}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{JUP}/tokens/v2/search", params={"query": ",".join(mints)})
    return {t["id"]: {"symbol": t["symbol"], "name": t["name"], "decimals": t.get("decimals", 6), "price": t.get("usdPrice")}
            for t in r.json()}


async def quote(in_mint: str, out_mint: str, amount: int, slippage_bps: int) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{JUP}/swap/v1/quote", params={"inputMint": in_mint, "outputMint": out_mint, "amount": amount,
                                                        "slippageBps": slippage_bps})
    j = r.json()
    if "error" in j or r.status_code >= 400:
        raise RuntimeError(j.get("error") or j.get("message") or "no route")
    return j


async def swap(uid: str, in_mint: str, out_mint: str, amount: int) -> dict:
    w = await get_wallet(uid) or {}
    if not w:
        raise RuntimeError("No Solana wallet imported")
    kp = keypair_of(w)
    t0 = time.time()
    bal = await balances(str(kp.pubkey()))
    need = amount / LAMPORTS if in_mint == SOL else 0
    if bal["sol"] < need + 0.005:
        raise RuntimeError(f"need {round(need + 0.005, 4)} SOL incl. fees, wallet has {round(bal['sol'], 4)} SOL")
    q = await quote(in_mint, out_mint, amount, int(w.get("slippage_bps") or 300))
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{JUP}/swap/v1/swap", json={"quoteResponse": q, "userPublicKey": str(kp.pubkey()),
                                                      "dynamicComputeUnitLimit": True, "wrapAndUnwrapSol": True,
                                                      "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {
                                                          "maxLamports": 2_000_000, "priorityLevel": "high"}}})
    j = r.json()
    if "swapTransaction" not in j:
        raise RuntimeError(j.get("error") or "swap build failed")
    tx = VersionedTransaction.from_bytes(base64.b64decode(j["swapTransaction"]))
    signed = VersionedTransaction(tx.message, [kp])
    sig = await rpc("sendTransaction", [base64.b64encode(bytes(signed)).decode(), {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}])
    status = await confirm(sig)
    res = {"sig": sig, "status": status, "in_amount": int(q["inAmount"]), "out_amount": int(q["outAmount"]),
           "price_impact": q.get("priceImpactPct"), "ms": int((time.time() - t0) * 1000)}
    await db.solana_swaps.insert_one({"user_id": uid, "ts": now_ms(), "in_mint": in_mint, "out_mint": out_mint, **res})
    if status == "failed":
        raise RuntimeError(f"swap failed on-chain · {sig[:12]}…")
    return res


async def confirm(sig: str, tries: int = 20) -> str:
    for _ in range(tries):
        await asyncio.sleep(1.5)
        st = (await rpc("getSignatureStatuses", [[sig]]))["value"][0]
        if st:
            return "failed" if st.get("err") else (st.get("confirmationStatus") or "processed")
    return "pending"


async def mirror(uid: str, side: str) -> list:
    w = await get_wallet(uid)
    if not w or not w.get("enabled") or not w.get("tokens"):
        return []
    out = []
    bal = await balances(w["pubkey"])
    for mint in w["tokens"]:
        try:
            if side == "BUY":
                res = await swap(uid, SOL, mint, int(float(w.get("amount_sol") or 0) * LAMPORTS))
            else:
                raw = (bal["tokens"].get(mint) or {}).get("raw", 0)
                if raw <= 0:
                    raise RuntimeError("no token balance to sell")
                res = await swap(uid, mint, SOL, raw)
            out.append({"mint": mint, "ok": True, **res})
        except Exception as e:
            out.append({"mint": mint, "ok": False, "error": str(e)[:160]})
    return out
