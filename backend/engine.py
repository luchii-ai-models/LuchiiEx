import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import alerts
import audit
import brokers
import ledger
import strategy
from db import db, now_ms
from exchange_service import (all_balances, cancel_stop, client_from_doc, ensure_funds, friendly, market_order, place_oco, place_stop,
                              resolve_symbol, stop_status, total_usd)
from market import SNAPSHOT

START_CASH = 10000.0
log_ = logging.getLogger("engine")

DEFAULT_BOT = {
    "status": "stopped", "mode": "live", "position_usd": 50.0, "max_order_usd": 0.0, "daily_loss_limit": 0.0,
    "manual_confirm": False, "kill_switch": False, "cash": START_CASH, "position_qty": 0.0, "entry_price": 0.0,
    "realized_pnl": 0.0, "trades_count": 0, "wins": 0, "losses": 0, "alerts_enabled": True, "live_equity": None,
    "last_tick": 0, "pocketoption": None, "auto_fund": True, "start_equity": None, "start_ts": None,
    "take_profit_pct": 0.6, "stop_loss_pct": 0.4, "trend_filter": True, "volume_filter": True, "trailing_stop": True, "peak_price": 0.0,
    "partial_tp": True, "tp_taken": False, "aggressive": True, "size_pct": 50.0, "assets": ["BTC", "ETH", "SOL"], "alt_positions": {},
    "strategy": "luchii003", "stop_price": 0.0, "tiers_done": 0, "entry_atr": 0.0,
}
BOOST_DEFAULTS = {"take_profit_pct": 0.6, "stop_loss_pct": 0.4, "aggressive": True, "size_pct": 50.0, "assets": ["BTC", "ETH", "SOL"]}
LUCHII003_DEFAULTS = {"strategy": "luchii003", "assets": ["BTC", "ETH", "SOL"]}


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


def pos_of(bot: dict, asset: str) -> dict:
    if asset == "BTC":
        return {"position_qty": float(bot.get("position_qty") or 0), "entry_price": float(bot.get("entry_price") or 0),
                "peak_price": float(bot.get("peak_price") or 0), "tp_taken": bool(bot.get("tp_taken")),
                "stop_price": float(bot.get("stop_price") or 0), "tiers_done": int(bot.get("tiers_done") or 0), "entry_atr": float(bot.get("entry_atr") or 0)}
    p = (bot.get("alt_positions") or {}).get(asset) or {}
    return {"position_qty": float(p.get("qty") or 0), "entry_price": float(p.get("entry") or 0),
            "peak_price": float(p.get("peak") or 0), "tp_taken": bool(p.get("tp_taken")),
            "stop_price": float(p.get("stop") or 0), "tiers_done": int(p.get("tiers") or 0), "entry_atr": float(p.get("atr") or 0)}


def pos_fields(asset: str, qty=None, entry=None, peak=None, tp_taken=None, stop=None, tiers=None, atr=None) -> dict:
    vals = {"qty": qty, "entry": entry, "peak": peak, "tp_taken": tp_taken, "stop": stop, "tiers": tiers, "atr": atr}
    if asset == "BTC":
        names = {"qty": "position_qty", "entry": "entry_price", "peak": "peak_price", "tp_taken": "tp_taken", "stop": "stop_price", "tiers": "tiers_done", "atr": "entry_atr"}
        return {names[k]: v for k, v in vals.items() if v is not None}
    return {f"alt_positions.{asset}.{k}": v for k, v in vals.items() if v is not None}


AGENT_WEIGHTS: dict = {}


def fee_round_trip_pct(bot: dict) -> float:
    """Taker fee both ways (from the exchange market, default 0.10%) + 0.02% slippage buffer, in percent."""
    rate = float(bot.get("fee_rate") or 0.001)
    return round(rate * 2 * 100 + 0.02, 4)


def net_tp_pct(bot: dict) -> float:
    tp = float(bot.get("take_profit_pct") or 0)
    return round(tp + fee_round_trip_pct(bot), 4) if tp else 0.0


def net_pnl(bot: dict, entry: float, exit_px: float, qty: float) -> float:
    rate = float(bot.get("fee_rate") or 0.001)
    return round((exit_px - entry) * qty - (entry + exit_px) * qty * rate, 4)


def luchii_signal(bot: dict, asset: str) -> dict:
    m = mkt(asset)
    if not m.get("candles"):
        return {"action": "HOLD", "confidence": 0.3, "score": 0, "regime": "CHOP", "reason": f"[{asset}] warming up market data"}
    i = strategy.indicators(m["candles"], m["price"], m.get("rsi"))
    b = mkt("BTC")
    btc = strategy.indicators(b["candles"], b["price"], b.get("rsi")) if asset != "BTC" and b.get("candles") else i
    d = strategy.evaluate(asset, i, btc, {**pos_of(bot, asset), "fee_pct": fee_round_trip_pct(bot)}, AGENT_WEIGHTS.get(bot["user_id"]))
    d["confidence"] = round(0.5 + d["score"] / 200, 2)
    d["metrics"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d["metrics"].items()}
    return d


def snapshot(asset: str) -> tuple:
    """(indicators for asset, indicators for BTC) — empty dicts while warming up."""
    m, b = mkt(asset), mkt("BTC")
    i = strategy.indicators(m["candles"], m["price"], m.get("rsi")) if m.get("candles") else {}
    bi = strategy.indicators(b["candles"], b["price"], b.get("rsi")) if b.get("candles") else {}
    return i, bi


def entry_guard(bot: dict, asset: str, d: dict):
    """Hard production filters for every long entry (both strategies). Returns a rejection reason or None."""
    i, bi = snapshot(asset)
    if not i or not bi:
        return "market data warming up"
    if strategy.nan(i["adx"], i["rsi"], i["volume_ratio"], bi["ema200"], bi["rsi"], bi["trend_pct"]):
        return "indicators warming up"
    score = d.get("score") if d.get("score") is not None else strategy.entry_score(i)[0]
    conf = d.get("confidence") or 0
    checks = [(i["adx"] >= 25, f"ADX {i['adx']:.0f} < 25"), (i["rsi"] < 70, f"RSI {i['rsi']:.0f} ≥ 70"),
              (i["volume_ratio"] >= 1.2, f"volume {i['volume_ratio']:.2f}x < 1.2"), (score >= 70, f"score {score} < 70"),
              (conf >= 0.7, f"confidence {conf * 100:.0f}% < 70%")]
    if asset != "BTC":
        checks += [(bi["price"] > bi["ema200"], "BTC below EMA200"), (bi["rsi"] > 50, f"BTC RSI {bi['rsi']:.0f} ≤ 50"), (bi["trend_pct"] > 0, "BTC trend negative")]
    failed = [msg for ok, msg in checks if not ok]
    return " · ".join(failed) if failed else None


def risk_multiplier(pf, pf_inf: bool) -> float:
    if pf_inf:
        return 1.0
    if pf is None:
        return 1.0
    return 0.25 if pf < 1.0 else 0.5 if pf < 1.2 else 1.0 if pf < 1.5 else 1.5


def decide(bot: dict, asset: str) -> dict:
    if bot.get("strategy", "luchii003") == "aggressive":
        d = signal(bot, asset)
        if d.get("action") == "BUY":
            d["regime"] = snapshot(asset)[0].get("regime", "CHOP")
            d["reason"] += " · AGGRESSIVE: regime/score/BTC filters bypassed by owner choice"
        return d
    d = luchii_signal(bot, asset)
    if d.get("action") == "BUY":
        if "regime" not in d:
            i, _ = snapshot(asset)
            d["regime"] = i.get("regime", "CHOP")
        if d["regime"] in strategy.NO_TRADE_REGIMES:
            return {**d, "action": "HOLD", "reason": f"[{asset}] {d['regime']} regime · no new trades"}
        why = entry_guard(bot, asset, d)
        if why:
            return {**d, "action": "HOLD", "reason": f"[{asset}] entry filtered · {why}"}
    return d


def mkt(asset: str) -> dict:
    m = (SNAPSHOT.get("markets") or {}).get(asset)
    if m:
        return m
    return {"price": SNAPSHOT.get("price"), "candles": SNAPSHOT.get("candles"), "rsi": SNAPSHOT.get("rsi")} if asset == "BTC" else {}


def equity(bot: dict) -> float:
    if bot["mode"] == "live":
        return bot.get("live_equity") or 0.0
    alts = sum(pos_of(bot, a)["position_qty"] * (mkt(a).get("price") or 0) for a in (bot.get("alt_positions") or {}))
    return round(bot["cash"] + bot["position_qty"] * (SNAPSHOT.get("price") or 0) + alts, 2)


async def log(db_, uid, d, price, fill=None, asset: str = "BTC"):
    c = mkt(asset).get("candles") or []
    pct = round((c[-1]["c"] - c[-2]["c"]) / c[-2]["c"] * 100, 3) if len(c) > 1 else 0.0
    await db_.bot_logs.insert_one({"user_id": uid, "ts": now_ms(), "action": d["action"], "price": float(price or 0),
                                   "pct": pct, "engine": d.get("engine", "algo"), "confidence": float(d.get("confidence", 0.5)),
                                   "reason": d.get("reason", ""), "fill": fill})


def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def signal(bot: dict, asset: str = "BTC") -> dict:
    m = mkt(asset)
    candles = m.get("candles") or []
    closes = [c["c"] for c in candles]
    price = m.get("price") or (closes[-1] if closes else 0)
    agg = bot.get("aggressive", True)
    nf, ns, rsi_cap = (5, 13, 80) if agg else (7, 25, 70)
    s7, s25, r = _sma(closes, nf), _sma(closes, ns), m.get("rsi")
    if s7 is None or s25 is None or r is None or len(candles) < 30:
        return {"action": "HOLD", "confidence": 0.3, "reason": f"[{asset}] warming up market data"}
    trend = (s7 - s25) / s25 * 100
    conf = min(0.95, 0.5 + abs(trend) * 5)
    pos = pos_of(bot, asset)
    flat = pos["position_qty"] <= 0
    tag = f"SMA{nf}/{ns}"
    tp, sl = net_tp_pct(bot), float(bot.get("stop_loss_pct") or 0)
    tp_txt = f"+{bot.get('take_profit_pct')}% +{fee_round_trip_pct(bot):.2f}% fees = +{tp}% net"
    if not flat and pos["entry_price"]:
        move = (price - pos["entry_price"]) / pos["entry_price"] * 100
        dip_stop = float(pos.get("stop_price") or 0)
        if dip_stop and price <= dip_stop:
            return {"action": "SELL", "confidence": 0.95, "reason": f"[{asset}] DIP STOP hit {move:+.2f}% (tight stop ${dip_stop:,.2f}) · RSI {r}"}
        if tp and move >= tp - 1e-9 and not pos["tp_taken"]:
            if bot.get("partial_tp", True):
                return {"action": "SELL", "partial": True, "confidence": 0.95, "reason": f"[{asset}] PARTIAL TAKE PROFIT {move:+.2f}% (target {tp_txt}) · selling half, rest rides trailing stop · RSI {r}"}
            return {"action": "SELL", "confidence": 0.95, "reason": f"[{asset}] TAKE PROFIT hit {move:+.2f}% (target {tp_txt}) · RSI {r}"}
        peak = max(float(pos["peak_price"] or 0), pos["entry_price"])
        fee_rt = fee_round_trip_pct(bot)
        peak_move = (peak - pos["entry_price"]) / pos["entry_price"] * 100
        if sl and bot.get("trailing_stop", True) and peak_move >= fee_rt + 0.1:
            floor = pos["entry_price"] * (1 + fee_rt / 100)
            if price <= floor:
                return {"action": "SELL", "confidence": 0.95, "reason": f"[{asset}] BREAK-EVEN+FEES lock hit (peak {peak_move:+.2f}% → exit {move:+.2f}% gross ≈ 0 net) · never give a winner back · RSI {r}"}
        if sl and bot.get("trailing_stop", True) and peak > pos["entry_price"]:
            drop = (price - peak) / peak * 100
            if drop <= -sl:
                return {"action": "SELL", "confidence": 0.95, "reason": f"[{asset}] TRAILING STOP hit {drop:+.2f}% from peak ${peak:,.2f} (locked {move:+.2f}%) · RSI {r}"}
        elif sl and move <= -sl:
            return {"action": "SELL", "confidence": 0.95, "reason": f"[{asset}] STOP LOSS hit {move:+.2f}% (limit -{sl}%) · RSI {r}"}
    if flat and agg and bot.get("dip_entries", True) and r < 25 and len(closes) >= 3 and closes[-1] > closes[-2] <= closes[-3]:
        last_stop = (bot.get("last_stop") or {}).get(asset) or {}
        if not (last_stop and time.time() - last_stop.get("ts", 0) < float(bot.get("reentry_cooldown_min", 15)) * 60):
            tight = float(bot.get("dip_stop_pct") or 0.25)
            return {"action": "BUY", "confidence": 0.8, "dip": True, "stop": price * (1 - tight / 100),
                    "reason": f"[{asset}] OVERSOLD DIP · RSI {r} < 25 · first green candle after the sell-off · tight stop -{tight}% · AGGRESSIVE"}
    if flat and agg and len(candles) >= 20:
        a = strategy.atr(candles[-20:])
        atr_pct = (a / price * 100) if a and price else 0
        reach = atr_pct * 6
        if tp and reach < tp:
            return {"action": "HOLD", "confidence": conf, "reason": f"[{asset}] market too quiet · 1m ATR {atr_pct:.3f}% → reachable ≈{reach:.2f}% < target +{tp}% net · fees would eat the trade · RSI {r}"}
    if flat and s7 > s25 and r < rsi_cap:
        last_stop = (bot.get("last_stop") or {}).get(asset) or {}
        cooldown = float(bot.get("reentry_cooldown_min", 15))
        if last_stop and time.time() - last_stop.get("ts", 0) < cooldown * 60 and price <= last_stop.get("price", 0) * 1.003:
            left = round(cooldown - (time.time() - last_stop["ts"]) / 60)
            return {"action": "HOLD", "confidence": conf, "reason": f"[{asset}] re-entry cooldown after stop-out · {left} min left unless price clears ${last_stop['price'] * 1.003:,.2f} · RSI {r}"}
        if bot.get("trend_filter", True):
            s25_prev = _sma(closes[:-5], ns)
            if price < s25 or (not agg and s25_prev and s25 <= s25_prev):
                return {"action": "HOLD", "confidence": conf, "reason": f"[{asset}] trend filter · price below/flat SMA{ns} · trend {trend:+.3f}% · RSI {r}"}
        if bot.get("volume_filter", True) and not agg:
            vols = [c["v"] for c in candles[-21:-1]]
            avg_v = sum(vols) / len(vols) if vols else 0
            if avg_v and candles[-2]["v"] < avg_v:
                return {"action": "HOLD", "confidence": conf, "reason": f"[{asset}] volume filter · last vol {candles[-2]['v']:.2f} < avg {avg_v:.2f} · RSI {r}"}
        return {"action": "BUY", "confidence": conf, "reason": f"[{asset}] {tag} cross up ({trend:+.3f}%) · RSI {r}{' · AGGRESSIVE' if agg else ' · trend+volume confirmed'}"}
    if not flat and pos["entry_price"]:
        move = (price - pos["entry_price"]) / pos["entry_price"] * 100
        if s7 < s25 and move < -0.3:
            return {"action": "SELL", "confidence": conf, "reason": f"[{asset}] {tag} cross down while losing {move:+.2f}% · cutting early · RSI {r}"}
        if s7 < s25 or r > 75:
            return {"action": "HOLD", "confidence": conf, "reason": f"[{asset}] in position {move:+.2f}% · winner rides TP/trailing stop · RSI {r}"}
    return {"action": "HOLD", "confidence": conf, "reason": f"[{asset}] {'in position' if not flat else 'no entry'} · trend {trend:+.3f}% · RSI {r}"}


def order_usd(bot: dict, have: float = None) -> float:
    usd = float(bot["position_usd"])
    pct = float(bot.get("size_pct") or 0)
    if pct > 0 and have is not None:
        usd = round(have * pct / 100, 2)
    if bot.get("max_order_usd"):
        usd = min(usd, float(bot["max_order_usd"]))
    return usd


async def set_bot(uid: str, **fields):
    await db.bots.update_one({"user_id": uid}, {"$set": fields})


async def record_fill(bot: dict, side: str, price: float, qty: float, fill: str, d: dict, asset: str = "BTC"):
    uid = bot["user_id"]
    pos = pos_of(bot, asset)
    pnl = 0.0
    upd = {"trades_count": bot["trades_count"] + 1}
    if side == "SELL" and "STOP" in (d.get("reason") or "").upper() and not (d.get("partial") or d.get("fraction")):
        upd[f"last_stop.{asset}"] = {"ts": time.time(), "price": price}
    if side == "BUY":
        upd.update(pos_fields(asset, qty=qty, entry=price, peak=price, tp_taken=False, stop=d.get("stop") or 0.0, tiers=0, atr=d.get("entry_atr") or 0.0))
        if bot["mode"] != "live":
            upd["cash"] = bot["cash"] - qty * price
    elif d.get("partial") or d.get("fraction"):
        pnl = net_pnl(bot, pos["entry_price"], price, qty) if bot["mode"] == "live" else round((price - pos["entry_price"]) * qty, 4)
        upd.update(pos_fields(asset, qty=round(max(pos["position_qty"] - qty, 0), 8), tp_taken=True,
                              tiers=d.get("tier", pos["tiers_done"]), stop=d.get("stop")), realized_pnl=round(bot["realized_pnl"] + pnl, 4))
        if bot["mode"] != "live":
            upd["cash"] = bot["cash"] + qty * price
    else:
        pnl = net_pnl(bot, pos["entry_price"], price, qty) if bot["mode"] == "live" else round((price - pos["entry_price"]) * qty, 4)
        upd.update(pos_fields(asset, qty=0.0, entry=0.0, peak=0.0, tp_taken=False, stop=0.0, tiers=0, atr=0.0), realized_pnl=round(bot["realized_pnl"] + pnl, 4),
                   wins=bot["wins"] + (pnl > 0), losses=bot["losses"] + (pnl <= 0))
        if bot["mode"] != "live":
            upd["cash"] = bot["cash"] + qty * price
    await set_bot(uid, **upd)
    await db.bot_trades.insert_one({"user_id": uid, "ts": now_ms(), "side": side, "asset": asset, "price": price, "qty": qty, "pnl": pnl,
                                    "strategy": bot.get("strategy", "luchii003")})
    if bot["mode"] == "live" and side == "SELL":
        try:
            rate = float(bot.get("fee_rate") or 0.001)
            await ledger.record_trade_pnl(uid, f"{uid}:{asset}:{now_ms()}", pnl, round((pos["entry_price"] + price) * qty * rate, 6), asset, side, qty, price)
        except Exception as e:  # noqa: BLE001
            log.warning("ledger posting skipped: %s", e)
    if side == "BUY":
        i, bi = snapshot(asset)
        await audit.open_trade(bot, asset, price, qty, d, i, bi)
    else:
        await audit.close_trade(uid, asset, price, qty, d, bool(d.get("partial") or d.get("fraction")))
    await log(db, uid, d, price, fill, asset)
    await alerts.notify(bot, f"{'🟢' if side == 'BUY' else '🔴'} {side} {qty} {asset} @ ${price:,.2f}" + (f" · P&L {pnl:+.2f}" if side == "SELL" else ""))
    bot = await db.bots.find_one({"user_id": uid})
    if not (d.get("partial") or d.get("fraction")) and asset == "BTC":
        asyncio.create_task(mirror_all(db, bot, side))
    await check_daily_loss(bot)


def sell_fraction(d: dict) -> float:
    return float(d.get("fraction") or (0.5 if d.get("partial") else 1.0))


def buy_usd(bot: dict, d: dict, cash: float, price: float) -> float:
    if d.get("risk") and d.get("stop"):
        return round(strategy.position_size(cash, d["risk"], price, d["stop"]) * price * 0.98, 2)
    return order_usd(bot, cash)


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
    cur, need = (symbol.split("/")[1], usd) if side == "BUY" else (symbol.split("/")[0], qty)
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


async def execute(bot: dict, d: dict, asset: str = "BTC"):
    side, price = d["action"], mkt(asset).get("price") or SNAPSHOT["price"]
    pos = pos_of(bot, asset)
    if bot["mode"] != "live":
        qty = round(buy_usd(bot, d, bot["cash"], price) / price, 6) if side == "BUY" else pos["position_qty"] * sell_fraction(d)
        return await record_fill(bot, side, price, qty, "PAPER FILLED", d, asset)
    uid = bot["user_id"]
    creds = await get_creds(uid)
    ex = client_from_doc(creds)
    try:
        symbol = await resolve_symbol(ex, creds["exchange"], asset)
        taker = (ex.market(symbol) or {}).get("taker")
        if taker and abs(float(taker) - float(bot.get("fee_rate") or 0)) > 1e-9:
            await set_bot(uid, fee_rate=float(taker))
            bot = {**bot, "fee_rate": float(taker)}
        quote = symbol.split("/")[1]
        if side == "BUY":
            if bot.get("auto_fund", True) and (float(bot.get("size_pct") or 0) > 0 or d.get("risk")):
                try:
                    bal = await all_balances(ex, creds["exchange"])
                    want = buy_usd(bot, d, sum(float(b.get(quote) or 0) for b in bal.values()), price)
                except Exception:
                    want = order_usd(bot)
            else:
                want = order_usd(bot)
            await auto_fund(ex, uid, creds["exchange"], symbol, side, want, 0, bot.get("auto_fund", True))
            have = await free_of(ex, quote)
            usd = buy_usd(bot, d, have, price)
        else:
            usd = 0
            await auto_fund(ex, uid, creds["exchange"], symbol, side, 0, pos["position_qty"], bot.get("auto_fund", True))
        price = (await ex.fetch_ticker(symbol))["last"] or price
        lim = ex.market(symbol).get("limits") or {}
        min_qty = float((lim.get("amount") or {}).get("min") or 0)
        min_cost = float((lim.get("cost") or {}).get("min") or 0)
        if side == "BUY":
            if have < usd or usd <= 0:
                return await wait_log(uid, bot, f"[{asset}] BUY signal skipped · need {usd} {quote} in trading, have {round(have, 4)}. Deposit {quote}/USDC.")
            if usd / price < min_qty or usd < min_cost:
                return await wait_log(uid, bot, f"[{asset}] BUY signal skipped · ${usd} is below the {symbol} minimum (≈ ${round(max(min_qty * price, min_cost), 2)}). Raise POSITION SIZE / % or deposit more.")
            o = await market_order(ex, symbol, "BUY", usd, 0, price, maker=bot.get("maker_entries", True))
            qty = float(o.get("filled") or 0) or usd / price
            fill_px = float(o.get("average") or price)
            if o.get("maker"):
                d = {**d, "reason": d["reason"] + " · MAKER entry (post-only limit, lower fee)"}
            await arm_stop(ex, creds["exchange"], uid, asset, symbol, qty, protective_stop(bot, asset, pos, fill_px, d), fill_px,
                           tp_price(bot, {"tp_taken": False}, fill_px), bot.get("partial_tp", True))
        else:
            await disarm_stop(ex, creds["exchange"], uid, bot, asset)
            bot = {**bot, "exchange_stops": {**(bot.get("exchange_stops") or {}), asset: None}}
            want = pos["position_qty"] * sell_fraction(d)
            qty = min(want, await free_of(ex, asset))
            if d.get("fraction") and (qty < min_qty or qty * price < min_cost):
                need = max(min_qty, (min_cost / price) if min_cost else 0)
                if need <= pos["position_qty"] * 0.6:
                    qty = min(need * 1.05, await free_of(ex, asset))
                    d = {**d, "reason": d["reason"] + " · (slice rounded up to exchange minimum)"}
                else:
                    await set_bot(uid, **pos_fields(asset, tiers=d.get("tier", pos["tiers_done"]), stop=d.get("stop")))
                    return await wait_log(uid, bot, f"{d['reason']} · slice below exchange minimum, keeping full position · stop ${d.get('stop', 0):,.2f}")
            elif d.get("partial") and (qty < min_qty or qty * price < min_cost):
                d = {**d, "partial": False, "reason": d["reason"] + " · (half below exchange minimum, selling all)"}
                qty = min(pos["position_qty"], await free_of(ex, asset))
            if qty < max(min_qty, 1e-8):
                return await wait_log(uid, bot, f"[{asset}] SELL signal skipped · only {qty} {asset} free in trading (minimum {min_qty}).")
            o = await market_order(ex, symbol, "SELL", 0, qty, price)
            qty = float(o.get("filled") or 0) or qty
            left = pos["position_qty"] - qty
            if (d.get("partial") or d.get("fraction")) and left * price >= max(min_cost, min_qty * price, 1):
                await arm_stop(ex, creds["exchange"], uid, asset, symbol, left, protective_stop(bot, asset, pos, pos["entry_price"], d), price)
        await record_fill(bot, side, float(o.get("average") or price), round(qty, 8), f"LIVE FILLED #{o.get('id')}", d, asset)
    except Exception as e:
        await log(db, uid, {**d, "reason": f"{d['reason']} · order error: {friendly(e)[:160]}"}, price, "REJECTED", asset)
    finally:
        await ex.close()


async def sync_position(bot: dict, bal: dict, ex=None, exchange: str = None):
    uid = bot["user_id"]
    for asset in bot.get("assets") or ["BTC"]:
        price = mkt(asset).get("price") or 0
        held = sum(float(b.get(asset) or 0) for b in bal.values())
        pos = pos_of(bot, asset)
        dust = 1e-5 if asset == "BTC" else 1e-3
        if pos["position_qty"] <= 0 and held >= dust and held * price >= 1:
            await set_bot(uid, **pos_fields(asset, qty=round(held, 8), entry=price, peak=price, tp_taken=False))
            await log(db, uid, {"action": "HOLD", "engine": "sync", "confidence": 1.0,
                                "reason": f"[{asset}] Synced position: {round(held, 8)} {asset} on exchange · entry set to ${price:,.2f} (P&L counted from now)"}, price, "SYNCED")
        elif pos["position_qty"] > 0 and held < pos["position_qty"] * 0.98:
            st = (bot.get("exchange_stops") or {}).get(asset)
            if st and ex is not None:
                legs, left = st.get("legs") or [st], []
                for leg in legs:
                    status = await stop_status(ex, exchange, st["symbol"], leg["id"])
                    if status["state"] != "filled":
                        left.append(leg)
                        continue
                    px = float(status["price"] or leg.get("tp") or leg["price"])
                    is_tp = bool(leg.get("tp")) and px >= leg["tp"] * 0.999
                    partial = held >= dust
                    kind = "TAKE PROFIT limit" if is_tp else "STOP"
                    fresh = await db.bots.find_one({"user_id": uid})
                    await set_bot(uid, **{f"exchange_stops.{asset}": None})
                    await record_fill(fresh, "SELL", px, min(float(leg["qty"]), pos_of(fresh, asset)["position_qty"]), f"EXCHANGE {kind.split()[0]} #{leg['id']}",
                                      {"action": "SELL", "confidence": 1.0, "partial": partial,
                                       "reason": f"[{asset}] EXCHANGE-SIDE {kind} filled on OKX @ ${px:,.2f} (server-independent protection)"}, asset)
                if len(left) != len(legs):
                    if left and held >= dust:
                        await set_bot(uid, **{f"exchange_stops.{asset}": {**st, "legs": left, "qty": round(sum(l["qty"] for l in left), 8), "id": left[0]["id"]}})
                    continue
            gone = held < dust
            if gone or held < pos["position_qty"] * 0.5:
                await set_bot(uid, **pos_fields(asset, qty=0.0 if gone else round(held, 8), entry=0.0 if gone else pos["entry_price"]))


def protective_stop(bot: dict, asset: str, pos: dict, entry: float, d: dict = None) -> float:
    """Price for the exchange-side stop: strategy stop (ATR/tiers) or hard SL%, raised by the trailing floor once in profit."""
    sl = float(bot.get("stop_loss_pct") or 0)
    cands = [float((d or {}).get("stop") or 0), pos.get("stop_price") or 0]
    if sl:
        cands.append(entry * (1 - sl / 100))
        peak = max(float(pos.get("peak_price") or 0), entry)
        if bot.get("trailing_stop", True) and peak > entry:
            cands.append(peak * (1 - sl / 100))
            fee_rt = fee_round_trip_pct(bot)
            if (peak - entry) / entry * 100 >= fee_rt + 0.1:
                cands.append(entry * (1 + fee_rt / 100))
    return max(cands)


def tp_price(bot: dict, pos: dict, entry: float) -> float:
    tp = net_tp_pct(bot)
    return entry * (1 + tp / 100) if tp and entry and not pos.get("tp_taken") else 0.0


async def arm_stop(ex, exchange: str, uid: str, asset: str, symbol: str, qty: float, stop_px: float, price: float, tp_px: float = 0.0, partial: bool = True):
    """Arm exchange-side protection: OCO (limit TP + stop) on the TP slice, plain stop on the rest."""
    if not stop_px or stop_px >= price or qty <= 0:
        return
    try:
        free = await free_of(ex, asset)
        qty = min(qty, free) if free > 0 else qty
        step = float(ex.amount_to_precision(symbol, qty))
        qty = step if step <= free or free <= 0 else float(ex.amount_to_precision(symbol, free * 0.999))
        min_qty = float(((ex.market(symbol).get("limits") or {}).get("amount") or {}).get("min") or 0)
        legs = []
        if tp_px and tp_px > price:
            half = float(ex.amount_to_precision(symbol, qty / 2))
            rest = float(ex.amount_to_precision(symbol, qty - half))
            if partial and half >= max(min_qty, 1e-8) and rest >= max(min_qty, 1e-8):
                legs.append(await place_oco(ex, exchange, symbol, half, stop_px, tp_px))
                legs.append({**await place_stop(ex, exchange, symbol, rest, stop_px), "kind": "sl"})
            else:
                legs.append(await place_oco(ex, exchange, symbol, qty, stop_px, tp_px))
        else:
            legs.append({**await place_stop(ex, exchange, symbol, qty, stop_px), "kind": "sl"})
        st = {"id": legs[0]["id"], "price": legs[0]["price"], "tp": tp_px if tp_px > price else 0.0, "qty": round(sum(l["qty"] for l in legs), 8),
              "legs": legs, "symbol": symbol, "ts": now_ms()}
        await set_bot(uid, **{f"exchange_stops.{asset}": st, "stop_retry_ts": 0})
        tp_txt = f" · TP limit @ ${st['tp']:,.2f} on {legs[0]['qty']} {asset}" if st["tp"] else ""
        await log(db, uid, {"action": "HOLD", "engine": "guard", "confidence": 1.0,
                            "reason": f"[{asset}] exchange-side protection armed on {exchange}: STOP @ ${st['price']:,.2f} for {st['qty']} {asset}{tp_txt} · fires even if this server is offline"},
                  price, f"STOP #{st['id']}", asset)
    except Exception as e:
        await set_bot(uid, stop_retry_ts=time.time())
        await log(db, uid, {"action": "HOLD", "engine": "guard", "confidence": 1.0,
                            "reason": f"[{asset}] could not arm exchange-side protection: {friendly(e)[:140]} · engine stop/TP still active · retry in 10 min"}, price, "REJECTED", asset)


async def disarm_stop(ex, exchange: str, uid: str, bot: dict, asset: str):
    st = (bot.get("exchange_stops") or {}).get(asset)
    if not st:
        return
    for leg in st.get("legs") or [st]:
        try:
            await cancel_stop(ex, exchange, st["symbol"], leg["id"])
        except Exception as e:
            log_.warning("cancel stop failed: %s", e)
    await set_bot(uid, **{f"exchange_stops.{asset}": None})


async def sync_stops(ex, exchange: str, bot: dict):
    """Keep the exchange-side stop in step with the engine's trailing/tier stop (replace when it moves up > 0.1%)."""
    uid = bot["user_id"]
    if time.time() - bot.get("stop_retry_ts", 0) < 600:
        return
    for asset in bot.get("assets") or ["BTC"]:
        pos = pos_of(bot, asset)
        st = (bot.get("exchange_stops") or {}).get(asset)
        price = mkt(asset).get("price") or 0
        if pos["position_qty"] <= 0 or not price:
            continue
        want = protective_stop(bot, asset, pos, pos["entry_price"])
        want_tp = tp_price(bot, pos, pos["entry_price"])
        if not want or want >= price:
            continue
        if st and want <= st["price"] * 1.001 and abs(st["qty"] - pos["position_qty"]) / pos["position_qty"] < 0.02 \
                and abs((st.get("tp") or 0) - (want_tp if want_tp > price else 0)) <= max(price * 0.0005, 0.01):
            continue
        symbol = st["symbol"] if st else await resolve_symbol(ex, exchange, asset)
        await disarm_stop(ex, exchange, uid, bot, asset)
        await arm_stop(ex, exchange, uid, asset, symbol, pos["position_qty"], want, price, want_tp, bot.get("partial_tp", True))


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
            await sync_position(bot, bal, ex, creds["exchange"])
            fresh = await db.bots.find_one({"user_id": bot["user_id"]})
            await sync_stops(ex, creds["exchange"], fresh)
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
    if time.time() - bot.get("weights_ts", 0) > 600:
        import agents
        AGENT_WEIGHTS[uid] = agents.weights_from_audit(await audit.closed(uid))
        await set_bot(uid, weights_ts=time.time(), agent_weights=AGENT_WEIGHTS[uid])
    if bot.get("pending"):
        return
    for asset in bot.get("assets") or ["BTC"]:
        price = mkt(asset).get("price")
        if not price:
            continue
        pos = pos_of(bot, asset)
        if pos["position_qty"] > 0:
            await audit.track(uid, asset, price)
        if pos["position_qty"] > 0 and price > float(pos["peak_price"] or 0):
            await set_bot(uid, **pos_fields(asset, peak=price))
            bot = await db.bots.find_one({"user_id": uid})
        d = decide(bot, asset)
        d["engine"] = "algo"
        if d["action"] == "HOLD" and d.get("stop") and pos["position_qty"] > 0 and abs((pos["stop_price"] or 0) - d["stop"]) > 1e-9:
            await set_bot(uid, **pos_fields(asset, stop=d["stop"]))
        key = f"last_hold_log_{asset}"
        if d["action"] == "HOLD":
            if time.time() - bot.get(key, 0) >= 60:
                await set_bot(uid, **{key: time.time()})
                await log(db, uid, d, price, asset=asset)
        elif d["action"] == "BUY" and (blocked := await entry_blocked(uid, bot, asset, d))[0]:
            await wait_log(uid, bot, blocked[0])
        elif bot["mode"] == "live" and bot.get("manual_confirm"):
            if d["action"] == "BUY":
                d = apply_risk_multiplier(d, (await entry_blocked(uid, bot, asset, d, gate_only=True))[1])
            cash = bot.get("live_equity") or 0
            size = buy_usd(bot, d, cash, price) if d["action"] == "BUY" and cash else order_usd(bot)
            await set_bot(uid, pending={"action": d["action"], "asset": asset, "price": price, "size_usd": size,
                                        "reason": d["reason"], "decision": d, "ts": now_ms()})
            await alerts.notify(bot, f"⏳ Confirm {d['action']} {asset} @ ${price:,.2f} in the terminal")
            break
        else:
            if d["action"] == "BUY":
                d = apply_risk_multiplier(d, (await entry_blocked(uid, bot, asset, d, gate_only=True))[1])
            await execute(bot, d, asset)
            bot = await db.bots.find_one({"user_id": uid})
    bot = await db.bots.find_one({"user_id": uid})
    if equity(bot) > 0:
        await db.bot_pnl.insert_one({"user_id": uid, "ts": now_ms(), "equity": equity(bot)})


def apply_risk_multiplier(d: dict, g: dict) -> dict:
    if d.get("risk") and g:
        return {**d, "risk": d["risk"] * risk_multiplier(g.get("profit_factor"), g.get("pf_inf"))}
    return d


async def entry_blocked(uid: str, bot: dict, asset: str, d: dict, gate_only: bool = False):
    """Returns (reason, gate_doc). reason is None when the entry is allowed."""
    import gates
    ok, g = await gates.allows_entry(uid, bot.get("strategy", "luchii003"))
    if gate_only:
        return None, g
    if not ok:
        return f"{d['reason']} · ENTRY BLOCKED · strategy gate {g['state']} ({g['reason']}) · exits still managed", g
    ks = await gates.kill_switch(uid)
    if ks:
        if time.time() - bot.get("ks_alert_ts", 0) > 4 * 3600:
            await set_bot(uid, ks_alert_ts=time.time())
            await alerts.notify(bot, f"🛑 LUCHII KILL SWITCH\n\nReason: {ks}\nNew entries paused · exits still managed")
        return f"{d['reason']} · ENTRY BLOCKED · kill switch: {ks} · exits still managed", g
    sym = await gates.symbol_state(uid, asset)
    if sym.get("disabled"):
        return f"{d['reason']} · {asset} DISABLED ({sym.get('by')}: {sym.get('reason')}) · owner can re-enable in Strategy Health", g
    return None, g


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
    last = time.time()
    while True:
        await asyncio.sleep(every)
        gap = time.time() - last
        if gap > every * 4:
            log_.error("TICK WATCHDOG: trading loop starved for %.0fs (expected %.0fs)", gap, every)
            await db.audit_log.insert_one({"event": "tick_starved", "seconds": round(gap), "ts": now_ms()})
        last = time.time()
        await autopilot_engage()
        async for bot in db.bots.find({"status": "running"}):
            try:
                await tick_bot(bot)
            except Exception as e:
                log_.exception("tick failed for %s: %s", bot.get("user_id"), e)


async def autopilot_engage():
    """Autopilot: any live bot with an exchange key, funds on the account and no pause/kill goes to running by itself."""
    async for bot in db.bots.find({"status": {"$ne": "running"}, "mode": "live", "autopilot": {"$ne": False}, "kill_switch": {"$ne": True}}):
        uid = bot["user_id"]
        if not await get_creds(uid):
            continue
        try:
            await refresh_live_equity({"user_id": uid})
            fresh = await db.bots.find_one({"user_id": uid})
            eq = float(fresh.get("live_equity") or 0)
        except Exception as e:  # noqa: BLE001
            log_.warning("autopilot equity check failed for %s: %s", uid, e)
            continue
        if eq < 5:
            continue
        await set_bot(uid, status="running")
        await log(db, uid, {"action": "HOLD", "engine": "system", "confidence": 1.0,
                            "reason": f"AUTOPILOT engaged · ${eq:,.2f} detected on exchange · AI trades automatically · only withdrawals need you"}, SNAPSHOT.get("price"))
        await alerts.notify(fresh, f"🤖 Autopilot engaged · ${eq:,.2f} available")

