import time

from db import db, now_ms

WINDOW_MS = 30 * 86400 * 1000
MIN_TRADES = 50


def profit_factor(pnls) -> float:
    if not pnls:
        return 0.0
    g = sum(p for p in pnls if p > 0)
    l = abs(sum(p for p in pnls if p < 0))
    return float("inf") if l == 0 and g > 0 else (0.0 if l == 0 else g / l)


def max_drawdown_pct(equity: list) -> float:
    peak, dd = 0.0, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            dd = max(dd, (peak - e) / peak * 100)
    return round(dd, 2)


def trade_stats(trades: list) -> dict:
    pnls = [float(t.get("pnl") or 0) for t in trades if t.get("side") == "SELL"]
    wins = [p for p in pnls if p > 0]
    pf = profit_factor(pnls)
    return {"trades": len(pnls), "wins": len(wins), "losses": len(pnls) - len(wins),
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
            "profit_factor": None if pf == float("inf") else round(pf, 2), "pf_inf": pf == float("inf"),
            "avg_gain": round(sum(wins) / len(wins), 4) if wins else 0.0,
            "avg_loss": round(sum(p for p in pnls if p <= 0) / max(len(pnls) - len(wins), 1), 4),
            "net_pnl": round(sum(pnls), 4)}


async def rolling(uid: str) -> dict:
    since = now_ms() - WINDOW_MS
    trades = [t async for t in db.bot_trades.find({"user_id": uid, "ts": {"$gte": since}}, {"_id": 0})]
    eq = [p["equity"] async for p in db.bot_pnl.find({"user_id": uid, "ts": {"$gte": since}}, {"_id": 0, "equity": 1}).sort("ts", 1)]
    by_asset = {}
    for a in sorted({t.get("asset", "BTC") for t in trades} | {"BTC", "ETH", "SOL"}):
        by_asset[a] = trade_stats([t for t in trades if t.get("asset", "BTC") == a])
    by_strategy = {s: trade_stats([t for t in trades if t.get("strategy", "luchii003") == s]) for s in ("luchii003", "aggressive")}
    return {"window_days": 30, "overall": trade_stats(trades), "max_drawdown": max_drawdown_pct(eq), "by_asset": by_asset, "by_strategy": by_strategy}


def classify(pf: float, dd: float, trades: int) -> str:
    if trades < MIN_TRADES:
        return "OPEN"
    if pf < 0.75 or dd > 15:
        return "DISABLED"
    if pf < 1.0 or dd > 12:
        return "RESTRICTED"
    if pf < 1.25 or dd > 10:
        return "WARNING"
    return "OPEN"


def next_state(current: str, raw: str, pf: float, dd: float, trades: int, streak: dict) -> tuple:
    """Hysteresis: 3 consecutive fails to close, 7 consecutive passes (PF>1.35, DD<8, trades>=100) to reopen; DISABLED is sticky."""
    fail = streak.get("fail", 0) + 1 if raw in ("RESTRICTED", "DISABLED") else 0
    ok = streak.get("pass", 0) + 1 if (pf > 1.35 and dd < 8 and trades >= 100) else 0
    streak = {"fail": fail, "pass": ok}
    if current == "DISABLED":
        return "DISABLED", streak
    if raw == "DISABLED":
        return "DISABLED", streak
    if current == "RESTRICTED":
        return ("OPEN" if ok >= 7 else "RESTRICTED"), streak
    if raw == "RESTRICTED":
        return ("RESTRICTED" if fail >= 3 or dd > 12 else "WARNING"), streak
    return raw, streak


async def evaluate(uid: str, strategy: str) -> dict:
    stats = await rolling(uid)
    s = stats["by_strategy"].get(strategy) or trade_stats([])
    pf = float("inf") if s["pf_inf"] else (s["profit_factor"] or 0.0)
    dd, n = stats["max_drawdown"], s["trades"]
    doc = await db.gates.find_one({"user_id": uid, "strategy": strategy}, {"_id": 0}) or {"state": "OPEN", "streak": {}, "history": []}
    raw = classify(pf, dd, n)
    state, streak = next_state(doc["state"], raw, pf, dd, n, doc.get("streak") or {})
    out = {"user_id": uid, "strategy": strategy, "state": state, "raw": raw, "profit_factor": s["profit_factor"], "pf_inf": s["pf_inf"],
           "drawdown": dd, "trades": n, "min_trades": MIN_TRADES, "streak": streak, "updated_at": now_ms(),
           "reason": f"PF {'∞' if s['pf_inf'] else s['profit_factor']} · DD {dd}% · {n} trades/30d" + (" · not enough trades yet, gate open by default" if n < MIN_TRADES else "")}
    hist = (doc.get("history") or [])[-20:]
    if state != doc["state"]:
        hist.append({"ts": now_ms(), "old": doc["state"], "new": state, "profit_factor": s["profit_factor"], "drawdown": dd})
        await db.audit_log.insert_one({"event": "strategy_gate_changed", "user_id": uid, "strategy": strategy, "oldState": doc["state"], "newState": state,
                                       "profitFactor": s["profit_factor"], "drawdown": dd, "ts": now_ms()})
    out["history"] = hist
    await db.gates.update_one({"user_id": uid, "strategy": strategy}, {"$set": out}, upsert=True)
    return out


async def kill_switch(uid: str):
    """3 consecutive losses, daily loss > 3% or weekly drawdown > 8% pauses new entries."""
    now = now_ms()
    sells = [t async for t in db.bot_trades.find({"user_id": uid, "side": "SELL"}, {"_id": 0, "pnl": 1, "ts": 1}).sort("ts", -1).limit(3)]
    if len(sells) == 3 and all(float(t.get("pnl") or 0) <= 0 for t in sells):
        return "3 consecutive losses"
    day = [p["equity"] async for p in db.bot_pnl.find({"user_id": uid, "ts": {"$gte": now - 86400_000}}, {"_id": 0, "equity": 1}).sort("ts", 1)]
    if len(day) > 1 and day[0] > 0 and (day[0] - day[-1]) / day[0] * 100 > 3:
        return f"daily loss {(day[0] - day[-1]) / day[0] * 100:.1f}% > 3%"
    week = [p["equity"] async for p in db.bot_pnl.find({"user_id": uid, "ts": {"$gte": now - 7 * 86400_000}}, {"_id": 0, "equity": 1}).sort("ts", 1)]
    if max_drawdown_pct(week) > 8:
        return f"weekly drawdown {max_drawdown_pct(week)}% > 8%"
    return None


async def allows_entry(uid: str, strategy: str) -> tuple:
    g = await db.gates.find_one({"user_id": uid, "strategy": strategy}, {"_id": 0})
    if not g or time.time() * 1000 - g.get("updated_at", 0) > 60_000:
        g = await evaluate(uid, strategy)
    return g["state"] in ("OPEN", "WARNING"), g
