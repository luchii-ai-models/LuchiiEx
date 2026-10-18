"""Promotion registry (Mongo): every evaluation, promotion, rollback is an append-only record; idempotent rollback events."""
import uuid

from db import db, now_ms

STATUSES = ("CANDIDATE", "PROMOTE", "REJECT", "ROLLED_BACK", "RETIRED")


async def add(record: dict) -> dict:
    doc = {"id": str(uuid.uuid4()), "ts": now_ms(), **record}
    await db.promotions.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def latest(strategy: str, asset: str | None = None) -> dict | None:
    q = {"strategy": strategy, **({"asset": asset} if asset else {})}
    return await db.promotions.find_one(q, {"_id": 0}, sort=[("ts", -1)])


async def history(strategy: str, limit: int = 50) -> list:
    return await db.promotions.find({"strategy": strategy}, {"_id": 0, "report": 0}).sort("ts", -1).limit(limit).to_list(limit)


async def rollback(strategy: str, version: str, reasons: list[str], event_id: str, actor: str) -> dict:
    """Idempotent on event_id: redelivery never double-records."""
    if await db.promotions.find_one({"event_id": event_id}, {"_id": 1}):
        return await db.promotions.find_one({"event_id": event_id}, {"_id": 0, "report": 0})
    return await add({"strategy": strategy, "version": version, "status": "ROLLED_BACK", "reasons": reasons, "event_id": event_id, "actor": actor})


async def is_promoted(strategy: str, asset: str) -> tuple[bool, str]:
    """Live gate: some timeframe's latest decision for (strategy, asset) is PROMOTE and no later rollback."""
    best = None
    for tf in ("1m", "5m", "15m"):
        last = await db.promotions.find_one({"strategy": strategy, "asset": asset, "timeframe": tf, "status": {"$in": ["PROMOTE", "REJECT"]}}, {"_id": 0, "status": 1, "ts": 1, "version": 1, "score": 1}, sort=[("ts", -1)])
        if not last:
            continue
        rb = await db.promotions.find_one({"strategy": strategy, "status": "ROLLED_BACK", "ts": {"$gt": last["ts"]}}, {"_id": 0, "reasons": 1})
        if rb:
            continue
        if last["status"] == "PROMOTE" and (best is None or last.get("score", 0) > best[1]):
            best = (tf, last.get("score", 0))
    if best:
        return True, f"PROMOTE @ {best[0]}"
    legacy = await db.promotions.find_one({"strategy": strategy, "asset": asset, "status": {"$in": ["PROMOTE", "REJECT"]}}, {"_id": 0, "status": 1}, sort=[("ts", -1)])
    return False, "no promotion run yet" if not legacy else "REJECT on every timeframe"


async def active_timeframe(strategy: str) -> str:
    """Timeframe to run live: the PROMOTEd one with the highest score across assets, else 1m."""
    rows = await db.promotions.find({"strategy": strategy, "status": "PROMOTE", "timeframe": {"$exists": True}}, {"_id": 0, "timeframe": 1, "score": 1, "ts": 1}).sort("ts", -1).limit(30).to_list(30)
    if not rows:
        return "1m"
    rb = await db.promotions.find_one({"strategy": strategy, "status": "ROLLED_BACK"}, {"_id": 0, "ts": 1}, sort=[("ts", -1)])
    rows = [r for r in rows if not rb or r["ts"] > rb["ts"]]
    return max(rows, key=lambda r: r.get("score", 0))["timeframe"] if rows else "1m"
