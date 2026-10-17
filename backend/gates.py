import time

import alerts
from db import db, now_ms

WINDOW_MS = 30 * 86400 * 1000
MIN_TRADES = 30
SYMBOL_MIN_TRADES = 20
SYMBOL_PF_KILL = 0.9
ALL_SYMBOLS = ("BTC", "ETH", "SOL")


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


def loss_streak(pnls: list) -> int:
    n = 0
    for p in reversed(pnls):
        if p > 0:
            break
        n += 1
    return n


def trade_stats(trades: list) -> dict:
    pnls = [float(t.get("pnl") or 0) for t in sorted(trades, key=lambda t: t.get("ts", 0)) if t.get("side") == "SELL"]
    wins = [p for p in pnls if p > 0]
    pf = profit_factor(pnls)
    return {"trades": len(pnls), "wins": len(wins), "losses": len(pnls) - len(wins),
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
            "profit_factor": None if pf == float("inf") else round(pf, 2), "pf_inf": pf == float("inf"),
            "avg_gain": round(sum(wins) / len(wins), 4) if wins else 0.0,
            "avg_loss": round(sum(p for p in pnls if p <= 0) / max(len(pnls) - len(wins), 1), 4),
            "net_pnl": round(sum(pnls), 4), "loss_streak": loss_streak(pnls)}


async def rolling(uid: str, since: int = 0) -> dict:
    since = max(now_ms() - WINDOW_MS, since or 0)
    trades = [t async for t in db.bot_trades.find({"user_id": uid, "ts": {"$gte": since}}, {"_id": 0})]
    eq = [p["equity"] async for p in db.bot_pnl.find({"user_id": uid, "ts": {"$gte": since}, "equity": {"$gt": 0}}, {"_id": 0, "equity": 1}).sort("ts", 1)]
    by_asset = {a: trade_stats([t for t in trades if t.get("asset", "BTC") == a]) for a in sorted({t.get("asset", "BTC") for t in trades} | set(ALL_SYMBOLS))}
    by_strategy = {s: trade_stats([t for t in trades if t.get("strategy", "luchii003") == s]) for s in ("luchii003", "aggressive")}
    return {"window_days": 30, "since": since, "overall": trade_stats(trades), "max_drawdown": max_drawdown_pct(eq), "by_asset": by_asset, "by_strategy": by_strategy}


def classify(pf: float, dd: float, trades: int, streak: int = 0) -> str:
    """OPEN: PF>1.10 & DD<8. WARNING: PF<1.10 or DD>8. RESTRICTED: PF<1.0 or DD>8 (persisting). DISABLED: PF<0.8, DD>15 or 10 losses in a row."""
    if streak >= 10:
        return "DISABLED"
    if trades < MIN_TRADES:
        return "OPEN"
    if pf < 0.8 or dd > 15:
        return "DISABLED"
    if pf < 1.0 or dd > 8:
        return "RESTRICTED"
    if pf < 1.10:
        return "WARNING"
    return "OPEN"


def next_state(current: str, raw: str, pf: float, dd: float, trades: int, streak: dict) -> tuple:
    """Hysteresis: 3 consecutive fails to close, 7 consecutive passes (PF>1.35, DD<8, trades>=100) to reopen; DISABLED is sticky."""
    fail = streak.get("fail", 0) + 1 if raw in ("RESTRICTED", "DISABLED") else 0
    ok = streak.get("pass", 0) + 1 if (pf > 1.35 and dd < 8 and trades >= 100) else 0
    streak = {"fail": fail, "pass": ok}
    if current == "DISABLED" or raw == "DISABLED":
        return "DISABLED", streak
    if current == "RESTRICTED":
        return ("OPEN" if ok >= 7 else "RESTRICTED"), streak
    if raw == "RESTRICTED":
        return ("RESTRICTED" if fail >= 3 or dd > 12 else "WARNING"), streak
    return raw, streak


ICON = {"OPEN": "✅ LUCHII ENABLED", "WARNING": "⚠️ LUCHII STRATEGY WARNING", "RESTRICTED": "⛔ LUCHII RESTRICTED", "DISABLED": "🛑 LUCHII DISABLED"}


async def _alert(uid: str, text: str):
    bot = await db.bots.find_one({"user_id": uid}, {"_id": 0, "alerts_enabled": 1, "telegram_chat_id": 1})
    await alerts.notify(bot, text)


async def evaluate(uid: str, strategy: str) -> dict:
    doc = await db.gates.find_one({"user_id": uid, "strategy": strategy}, {"_id": 0}) or {"state": "OPEN", "streak": {}, "history": [], "since": 0}
    stats = await rolling(uid, doc.get("since") or 0)
    s = stats["by_strategy"].get(strategy) or trade_stats([])
    pf = float("inf") if s["pf_inf"] else (s["profit_factor"] or 0.0)
    dd, n = stats["max_drawdown"], s["trades"]
    raw = classify(pf, dd, n, s["loss_streak"])
    state, streak = next_state(doc["state"], raw, pf, dd, n, doc.get("streak") or {})
    pf_txt = "∞" if s["pf_inf"] else s["profit_factor"]
    out = {"user_id": uid, "strategy": strategy, "state": state, "raw": raw, "profit_factor": s["profit_factor"], "pf_inf": s["pf_inf"], "win_rate": s["win_rate"],
           "drawdown": dd, "trades": n, "loss_streak": s["loss_streak"], "min_trades": MIN_TRADES, "streak": streak, "since": doc.get("since") or 0, "updated_at": now_ms(),
           "reason": f"PF {pf_txt} · DD {dd}% · {n} trades/30d" + (f" · {s['loss_streak']} losses in a row" if s["loss_streak"] >= 3 else "")
           + (" · not enough trades yet, gate open by default" if n < MIN_TRADES and state == "OPEN" else "")}
    hist = (doc.get("history") or [])[-20:]
    if state != doc["state"]:
        hist.append({"ts": now_ms(), "old": doc["state"], "new": state, "profit_factor": s["profit_factor"], "drawdown": dd, "by": "auto"})
        await db.audit_log.insert_one({"event": "strategy_gate_changed", "user_id": uid, "strategy": strategy, "oldState": doc["state"], "newState": state,
                                       "profitFactor": s["profit_factor"], "drawdown": dd, "by": "auto", "ts": now_ms()})
        await _alert(uid, f"{ICON[state]}\n\nStrategy: {strategy}\nPF: {pf_txt}\nWin: {s['win_rate']}%\nDD: {dd}%\nTrades: {n}\nStatus: {state}")
    out["history"] = hist
    await db.gates.update_one({"user_id": uid, "strategy": strategy}, {"$set": out}, upsert=True)
    return out


async def reset_gate(uid: str, strategy: str, who: str = "owner") -> dict:
    doc = await db.gates.find_one({"user_id": uid, "strategy": strategy}, {"_id": 0}) or {"state": "DISABLED", "history": []}
    ts = now_ms()
    hist = (doc.get("history") or [])[-20:] + [{"ts": ts, "old": doc["state"], "new": "OPEN", "profit_factor": doc.get("profit_factor"), "drawdown": doc.get("drawdown"), "by": who}]
    await db.gates.update_one({"user_id": uid, "strategy": strategy},
                              {"$set": {"state": "OPEN", "streak": {}, "since": ts, "history": hist, "updated_at": ts, "reason": "re-enabled by owner · stats restart from now"}}, upsert=True)
    await db.audit_log.insert_one({"event": "strategy_gate_reset", "user_id": uid, "strategy": strategy, "oldState": doc["state"], "newState": "OPEN", "by": who, "ts": ts})
    await _alert(uid, f"✅ LUCHII ENABLED\n\nStrategy: {strategy}\nApproved By: Owner\nStatus: OPEN\n(rolling stats restart from now)")
    return await evaluate(uid, strategy)


async def symbol_state(uid: str, symbol: str, stats: dict = None) -> dict:
    """Per-symbol kill switch: PF < 0.9 over 20+ trades disables the symbol until the owner re-enables it. SOL starts disabled by policy."""
    doc = await db.symbol_gates.find_one({"user_id": uid, "symbol": symbol}, {"_id": 0})
    if not doc:
        doc = {"user_id": uid, "symbol": symbol, "disabled": symbol == "SOL", "by": "policy" if symbol == "SOL" else None,
               "reason": "disabled by policy until the owner re-enables it" if symbol == "SOL" else None, "since": 0, "ts": now_ms()}
        await db.symbol_gates.update_one({"user_id": uid, "symbol": symbol}, {"$setOnInsert": doc}, upsert=True)
    if doc["disabled"]:
        return doc
    st = ((stats if stats and not doc.get("since") else await rolling(uid, doc.get("since") or 0))["by_asset"].get(symbol)) or trade_stats([])
    pf = float("inf") if st["pf_inf"] else (st["profit_factor"] or 0.0)
    doc.update({"profit_factor": st["profit_factor"], "trades": st["trades"], "win_rate": st["win_rate"]})
    if st["trades"] >= SYMBOL_MIN_TRADES and pf < SYMBOL_PF_KILL:
        doc.update({"disabled": True, "by": "auto", "reason": f"PF {st['profit_factor']} < {SYMBOL_PF_KILL} over {st['trades']} trades", "ts": now_ms()})
        await db.symbol_gates.update_one({"user_id": uid, "symbol": symbol}, {"$set": doc})
        await db.audit_log.insert_one({"event": "symbol_disabled", "user_id": uid, "symbol": symbol, "reason": doc["reason"], "by": "auto", "ts": now_ms()})
        await _alert(uid, f"🛑 LUCHII DISABLED\n\nSymbol: {symbol}\nReason: {doc['reason']}\nTrades: {st['trades']}\nStatus: DISABLED")
    return doc


async def set_symbol(uid: str, symbol: str, enabled: bool, who: str = "owner") -> dict:
    ts = now_ms()
    doc = {"user_id": uid, "symbol": symbol, "disabled": not enabled, "by": who, "since": ts if enabled else 0, "ts": ts,
           "reason": None if enabled else "disabled by owner"}
    await db.symbol_gates.update_one({"user_id": uid, "symbol": symbol}, {"$set": doc}, upsert=True)
    await db.audit_log.insert_one({"event": "symbol_enabled" if enabled else "symbol_disabled", "user_id": uid, "symbol": symbol, "by": who, "ts": ts})
    await _alert(uid, f"{'✅ LUCHII ENABLED' if enabled else '🛑 LUCHII DISABLED'}\n\nSymbol: {symbol}\nApproved By: Owner\nStatus: {'OPEN' if enabled else 'DISABLED'}")
    return {k: v for k, v in doc.items() if k != "user_id"}


async def symbols(uid: str) -> dict:
    stats = await rolling(uid)
    return {s: {k: v for k, v in (await symbol_state(uid, s, stats)).items() if k != "user_id"} for s in ALL_SYMBOLS}


async def kill_switch(uid: str):
    """3 consecutive losses, daily loss > 3% or weekly drawdown > 8% pauses new entries."""
    now = now_ms()
    sells = [t async for t in db.bot_trades.find({"user_id": uid, "side": "SELL", "ts": {"$gte": now - 86400_000}}, {"_id": 0, "pnl": 1, "ts": 1}).sort("ts", -1).limit(3)]
    if len(sells) == 3 and all(float(t.get("pnl") or 0) <= 0 for t in sells) and now - sells[0]["ts"] < 4 * 3600_000:
        return f"3 consecutive losses · entries paused {round((4 * 3600_000 - (now - sells[0]['ts'])) / 60000)} min"
    day = [p["equity"] async for p in db.bot_pnl.find({"user_id": uid, "ts": {"$gte": now - 86400_000}, "equity": {"$gt": 0}}, {"_id": 0, "equity": 1}).sort("ts", 1)]
    if len(day) > 1 and (day[0] - day[-1]) / day[0] * 100 > 3:
        return f"daily loss {(day[0] - day[-1]) / day[0] * 100:.1f}% > 3%"
    week = [p["equity"] async for p in db.bot_pnl.find({"user_id": uid, "ts": {"$gte": now - 7 * 86400_000}, "equity": {"$gt": 0}}, {"_id": 0, "equity": 1}).sort("ts", 1)]
    if max_drawdown_pct(week) > 8:
        return f"weekly drawdown {max_drawdown_pct(week)}% > 8%"
    return None


async def allows_entry(uid: str, strategy: str) -> tuple:
    g = await db.gates.find_one({"user_id": uid, "strategy": strategy}, {"_id": 0})
    if not g or time.time() * 1000 - g.get("updated_at", 0) > 60_000:
        g = await evaluate(uid, strategy)
    return g["state"] in ("OPEN", "WARNING"), g
