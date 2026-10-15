import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import alerts
import brokers
from db import db, now_ms
from exchange_service import all_balances, client_from_doc, ensure_funds, friendly, market_order, resolve_symbol, total_usd
from market import SNAPSHOT

START_CASH = 10000.0
log_ = logging.getLogger("engine")

DEFAULT_BOT = {
    "status": "stopped", "mode": "live", "position_usd": 50.0, "max_order_usd": 0.0, "daily_loss_limit": 0.0,
    "manual_confirm": False, "kill_switch": False, "cash": START_CASH, "position_qty": 0.0, "entry_price": 0.0,
    "realized_pnl": 0.0, "trades_count": 0, "wins": 0, "losses": 0, "alerts_enabled": True, "live_equity": None,
    "last_tick": 0, "pocketoption": None, "auto_fund": True, "start_equity": None, "start_ts": None,
    "take_profit_pct": 1.5, "stop_loss_pct": 1.0, "trend_filter": True, "volume_filter": True, "trailing_stop": True, "peak_price": 0.0,
    "partial_tp": True, "tp_taken": False,
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
        raise HTTPException(400, friendly(e))
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
    candles = SNAPSHOT.get("candles") or []
    closes = [c["c"] for c in candles]
    price = SNAPSHOT.get("price") or (closes[-1] if closes else 0)
    s7, s25, r = _sma(closes, 7), _sma(closes, 25), SNAPSHOT.get("rsi")
    if s7 is None or s25 is None or r is None or len(candles) < 30:
        return {"action": "HOLD", "confidence": 0.3, "reason": "warming up market data"}
    trend = (s7 - s25) / s25 * 100
    conf = min(0.95, 0.5 + abs(trend) * 5)
    flat = bot["position_qty"] <= 0
    tp, sl = float(bot.get("take_profit_pct") or 0), float(bot.get("stop_loss_pct") or 0)
    if not flat and bot.get("entry_price"):
        move = (price - bot["entry_price"]) / bot["entry_price"] * 100
        if tp and move >= tp - 1e-9 and not bot.get("tp_taken"):
            if bot.get("partial_tp", True):
                return {"action": "SELL", "partial": True, "confidence": 0.95, "reason": f"PARTIAL TAKE PROFIT {move:+.2f}% (target +{tp}%) · selling half, rest rides trailing stop · RSI {r}"}
            return {"action": "SELL", "confidence": 0.95, "reason": f"TAKE PROFIT hit {move:+.2f}% (target +{tp}%) · RSI {r}"}
        peak = max(float(bot.get("peak_price") or 0), bot["entry_price"])
        if sl and bot.get("trailing_stop", True) and peak > bot["entry_price"]:
            drop = (price - peak) / peak * 100
            if drop <= -sl:
                return {"action": "SELL", "confidence": 0.95, "reason": f"TRAILING STOP hit {drop:+.2f}% from peak ${peak:,.0f} (locked {move:+.2f}%) · RSI {r}"}
        elif sl and move <= -sl:
            return {"action": "SELL", "confidence": 0.95, "reason": f"STOP LOSS hit {move:+.2f}% (limit -{sl}%) · RSI {r}"}
    if flat and s7 > s25 and r < 70:
        if bot.get("trend_filter", True):
            s25_prev = _sma(closes[:-5], 25)
            if price < s25 or (s25_prev and s25 <= s25_prev):
                return {"action": "HOLD", "confidence": conf, "reason": f"trend filter · price below/flat SMA25 · trend {trend:+.3f}% · RSI {r}"}
        if bot.get("volume_filter", True):
            vols = [c["v"] for c in candles[-21:-1]]
            avg_v = sum(vols) / len(vols) if vols else 0
            if avg_v and candles[-2]["v"] < avg_v:
                return {"action": "HOLD", "confidence": conf, "reason": f"volume filter · last vol {candles[-2]['v']:.2f} < avg {avg_v:.2f} · RSI {r}"}
        return {"action": "BUY", "confidence": conf, "reason": f"SMA7>SMA25 ({trend:+.3f}%) · RSI {r} · trend+volume confirmed"}
    if not flat and bot.get("entry_price"):
        move = (price - bot["entry_price"]) / bot["entry_price"] * 100
        if s7 < s25 and move < -0.3:
            return {"action": "SELL", "confidence": conf, "reason": f"SMA7<SMA25 while losing {move:+.2f}% · cutting early · RSI {r}"}
    if not flat and (s7 < s25 or r > 75):
        return {"action": "HOLD", "confidence": conf, "reason": f"in position {((price - bot['entry_price']) / bot['entry_price'] * 100) if bot.get('entry_price') else 0:+.2f}% · winner rides TP/trailing stop · RSI {r}"}
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
        upd.update(position_qty=qty, entry_price=price, peak_price=price, tp_taken=False)
        if bot["mode"] != "live":
            upd["cash"] = bot["cash"] - qty * price
    elif d.get("partial"):
        pnl = round((price - bot["entry_price"]) * qty, 4)
        upd.update(position_qty=round(max(bot["position_qty"] - qty, 0), 8), tp_taken=True, realized_pnl=round(bot["realized_pnl"] + pnl, 4))
        if bot["mode"] != "live":
            upd["cash"] = bot["cash"] + qty * price
    else:
        pnl = round((price - bot["entry_price"]) * qty, 4)
        upd.update(position_qty=0.0, entry_price=0.0, peak_price=0.0, tp_taken=False, realized_pnl=round(bot["realized_pnl"] + pnl, 4),
                   wins=bot["wins"] + (pnl > 0), losses=bot["losses"] + (pnl <= 0))
        if bot["mode"] != "live":
            upd["cash"] = bot["cash"] + qty * price
    await set_bot(uid, **upd)
    await db.bot_trades.insert_one({"user_id": uid, "ts": now_ms(), "side": side, "price": price, "qty": qty, "pnl": pnl})
    await log(db, uid, d, price, fill)
    await alerts.notify(bot, f"{'🟢' if side == 'BUY' else '🔴'} {side} {qty} BTC @ ${price:,.2f}" + (f" · P&L {pnl:+.2f}" if side == "SELL" else ""))
    bot = await db.bots.find_one({"user_id": uid})
    if not d.get("partial"):
        asyncio.create_task(mirror_all(db, bot, side))
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


async def auto_fund(ex, uid: str, exchange: str, symbol: str, side: str, usd: float, qty: float, enabled: bool = True):
    if not enabled:
        return
    cur, need = (symbol.split("/")[1], usd) if side == "BUY" else ("BTC", qty)
    try:
        moved = await ensure_funds(ex, exchange, cur, need)
    except Exception as e:
        return await log(db, uid, {"action": "HOLD", "engine": "fund", "confidence": 1.0,
                                   "reason": f"Auto-fund failed: {friendly(e)[:140]}"}, SNAPSHOT.get("price"), "REJECTED")
    if moved:
        await log(db, uid, {"action": "HOLD", "engine": "fund", "confidence": 1.0,
                            "reason": f"Auto-fund: moved {moved} {cur} funding → trading"}, SNAPSHOT.get("price"), "TRANSFER")


async def wait_log(uid: str, bot: dict, reason: str):
    if time.time() - bot.get("last_wait_log", 0) < 300:
        return
    await set_bot(uid, last_wait_log=time.time())
    await log(db, uid, {"action": "HOLD", "engine": "fund", "confidence": 1.0, "reason": reason}, SNAPSHOT.get("price"), "WAITING")


async def free_of(ex, cur: str) -> float:
    return float(((await ex.fetch_balance()).get(cur) or {}).get("free") or 0)


async def execute(bot: dict, d: dict):
    side, price = d["action"], SNAPSHOT["price"]
    if bot["mode"] != "live":
        qty = round(order_usd(bot) / price, 6) if side == "BUY" else bot["position_qty"] * (0.5 if d.get("partial") else 1)
        return await record_fill(bot, side, price, qty, "PAPER FILLED", d)
    uid = bot["user_id"]
    creds = await get_creds(uid)
    ex = client_from_doc(creds)
    try:
        symbol = await resolve_symbol(ex, creds["exchange"])
        quote = symbol.split("/")[1]
        usd = order_usd(bot)
        await auto_fund(ex, uid, creds["exchange"], symbol, side, usd, bot["position_qty"], bot.get("auto_fund", True))
        price = (await ex.fetch_ticker(symbol))["last"] or price
        min_qty = float(((ex.market(symbol).get("limits") or {}).get("amount") or {}).get("min") or 0)
        if side == "BUY":
            have = await free_of(ex, quote)
            if have < usd:
                return await wait_log(uid, bot, f"BUY signal skipped · need {usd} {quote} in trading, have {round(have, 4)}. Deposit {quote}/USDC, or hold BTC so the bot can trade it on SELL signals.")
            if usd / price < min_qty:
                return await wait_log(uid, bot, f"BUY signal skipped · ${usd} is below the {symbol} minimum ({min_qty} BTC ≈ ${round(min_qty * price, 2)}). Raise POSITION SIZE.")
            o = await market_order(ex, symbol, "BUY", usd, 0, price)
            qty = float(o.get("filled") or 0) or usd / price
        else:
            want = bot["position_qty"] * (0.5 if d.get("partial") else 1)
            qty = min(want, await free_of(ex, "BTC"))
            if d.get("partial") and qty < min_qty:
                d = {**d, "partial": False, "reason": d["reason"].replace("PARTIAL TAKE PROFIT", "TAKE PROFIT (half below exchange minimum, selling all)")}
                qty = min(bot["position_qty"], await free_of(ex, "BTC"))
            if qty < max(min_qty, 1e-8):
                return await wait_log(uid, bot, f"SELL signal skipped · only {qty} BTC free in trading (minimum {min_qty}). Waiting for deposit to credit.")
            o = await market_order(ex, symbol, "SELL", 0, qty, price)
            qty = float(o.get("filled") or 0) or qty
        await record_fill(bot, side, float(o.get("average") or price), round(qty, 8), f"LIVE FILLED #{o.get('id')}", d)
    except Exception as e:
        await log(db, uid, {**d, "reason": f"{d['reason']} · order error: {friendly(e)[:160]}"}, price, "REJECTED")
    finally:
        await ex.close()


async def sync_position(bot: dict, bal: dict):
    price = SNAPSHOT.get("price") or 0
    btc = sum(float(b.get("BTC") or 0) for b in bal.values())
    uid = bot["user_id"]
    if bot.get("position_qty", 0) <= 0 and btc >= 1e-5:
        await set_bot(uid, position_qty=round(btc, 8), entry_price=price, peak_price=price)
        await log(db, uid, {"action": "HOLD", "engine": "sync", "confidence": 1.0,
                            "reason": f"Synced position: {round(btc, 8)} BTC on exchange · entry set to ${price:,.2f} (P&L counted from now)"}, price, "SYNCED")
    elif bot.get("position_qty", 0) > 0 and btc < bot["position_qty"] * 0.5:
        await set_bot(uid, position_qty=round(btc, 8) if btc >= 1e-5 else 0.0, entry_price=bot["entry_price"] if btc >= 1e-5 else 0.0)


async def refresh_live_equity(bot: dict):
    creds = await get_creds(bot["user_id"])
    if not creds:
        return
    ex = client_from_doc(creds)
    try:
        bal = await all_balances(ex, creds["exchange"])
        eq = total_usd(bal, SNAPSHOT.get("price") or 0)
        await set_bot(bot["user_id"], live_equity=eq)
        fresh = await db.bots.find_one({"user_id": bot["user_id"]}, {"start_equity": 1})
        if eq > 0 and fresh and not fresh.get("start_equity"):
            await set_bot(bot["user_id"], start_equity=eq, start_ts=now_ms())
        if "position_qty" in bot:
            await sync_position(bot, bal)
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
        bot = await db.bots.find_one({"user_id": uid})
    if bot.get("pending"):
        return
    if bot["position_qty"] > 0 and SNAPSHOT["price"] > float(bot.get("peak_price") or 0):
        bot["peak_price"] = SNAPSHOT["price"]
        await set_bot(uid, peak_price=SNAPSHOT["price"])
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


async def mirror_solana(db_, bot, side):
    import solana_dex
    try:
        results = await solana_dex.mirror(bot["user_id"], side)
    except Exception as e:
        results = [{"mint": "wallet", "ok": False, "error": str(e)[:160]}]
    for r in results:
        if r["ok"]:
            reason = f"Solana {'SOL→' if side == 'BUY' else '→SOL '}{r['mint'][:6]}… · {r['status']} · {r['ms']}ms"
            fill = f"SOL #{r['sig'][:10]}"
        else:
            reason, fill = f"Solana {r['mint'][:6]}… error: {r['error']}", "REJECTED"
        await log(db_, bot["user_id"], {"action": side, "engine": "solana", "confidence": 1.0, "reason": reason},
                  SNAPSHOT.get("price") or 0, fill)


async def mirror_all(db_, bot, side):
    import pocket
    await mirror_solana(db_, bot, side)
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
