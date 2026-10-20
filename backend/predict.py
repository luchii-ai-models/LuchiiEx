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
    await ensure_funds(uid, amt)
    je = await ledger.post_journal([{"account": ledger.user_code(uid, "USD"), "debit": amt}, {"account": "PREDICT_ESCROW_USD", "credit": amt}],
                                   source="PREDICT", description=f"Predict {asset} {direction} {duration}s stake ${amt}", reference_type="PREDICT_STAKE",
                                   reference_id=cid, idempotency_key=f"predict:stake:{cid}", actor_id=uid)
    doc = {"id": cid, "user_id": uid, "asset": asset, "direction": direction, "stake": float(amt), "duration": duration, "multiplier": mult,
           "potentialPayout": float(payout), "aiConfidence": round(conf, 3), "entryPrice": px, "openedAt": now_ms(), "expiresAt": now_ms() + duration * 1000,
           "status": "OPEN", "stakeJournalId": je["id"]}
    await db.predict_contracts.insert_one({**doc})
    return doc


async def win_streak(uid: str) -> int:
    """Consecutive AI-predict wins (most recent first), stops at the first loss. PUSH is skipped."""
    rows = await db.predict_contracts.find(
        {"user_id": uid, "auto": True, "status": {"$in": ["WON", "LOST"]}},
        {"_id": 0, "status": 1, "settledAt": 1}).sort("settledAt", -1).to_list(100)
    streak = 0
    for r in rows:
        if r.get("status") == "WON":
            streak += 1
        else:
            break
    return streak


async def _notify(uid: str, text: str):
    """Best-effort Telegram ping; honours the user's alerts toggle + linked chat."""
    try:
        import alerts
        bot = await db.bots.find_one({"user_id": uid}, {"_id": 0, "alerts_enabled": 1, "telegram_chat_id": 1})
        await alerts.notify(bot, text)
    except Exception:  # noqa: BLE001
        pass


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
    if c.get("auto") and result in ("WON", "LOST"):
        if result == "WON":
            streak = await win_streak(c["user_id"])
            net = payout - stake
            fire = "🔥" * min(max(streak, 1), 6)
            await _notify(c["user_id"], f"✅ AI PREDICT WON — {c['asset']} {c['direction']} · back ${float(payout):.2f} (net +${float(net):.2f})\n{fire} {streak} win streak")
        else:
            await _notify(c["user_id"], f"❌ AI PREDICT lost — {c['asset']} {c['direction']} · -${float(stake):.2f} · streak reset")
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


# ---------- AI auto-predict: uses every dollar already in the system (ledger USD + exchange equity), 3s contracts ----------
AUTO_CONF, AUTO_DURATION = 0.80, int(os.environ.get("PREDICT_AUTO_DURATION", "3"))
AUTO_STAKE_PCT, AUTO_MAX_STAKE, DAILY_LOSS_PCT = Decimal(os.environ.get("PREDICT_AUTO_STAKE_PCT", "0.10")), MAX_STAKE, Decimal(os.environ.get("PREDICT_DAILY_LOSS_PCT", "0.25"))
MIRROR_ACCOUNT = ("EXCHANGE_MIRROR_USD", "User exchange equity mirrored for Predict", "ASSET", "USD")


async def mirrored(uid: str) -> Decimal:
    rows = await db.journal_entries.find({"reference_type": "EXCHANGE_MIRROR", "actor_id": uid}, {"_id": 0, "amount": 1}).to_list(5000)
    return sum((Decimal(str(r.get("amount") or 0)) for r in rows), Decimal(0))


async def ensure_funds(uid: str, need: Decimal) -> Decimal:
    """Top the USD ledger wallet up from the user's exchange equity (OKX balance already in the system). Returns available after."""
    wallets = {w["currency"]: w for w in await ledger.user_wallets(uid)}
    avail = Decimal(str((wallets.get("USD") or {}).get("available", 0)))
    if avail >= need:
        return avail
    bot = await db.bots.find_one({"user_id": uid}, {"_id": 0, "live_equity": 1})
    room = Decimal(str((bot or {}).get("live_equity") or 0)).quantize(Decimal("0.01")) - await mirrored(uid)
    top = min(room, (need - avail).quantize(Decimal("0.01")))
    if top <= 0:
        return avail
    await db.ledger_accounts.update_one({"code": MIRROR_ACCOUNT[0]}, {"$setOnInsert": {"id": str(uuid.uuid4()), "code": MIRROR_ACCOUNT[0], "name": MIRROR_ACCOUNT[1], "account_type": "ASSET",
                                                                                      "currency": "USD", "user_id": None, "created_at": now_ms()}}, upsert=True)
    ref = str(uuid.uuid4())
    je = await ledger.post_journal([{"account": MIRROR_ACCOUNT[0], "debit": top}, {"account": ledger.user_code(uid, "USD"), "credit": top}], source="SYSTEM",
                                   description=f"Mirror ${top} exchange equity into Predict wallet", reference_type="EXCHANGE_MIRROR", reference_id=ref, idempotency_key=f"mirror:{ref}", actor_id=uid)
    await db.journal_entries.update_one({"id": je["id"]}, {"$set": {"amount": str(top)}})
    return avail + top


async def reload_buying_power(uid: str) -> dict:
    """Operator action (house absorbs prior Predict losses): top the Predict wallet up to the user's
    full current exchange equity so AVAILABLE TO PREDICT == exchange balance again."""
    await ensure_accounts()
    try:
        import engine
        await engine.refresh_live_equity({"user_id": uid})
    except Exception:  # noqa: BLE001
        pass
    bot = await db.bots.find_one({"user_id": uid}, {"_id": 0, "live_equity": 1})
    equity = Decimal(str((bot or {}).get("live_equity") or 0)).quantize(Decimal("0.01"))
    wallets = {w["currency"]: w for w in await ledger.user_wallets(uid)}
    cash = Decimal(str((wallets.get("USD") or {}).get("available", 0))).quantize(Decimal("0.01"))
    top = (equity - cash).quantize(Decimal("0.01"))
    if top <= 0:
        return {"ok": True, "reloaded": 0.0, "available": float(max(cash, equity))}
    await db.ledger_accounts.update_one({"code": MIRROR_ACCOUNT[0]}, {"$setOnInsert": {"id": str(uuid.uuid4()), "code": MIRROR_ACCOUNT[0], "name": MIRROR_ACCOUNT[1],
                                                                                      "account_type": "ASSET", "currency": "USD", "user_id": None, "created_at": now_ms()}}, upsert=True)
    ref = str(uuid.uuid4())
    je = await ledger.post_journal([{"account": MIRROR_ACCOUNT[0], "debit": top}, {"account": ledger.user_code(uid, "USD"), "credit": top}], source="SYSTEM",
                                   description=f"Reload Predict buying power to exchange equity (house absorbs ${top} prior losses)",
                                   reference_type="EXCHANGE_MIRROR", reference_id=ref, idempotency_key=f"reload:{ref}", actor_id=uid)
    await db.journal_entries.update_one({"id": je["id"]}, {"$set": {"amount": str(top)}})
    return {"ok": True, "reloaded": float(top), "available": float(equity)}


async def total_funds(uid: str) -> Decimal:
    wallets = {w["currency"]: w for w in await ledger.user_wallets(uid)}
    avail = Decimal(str((wallets.get("USD") or {}).get("available", 0)))
    bot = await db.bots.find_one({"user_id": uid}, {"_id": 0, "live_equity": 1})
    return avail + max(Decimal(0), Decimal(str((bot or {}).get("live_equity") or 0)) - await mirrored(uid))


async def funds_detail(uid: str) -> dict:
    """Every dollar in the system the AI can stake for this user: ledger USD + free exchange equity."""
    wallets = {w["currency"]: w for w in await ledger.user_wallets(uid)}
    ledger_usd = Decimal(str((wallets.get("USD") or {}).get("available", 0)))
    bot = await db.bots.find_one({"user_id": uid}, {"_id": 0, "live_equity": 1})
    equity = Decimal(str((bot or {}).get("live_equity") or 0))
    mir = await mirrored(uid)
    exchange_free = max(Decimal(0), equity - mir)
    return {"ledgerUsd": float(ledger_usd), "exchangeEquity": float(equity), "mirrored": float(mir),
            "exchangeFree": float(exchange_free), "total": float(ledger_usd + exchange_free)}


async def sync_available(uid: str) -> Decimal:
    """Refresh the user's live exchange equity on demand (throttled ~8s) so funds reflect the whole system, then return total available."""
    import time as _t
    bot = await db.bots.find_one({"user_id": uid}, {"_id": 0, "live_equity_ts": 1})
    if bot is not None and _t.time() - (bot.get("live_equity_ts") or 0) > 8:
        try:
            import engine
            await engine.refresh_live_equity({"user_id": uid})
            await db.bots.update_one({"user_id": uid}, {"$set": {"live_equity_ts": _t.time()}})
        except Exception:  # noqa: BLE001
            pass
    return await total_funds(uid)


def ai_direction(asset: str) -> tuple[str | None, float]:
    """UP when the long-side conviction model is ≥ 0.80; DOWN on the mirrored bearish setup; else no contract."""
    from engine import snapshot
    import strategy
    i, bi = snapshot(asset)
    if not i or not bi or strategy.nan(i.get("ema9"), i.get("ema21"), i.get("rsi"), i.get("adx"), i.get("volume_ratio")):
        return None, 0.0
    conf = strategy.confidence(i, bi)
    if conf >= AUTO_CONF and i["regime"] != "CHOP":
        return "UP", conf
    bear = (0.30 if i["ema9"] < i["ema21"] else 0) + (0.20 if i["rsi"] < 40 else 0) + (0.20 if i["volume_ratio"] > 1.5 else 0) \
        + (0.15 if bi.get("price", 0) < (bi.get("ema200") or 0) else 0) + (0.15 if i["adx"] > 30 else 0)
    if bear >= AUTO_CONF and i["regime"] != "CHOP":
        return "DOWN", round(bear, 2)
    return None, max(conf, bear)


async def _daily_net(uid: str) -> Decimal:
    since = now_ms() - 86_400_000
    rows = await db.predict_contracts.find({"user_id": uid, "settledAt": {"$gte": since}}, {"_id": 0, "stake": 1, "payout": 1}).to_list(2000)
    return sum((Decimal(str(r.get("payout") or 0)) - Decimal(str(r["stake"])) for r in rows), Decimal(0))


async def auto_tick():
    if not enabled():
        return
    async for bot in db.bots.find({"status": {"$in": ["running", "stopped"]}, "auto_predict": {"$ne": False}}, {"_id": 0, "user_id": 1, "kill_switch": 1}):
        uid = bot["user_id"]
        if bot.get("kill_switch"):
            continue
        avail = await sync_available(uid)
        if avail < MIN_STAKE:
            continue
        if await _daily_net(uid) <= -max(Decimal("2"), avail * DAILY_LOSS_PCT):
            continue
        for asset in ASSETS:
            if await db.predict_contracts.find_one({"user_id": uid, "asset": asset, "status": "OPEN"}, {"_id": 1}):
                continue
            direction, conf = ai_direction(asset)
            if not direction:
                continue
            stake = max(MIN_STAKE, min(AUTO_MAX_STAKE, (avail * AUTO_STAKE_PCT).quantize(Decimal("0.01")), avail))
            try:
                await ensure_funds(uid, stake)
                c = await place(uid, asset, direction, stake, AUTO_DURATION)
                await db.predict_contracts.update_one({"id": c["id"]}, {"$set": {"auto": True, "aiConfidence": round(conf, 3)}})
                from engine import log as elog
                await elog(db, uid, {"action": "HOLD", "engine": "predict", "confidence": conf,
                                     "reason": f"[{asset}] AI PREDICT {direction} · ${stake} for {AUTO_DURATION}s @ {c['multiplier']}x · conviction {conf * 100:.0f}%"}, c["entryPrice"], asset=asset)
                await _notify(uid, f"🤖 AI PREDICT opened — {asset} {direction} · ${stake} for {AUTO_DURATION}s @ {c['multiplier']}x · conviction {conf * 100:.0f}%")
            except ledger.LedgerError:
                continue


async def auto_worker():
    while True:
        try:
            await auto_tick()
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("predict").warning("auto loop: %s", e)
        await asyncio.sleep(15)
