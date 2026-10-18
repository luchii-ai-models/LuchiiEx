"""Luchii Predict — fixed-payout UP/DOWN contracts (3s…5m). Luchii is the counterparty; stakes move through the double-entry ledger.
Gated by PREDICT_ENABLED=true (regulatory). Payout is dynamic on AI confidence; house edge is kept in PREDICT_HOUSE_USD."""
import asyncio
import os
import uuid
from decimal import Decimal

import ledger
from db import db, now_ms

DURATIONS = {3, 15, 30, 60, 300}
MIN_STAKE, MAX_STAKE = Decimal("1"), Decimal(os.environ.get("PREDICT_MAX_STAKE", "100"))
MAX_OPEN_LIABILITY = Decimal(os.environ.get("PREDICT_MAX_LIABILITY", "2000"))
ASSETS = ("BTC", "ETH", "SOL")
SYSTEM_ACCOUNTS = [("PREDICT_ESCROW_USD", "Predict Escrow", "LIABILITY", "USD"), ("PREDICT_HOUSE_USD", "Predict House P&L", "REVENUE", "USD")]


def enabled() -> bool:
    return os.environ.get("PREDICT_ENABLED", "false").lower() == "true"


def status() -> dict:
    return {"enabled": enabled(), "durations": sorted(DURATIONS), "assets": list(ASSETS), "minStake": float(MIN_STAKE), "maxStake": float(MAX_STAKE),
            "notice": "Fixed-payout contracts are regulated in many jurisdictions. Luchii is the counterparty. Enable only with compliance clearance."}


def payout_multiplier(confidence: float, duration: int) -> float:
    """Base 1.80x; conviction (|conf-0.5|) and longer durations widen payout. Never above 2.10x."""
    base = 1.80 + min(0.20, max(0.0, confidence - 0.5) * 0.5) + (0.05 if duration >= 60 else 0)
    return round(min(2.10, base), 2)


def price_of(asset: str) -> float:
    from engine import mkt
    return float(mkt(asset).get("price") or 0)


def confidence_for(asset: str) -> float:
    from engine import snapshot
    import strategy
    i, bi = snapshot(asset)
    return strategy.confidence(i, bi) if i and bi else 0.5


async def ensure_accounts():
    for code, name, typ, ccy in SYSTEM_ACCOUNTS:
        await db.ledger_accounts.update_one({"code": code}, {"$setOnInsert": {"id": str(uuid.uuid4()), "code": code, "name": name, "account_type": typ, "currency": ccy,
                                                                             "user_id": None, "created_at": now_ms()}}, upsert=True)


async def open_liability() -> Decimal:
    rows = await db.predict_contracts.find({"status": "OPEN"}, {"_id": 0, "potentialPayout": 1}).to_list(5000)
    return sum((Decimal(str(r["potentialPayout"])) for r in rows), Decimal(0))


async def place(uid: str, asset: str, direction: str, stake, duration: int, idem: str | None = None) -> dict:
    if not enabled():
        raise ledger.LedgerError("Predict is disabled on this server (PREDICT_ENABLED)", 503, "PREDICT_DISABLED")
    if asset not in ASSETS or direction not in ("UP", "DOWN") or duration not in DURATIONS:
        raise ledger.LedgerError("Invalid contract parameters", 422, "INVALID_CONTRACT")
    amt = ledger.D(stake)
    if not MIN_STAKE <= amt <= MAX_STAKE:
        raise ledger.LedgerError(f"Stake must be between ${MIN_STAKE} and ${MAX_STAKE}", 422, "STAKE_RANGE")
    px = price_of(asset)
    if px <= 0:
        raise ledger.LedgerError("Price oracle unavailable", 503, "ORACLE_DOWN")
    conf = confidence_for(asset)
    mult = payout_multiplier(conf, duration)
    payout = (amt * Decimal(str(mult))).quantize(Decimal("0.01"))
    if await open_liability() + payout > MAX_OPEN_LIABILITY:
        raise ledger.LedgerError("House liability limit reached — try a smaller stake or later", 409, "LIABILITY_CAP")
    cid = idem or str(uuid.uuid4())
    if existing := await db.predict_contracts.find_one({"id": cid}, {"_id": 0}):
        return existing
    je = await ledger.post_journal([{"account": ledger.user_code(uid, "USD"), "debit": amt}, {"account": "PREDICT_ESCROW_USD", "credit": amt}],
                                   source="PREDICT", description=f"Predict {asset} {direction} {duration}s stake ${amt}", reference_type="PREDICT_STAKE",
                                   reference_id=cid, idempotency_key=f"predict:stake:{cid}", actor_id=uid)
    doc = {"id": cid, "user_id": uid, "asset": asset, "direction": direction, "stake": float(amt), "duration": duration, "multiplier": mult,
           "potentialPayout": float(payout), "aiConfidence": round(conf, 3), "entryPrice": px, "openedAt": now_ms(), "expiresAt": now_ms() + duration * 1000,
           "status": "OPEN", "stakeJournalId": je["id"]}
    await db.predict_contracts.insert_one({**doc})
    return doc


async def settle(cid: str) -> dict:
    c = await db.predict_contracts.find_one({"id": cid}, {"_id": 0})
    if not c or c["status"] != "OPEN":
        return c
    px = price_of(c["asset"])
    if px <= 0:
        return c
    won = (px > c["entryPrice"]) if c["direction"] == "UP" else (px < c["entryPrice"])
    push = px == c["entryPrice"]
    stake, payout = ledger.D(c["stake"]), ledger.D(c["potentialPayout"])
    if push:
        lines, result = [{"account": "PREDICT_ESCROW_USD", "debit": stake}, {"account": ledger.user_code(c["user_id"], "USD"), "credit": stake}], "PUSH"
    elif won:
        house = payout - stake
        lines = [{"account": "PREDICT_ESCROW_USD", "debit": stake}, {"account": "PREDICT_HOUSE_USD", "debit": house},
                 {"account": ledger.user_code(c["user_id"], "USD"), "credit": payout}]
        result = "WON"
    else:
        lines, result = [{"account": "PREDICT_ESCROW_USD", "debit": stake}, {"account": "PREDICT_HOUSE_USD", "credit": stake}], "LOST"
    je = await ledger.post_journal(lines, source="PREDICT", description=f"Predict settle {c['asset']} {result} @ {px}", reference_type="PREDICT_SETTLE",
                                   reference_id=cid, idempotency_key=f"predict:settle:{cid}", actor_id="SYSTEM")
    upd = {"status": result, "exitPrice": px, "settledAt": now_ms(), "settleJournalId": je["id"], "payout": float(payout) if result == "WON" else (float(stake) if result == "PUSH" else 0.0)}
    await db.predict_contracts.update_one({"id": cid, "status": "OPEN"}, {"$set": upd})
    return {**c, **upd}


async def worker():
    """Settles expired contracts every second (price oracle = live market snapshot)."""
    await ensure_accounts()
    while True:
        try:
            async for c in db.predict_contracts.find({"status": "OPEN", "expiresAt": {"$lte": now_ms()}}, {"_id": 0, "id": 1}):
                await settle(c["id"])
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("predict").warning("settle loop: %s", e)
        await asyncio.sleep(1)
