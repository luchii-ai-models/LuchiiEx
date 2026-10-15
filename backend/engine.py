import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import alerts
import brokers
from db import db, now_ms
from exchange_service import client_from_doc, equity_usd, market_order, symbol_for
from market import SNAPSHOT

START_CASH = 10000.0
log_ = logging.getLogger("engine")

DEFAULT_BOT = {
    "status": "stopped", "mode": "live", "position_usd": 50.0, "max_order_usd": 0.0, "daily_loss_limit": 0.0,
    "manual_confirm": False, "kill_switch": False, "cash": START_CASH, "position_qty": 0.0, "entry_price": 0.0,
    "realized_pnl": 0.0, "trades_count": 0, "wins": 0, "losses": 0, "alerts_enabled": True, "live_equity": None,
    "last_tick": 0, "pocketoption": None,
}


async def get_creds(uid: str):
    return await db.exchange_credentials.find_one({"user_id": uid}, {"_id": 0})


async def with_exchange(uid: str, fn):
    from fastapi import HTTPException
    creds = await get_creds(uid)
    if not creds:
        raise HTTPException(400, "No exchange connected")
    ex = client_from_doc(creds)
    try:
        return await fn(ex)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {str(e)[:240]}")
    finally:
        await ex.close()


def equity(bot: dict) -> float:
    if bot["mode"] == "live":
        return bot.get("live_equity") or 0.0
    return round(bot["cash"] + bot["position_qty"] * (SNAPSHOT.get("price") or 0), 2)


async def log(db_, uid, d, price, fill=None):
    c = SNAPSHOT.get("candles") or []
    pct = round((c[-1]["c"] - c[-2]["c"]) / c[-2]["c"] * 100, 3) if len(c) > 1 else 0.0
    await db_.bot_logs.insert_one({"user_id": uid, "ts": now_ms(), "action": d["action"], "price": float(price or 0),
                                   "pct": pct, "engine": d.get("engine", "algo"), "confidence": float(d.get("confidence", 0.5)),
                                   "reason": d.get("reason", ""), "fill": fill})


def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def signal(bot: dict) -> dict:
    closes = [c["c"] for c in SNAPSHOT.get("candles") or []]
    s7, s25, r = _sma(closes, 7), _sma(closes, 25), SNAPSHOT.get("rsi")
    if s7 is None or s25 is None or r is None:
        return {"action": "HOLD", "confidence": 0.3, "reason": "warming up market data"}
    trend = (s7 - s25) / s25 * 100
    conf = min(0.95, 0.5 + abs(trend) * 5)
    flat = bot["position_qty"] <= 0
    if flat and s7 > s25 and r < 70:
        return {"action": "BUY", "confidence": conf, "reason": f"SMA7>SMA25 ({trend:+.3f}%) · RSI {r}"}
    if not flat and (s7 < s25 or r > 75):
        return {"action": "SELL", "confidence": conf, "reason": f"{'SMA7<SMA25' if s7 < s25 else 'RSI overbought'} · RSI {r}"}
    return {"action": "HOLD", "confidence": conf, "reason": f"{'in position' if not flat else 'no entry'} · trend {trend:+.3f}% · RSI {r}"}


def order_usd(bot: dict) -> float:
    usd = float(bot["position_usd"])
    if bot.get("max_order_usd"):
        usd = min(usd, float(bot["max_order_usd"]))
    return usd


async def set_bot(uid: str, **fields):
    await db.bots.update_one({"user_id": uid}, {"$set": fields})


async def record_fill(bot: dict, side: str, price: float, qty: float, fill: str, d: dict):
    uid = bot["user_id"]
    pnl = 0.0
    upd = {"trades_count": bot["trades_count"] + 1}
    if side == "BUY":
        upd.update(position_qty=qty, entry_price=price)
        if bot["mode"] != "live":
            upd["cash"] = bot["cash"] - qty * price
    else:
        pnl = round((price - bot["entry_price"]) * qty, 2)
        upd.update(position_qty=0.0, entry_price=0.0, realized_pnl=round(bot["realized_pnl"] + pnl, 2),
                   wins=bot["wins"] + (pnl > 0), losses=bot["losses"] + (pnl <= 0))
        if bot["mode"] != "live":
            upd["cash"] = bot["cash"] + qty * price
    await set_bot(uid, **upd)
    await db.bot_trades.insert_one({"user_id": uid, "ts": now_ms(), "side": side, "price": price, "qty": qty, "pnl": pnl})
    await log(db, uid, d, price, fill)
    await alerts.notify(bot, f"{'🟢' if side == 'BUY' else '🔴'} {side} {qty} BTC @ ${price:,.2f}" + (f" · P&L {pnl:+.2f}" if side == "SELL" else ""))
    bot = await db.bots.find_one({"user_id": uid})
    await mirror_all(db, bot, side)
    await check_daily_loss(bot)


async def check_daily_loss(bot: dict):
    lim = float(bot.get("daily_loss_limit") or 0)
    if lim <= 0:
        return
    start = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    today = sum(t["pnl"] async for t in db.bot_trades.find({"user_id": bot["user_id"], "side": "SELL", "ts": {"$gte": start}}))
    if today <= -lim:
        await set_bot(bot["user_id"], kill_switch=True, status="stopped")
        await log(db, bot["user_id"], {"action": "HOLD", "engine": "guard", "confidence": 1.0,
                                        "reason": f"Daily loss limit ${lim} hit ({today:.2f}) · KILL SWITCH"}, SNAPSHOT.get("price"))
        await alerts.notify(bot, f"🛑 Kill switch tripped: daily loss {today:.2f}")


async def execute(bot: dict, d: dict):
    side, price = d["action"], SNAPSHOT["price"]
    if bot["mode"] != "live":
        qty = round(order_usd(bot) / price, 6) if side == "BUY" else bot["position_qty"]
        return await record_fill(bot, side, price, qty, "PAPER FILLED", d)
    creds = await get_creds(bot["user_id"])
    ex = client_from_doc(creds)
    symbol = symbol_for(creds["exchange"])
    try:
        o = await market_order(ex, symbol, side, order_usd(bot), bot["position_qty"], price)
        avg = float(o.get("average") or price)
        qty = float(o.get("filled") or 0) or (order_usd(bot) / price if side == "BUY" else bot["position_qty"])
        await record_fill(bot, side, avg, round(qty, 8), f"LIVE FILLED #{o.get('id')}", d)
    except Exception as e:
        await log(db, bot["user_id"], {**d, "reason": f"{d['reason']} · order error: {type(e).__name__}: {str(e)[:120]}"}, price, "REJECTED")
    finally:
        await ex.close()


async def refresh_live_equity(bot: dict):
    creds = await get_creds(bot["user_id"])
    if not creds:
        return
    ex = client_from_doc(creds)
    try:
        bal = await ex.fetch_balance()
        await set_bot(bot["user_id"], live_equity=equity_usd(bal, SNAPSHOT.get("price") or 0))
    except Exception as e:
        log_.warning("live equity failed: %s", e)
    finally:
        await ex.close()


async def tick_bot(bot: dict):
    uid = bot["user_id"]
    if bot.get("kill_switch") or not SNAPSHOT.get("price"):
        return
    await set_bot(uid, last_tick=time.time())
    if bot["mode"] == "live":
        await refresh_live_equity(bot)
    if bot.get("pending"):
        return
    d = signal(bot)
    d["engine"] = "algo"
    if d["action"] == "HOLD":
        if time.time() - bot.get("last_hold_log", 0) >= 60:
            await set_bot(uid, last_hold_log=time.time())
            await log(db, uid, d, SNAPSHOT["price"])
    elif bot["mode"] == "live" and bot.get("manual_confirm"):
        await set_bot(uid, pending={"action": d["action"], "price": SNAPSHOT["price"], "size_usd": order_usd(bot),
                                    "reason": d["reason"], "decision": d, "ts": now_ms()})
        await alerts.notify(bot, f"⏳ Confirm {d['action']} BTC @ ${SNAPSHOT['price']:,.2f} in the terminal")
    else:
        await execute(bot, d)
    bot = await db.bots.find_one({"user_id": uid})
    await db.bot_pnl.insert_one({"user_id": uid, "ts": now_ms(), "equity": equity(bot)})


async def mirror_all(db_, bot, side):
    import pocket
    po = bot.get("pocketoption")
    if po:
        try:
            m = await pocket.mirror(bot["user_id"], po, side)
            metrics = {"ok": True, "last_ms": m["last_ms"], "ts": m["ts"]}
            await log(db_, bot["user_id"], {"action": side, "engine": "mirror", "confidence": 1.0,
                                            "reason": f"PocketOption {po['asset']} ${po['amount']} {po['duration']}s"},
                      SNAPSHOT.get("price") or 0, f"PO #{m['trade_id'][:10]}")
        except Exception as e:
            metrics = {"ok": False, "last_ms": 0, "ts": now_ms(), "error": str(e)[:200]}
            await log(db_, bot["user_id"], {"action": side, "engine": "mirror", "confidence": 1.0,
                                            "reason": f"PocketOption error: {str(e)[:120]}"}, SNAPSHOT.get("price") or 0, "REJECTED")
        await db_.bots.update_one({"user_id": bot["user_id"]}, {"$set": {"pocketoption.metrics": metrics}})
    mt = bot.get("metaapi")
    if not mt:
        return
    try:
        m = await brokers.trade(mt, side)
        metrics = {"ok": True, "last_ms": m["last_ms"], "ts": m["ts"]}
        await log(db_, bot["user_id"], {"action": side, "engine": "mirror", "confidence": 1.0,
                                        "reason": f"MetaTrader {mt['symbols']} {mt['volume']} lots"}, SNAPSHOT.get("price") or 0, "MT FILLED")
    except Exception as e:
        metrics = {"ok": False, "last_ms": 0, "ts": now_ms(), "error": str(e)[:200]}
        await log(db_, bot["user_id"], {"action": side, "engine": "mirror", "confidence": 1.0,
                                        "reason": f"MetaTrader error: {str(e)[:120]}"}, SNAPSHOT.get("price") or 0, "REJECTED")
    await db_.bots.update_one({"user_id": bot["user_id"]}, {"$set": {"metaapi.metrics": metrics, "metaapi.warm": metrics["ok"]}})


async def scheduler():
    every = float(os.environ["BOT_TICK_SECONDS"])
    while True:
        await asyncio.sleep(every)
        async for bot in db.bots.find({"status": "running"}):
            try:
                await tick_bot(bot)
            except Exception as e:
                log_.exception("tick failed for %s: %s", bot.get("user_id"), e)
