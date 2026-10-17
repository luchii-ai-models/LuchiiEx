import csv
import io
import uuid

from db import db, now_ms

TAKER_FEE = 0.001
SLIPPAGE = 0.0005
VERSION = {"luchii003": "LUCHII_003", "aggressive": "AGGR_SMA_5_13"}
COLUMNS = ["trade_id", "strategy_id", "strategy_version", "symbol", "direction", "entry_time", "exit_time", "entry_price", "exit_price",
           "quantity", "position_usd", "gross_pnl", "fees", "slippage", "net_pnl", "pnl_pct", "signal_score", "confidence", "adx", "rsi",
           "volume_ratio", "atr", "trend_pct", "btc_price", "btc_rsi", "btc_above_ema200", "btc_trend", "regime", "entry_reason", "exit_reason",
           "mae_pct", "mfe_pct", "trade_duration_sec", "winner", "trade_result", "winner_agent", "agent_final"]


def _f(v, n=6):
    return None if v is None else round(float(v), n)


async def open_trade(bot: dict, asset: str, price: float, qty: float, d: dict, i: dict, bi: dict):
    strat = bot.get("strategy", "luchii003")
    doc = {"trade_id": uuid.uuid4().hex[:16], "user_id": bot["user_id"], "strategy_id": strat, "strategy_version": VERSION.get(strat, strat),
           "symbol": asset, "direction": "LONG", "entry_time": now_ms(), "exit_time": None, "entry_price": price, "exit_price": None,
           "quantity": qty, "position_usd": round(price * qty, 2), "signal_score": d.get("score"), "confidence": _f(d.get("confidence"), 2),
           "adx": _f(i.get("adx"), 2), "rsi": _f(i.get("rsi"), 2), "volume_ratio": _f(i.get("volume_ratio"), 3), "atr": _f(i.get("atr")),
           "trend_pct": _f(i.get("trend_pct"), 4), "btc_price": bi.get("price"), "btc_rsi": _f(bi.get("rsi"), 2),
           "btc_above_ema200": bool(bi.get("ema200") and bi.get("price") and bi["price"] > bi["ema200"]),
           "btc_trend": "UP" if (bi.get("trend_pct") or 0) > 0 else "DOWN", "regime": d.get("regime") or i.get("regime"),
           "entry_reason": d.get("reason", ""), "exit_reason": None, "low": price, "high": price, "partial_pnl": 0.0, "partial_qty": 0.0,
           "winner_agent": d.get("winner_agent"), "agent_scores": (d.get("agents") or {}).get("scores"), "agent_final": (d.get("agents") or {}).get("final"),
           "status": "open", "winner": None, "trade_result": None}
    validate(doc)
    await db.trade_audit.insert_one(doc)


def validate(doc: dict):
    """Integrity constraints (Mongo has no CHECK): direction, quantity, position_usd, time order, result."""
    assert doc["direction"] in ("LONG", "SHORT"), "direction must be LONG/SHORT"
    assert doc["quantity"] > 0, "quantity must be > 0"
    assert doc["position_usd"] > 0, "position_usd must be > 0"
    if doc.get("exit_time") is not None:
        assert doc["exit_time"] >= doc["entry_time"], "exit_time before entry_time"
        assert doc.get("trade_result") in ("WIN", "LOSS", "BREAKEVEN"), "bad trade_result"


async def ensure_indexes():
    await db.trade_audit.create_index("trade_id", unique=True)
    await db.trade_audit.create_index([("user_id", 1), ("symbol", 1), ("status", 1)])
    await db.trade_audit.create_index([("user_id", 1), ("strategy_id", 1), ("entry_time", -1)])
    await db.trade_audit.create_index([("user_id", 1), ("regime", 1)])
    await db.trade_audit.create_index([("user_id", 1), ("trade_result", 1)])
    await db.trade_audit.create_index([("user_id", 1), ("signal_score", 1)])
    await db.gates.create_index([("user_id", 1), ("strategy", 1)], unique=True)
    await db.symbol_gates.create_index([("user_id", 1), ("symbol", 1)], unique=True)
    await db.audit_log.create_index([("user_id", 1), ("ts", -1)])
    await db.bot_trades.create_index([("user_id", 1), ("ts", -1)])
    await db.bot_pnl.create_index([("user_id", 1), ("ts", -1)])


async def track(uid: str, asset: str, price: float):
    await db.trade_audit.update_one({"user_id": uid, "symbol": asset, "status": "open"}, {"$min": {"low": price}, "$max": {"high": price}})


async def close_trade(uid: str, asset: str, price: float, qty: float, d: dict, partial: bool):
    t = await db.trade_audit.find_one({"user_id": uid, "symbol": asset, "status": "open"})
    if not t:
        return
    pnl = (price - t["entry_price"]) * qty
    if partial:
        await db.trade_audit.update_one({"_id": t["_id"]}, {"$inc": {"partial_pnl": pnl, "partial_qty": qty}})
        return
    entry, total_qty = t["entry_price"], t["partial_qty"] + qty
    gross = t["partial_pnl"] + pnl
    notional_in, notional_out = entry * total_qty, gross + entry * total_qty
    fees = (notional_in + notional_out) * TAKER_FEE
    slip = notional_in * SLIPPAGE * 2
    net = gross - fees - slip
    ts = now_ms()
    upd = {"exit_time": ts, "exit_price": price, "gross_pnl": round(gross, 6), "fees": round(fees, 6), "slippage": round(slip, 6), "net_pnl": round(net, 6),
           "pnl_pct": round(net / notional_in * 100, 4) if notional_in else 0.0, "exit_reason": d.get("reason", ""),
           "mae_pct": round((min(t["low"], price) - entry) / entry * 100, 4), "mfe_pct": round((max(t["high"], price) - entry) / entry * 100, 4),
           "trade_duration_sec": int((ts - t["entry_time"]) / 1000), "winner": net > 0, "status": "closed",
           "trade_result": "WIN" if net > 0 else "LOSS" if net < 0 else "BREAKEVEN"}
    validate({**t, **upd})
    await db.trade_audit.update_one({"_id": t["_id"]}, {"$set": upd})


async def closed(uid: str, limit: int = 2000) -> list:
    return [t async for t in db.trade_audit.find({"user_id": uid, "status": "closed"}, {"_id": 0, "user_id": 0, "low": 0, "high": 0, "partial_pnl": 0, "partial_qty": 0, "status": 0})
            .sort("exit_time", -1).limit(limit)]


def to_csv(rows: list) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "winner": "TRUE" if r.get("winner") else "FALSE", "btc_above_ema200": "TRUE" if r.get("btc_above_ema200") else "FALSE"})
    return buf.getvalue()


def _bucket_stats(rows: list) -> dict:
    pnls = [r["net_pnl"] for r in rows]
    wins = [p for p in pnls if p > 0]
    loss = abs(sum(p for p in pnls if p <= 0))
    return {"trades": len(rows), "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
            "profit_factor": None if loss == 0 else round(sum(wins) / loss, 2), "expectancy": round(sum(pnls) / len(pnls), 4) if pnls else 0.0}


def buckets(rows: list) -> dict:
    score_b = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    adx_b = [(20, 25), (25, 30), (30, 40), (40, 1000)]
    out = {"score": {}, "adx": {}, "regime": {}, "symbol": {}}
    for lo, hi in score_b:
        out["score"][f"{lo}-{min(hi, 100)}"] = _bucket_stats([r for r in rows if r.get("signal_score") is not None and lo <= r["signal_score"] < hi])
    for lo, hi in adx_b:
        out["adx"][f"{lo}-{hi}" if hi < 1000 else f"{lo}+"] = _bucket_stats([r for r in rows if r.get("adx") is not None and lo <= r["adx"] < hi])
    for key in ("regime", "symbol"):
        for v in sorted({r.get(key) or "-" for r in rows}):
            out[key][v] = _bucket_stats([r for r in rows if (r.get(key) or "-") == v])
    return out
