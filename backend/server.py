from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

import asyncio  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Literal, Optional  # noqa: E402

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402

import alerts  # noqa: E402
import brokers  # noqa: E402
import engine  # noqa: E402
import ledger  # noqa: E402
import ledger_routes  # noqa: E402
import market  # noqa: E402
import pocket  # noqa: E402
import solana_dex  # noqa: E402
import webhooks  # noqa: E402
from auth import (check_lockout, clear_fails, create_token, current_user, hash_password, public_user,  # noqa: E402
                  record_fail, seed_admin, verify_password)
from db import db, now_ms  # noqa: E402
from engine import DEFAULT_BOT, get_creds, set_bot, with_exchange  # noqa: E402
from exchange_service import (ACCOUNTS, EXCHANGES, all_balances, encrypt, friendly, market_order,  # noqa: E402
                              nonzero_balances, permissions, resolve_symbol, total_usd, verify)
import web3bal  # noqa: E402
from market import SNAPSHOT  # noqa: E402

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
app = FastAPI()
api = APIRouter(prefix="/api")


def bad(msg: str, code: int = 400):
    raise HTTPException(code, msg)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- auth ----------
class LoginIn(BaseModel):
    email: str
    password: str


class RegisterIn(LoginIn):
    name: str = ""


@api.post("/auth/register")
async def register(body: RegisterIn):
    email = body.email.strip().lower()
    if "@" not in email or len(body.password) < 6:
        bad("Enter a valid email and a passphrase of at least 6 characters")
    if await db.users.find_one({"email": email}):
        bad("Email already registered")
    r = await db.users.insert_one({"email": email, "password_hash": hash_password(body.password),
                                   "name": body.name.strip() or email.split("@")[0], "role": "user", "created_at": iso_now()})
    u = await db.users.find_one({"_id": r.inserted_id})
    await ledger.provision_user(str(u["_id"]))
    return {"token": create_token(str(u["_id"]), email), "user": public_user(u)}


@api.post("/auth/login")
async def login(body: LoginIn, request: Request):
    email = body.email.strip().lower()
    ident = f"{request.client.host if request.client else ''}:{email}"
    await check_lockout(ident)
    u = await db.users.find_one({"email": email})
    if not u or not verify_password(body.password, u["password_hash"]):
        await record_fail(ident)
        bad("Invalid email or passphrase", 401)
    await clear_fails(ident)
    return {"token": create_token(str(u["_id"]), email), "user": public_user(u)}


@api.get("/auth/me")
async def me(user=Depends(current_user)):
    return user


# ---------- bot ----------
async def get_bot(user) -> dict:
    bot = await db.bots.find_one({"user_id": user["id"]})
    if bot:
        return bot
    n = await db.bots.count_documents({}) + 1
    doc = {**DEFAULT_BOT, "user_id": user["id"], "name": f"Luchii_{n:03d}", "created_at": iso_now()}
    await db.bots.update_one({"user_id": user["id"]}, {"$setOnInsert": doc}, upsert=True)
    return await db.bots.find_one({"user_id": user["id"]})


async def bot_view(bot: dict) -> dict:
    creds = await get_creds(bot["user_id"])
    out = {k: v for k, v in bot.items() if k not in ("_id", "pending", "metaapi", "telegram_chat_id")}
    if out.get("pocketoption"):
        out["pocketoption"] = {k: v for k, v in out["pocketoption"].items() if k != "ssid"}
    done = bot["wins"] + bot["losses"]
    out.update(equity=engine.equity(bot), win_rate=round(bot["wins"] / done * 100) if done else 0,
               exchange_connected=bool(creds), exchange=creds["exchange"] if creds else None)
    return out


@api.get("/bot/state")
async def bot_state(user=Depends(current_user)):
    return await bot_view(await get_bot(user))


class ConfigIn(BaseModel):
    mode: Optional[Literal["live"]] = None
    position_usd: Optional[float] = Field(None, gt=0)
    max_order_usd: Optional[float] = Field(None, ge=0)
    daily_loss_limit: Optional[float] = Field(None, ge=0)
    manual_confirm: Optional[bool] = None
    auto_fund: Optional[bool] = None
    take_profit_pct: Optional[float] = Field(None, ge=0, le=100)
    stop_loss_pct: Optional[float] = Field(None, ge=0, le=100)
    trend_filter: Optional[bool] = None
    volume_filter: Optional[bool] = None
    trailing_stop: Optional[bool] = None
    partial_tp: Optional[bool] = None
    maker_entries: Optional[bool] = None
    dip_entries: Optional[bool] = None
    aggressive: Optional[bool] = None
    size_pct: Optional[float] = Field(None, ge=0, le=100)
    assets: Optional[list[Literal["BTC", "ETH", "SOL"]]] = Field(None, min_length=1)
    strategy: Optional[Literal["luchii003", "aggressive"]] = None


@api.get("/strategy/gate")
async def strategy_gate(user=Depends(current_user)):
    import gates
    bot = await get_bot(user)
    return await gates.evaluate(user["id"], bot.get("strategy", "luchii003"))


@api.get("/strategy/stats")
async def strategy_stats(user=Depends(current_user)):
    import gates
    return await gates.rolling(user["id"])


@api.post("/strategy/gate/reset")
async def strategy_gate_reset(user=Depends(current_user)):
    import gates
    bot = await get_bot(user)
    return await gates.reset_gate(user["id"], bot.get("strategy", "luchii003"))


@api.get("/strategy/symbols")
async def strategy_symbols(user=Depends(current_user)):
    import gates
    return await gates.symbols(user["id"])


class SymbolIn(BaseModel):
    enabled: bool


@api.post("/strategy/symbols/{symbol}")
async def strategy_symbol_set(symbol: Literal["BTC", "ETH", "SOL"], body: SymbolIn, user=Depends(current_user)):
    import gates
    return await gates.set_symbol(user["id"], symbol, body.enabled)


@api.get("/trades/audit")
async def trades_audit(user=Depends(current_user)):
    import audit
    return await audit.closed(user["id"])


@api.get("/trades/audit.csv")
async def trades_audit_csv(user=Depends(current_user)):
    import audit
    from fastapi.responses import Response
    rows = await audit.closed(user["id"])
    return Response(audit.to_csv(rows), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=luchii_trade_audit.csv"})


@api.get("/trades/buckets")
async def trades_buckets(user=Depends(current_user)):
    import audit
    rows = await audit.closed(user["id"])
    return {"trades": len(rows), "buckets": audit.buckets(rows)}


@api.get("/strategy/agents")
async def strategy_agents(user=Depends(current_user)):
    import agents
    import audit
    bot = await get_bot(user)
    rows = await audit.closed(user["id"])
    weights = agents.weights_from_audit(rows)
    out = {}
    for a in bot.get("assets") or ["BTC"]:
        i, _ = engine.snapshot(a)
        out[a] = agents.vote(i, weights) if i else {"scores": {}, "final": 0, "winner": None, "allowed": False, "reason": "warming up"}
        out[a]["regime"] = i.get("regime") if i else None
    perf = {ag: {"trades": len([r for r in rows if r.get("winner_agent") == ag]), "weight": weights[ag]} for ag in agents.AGENTS}
    return {"min_final": agents.MIN_FINAL, "allowed": {k: sorted(v) for k, v in agents.REGIME_ALLOWED.items()}, "weights": weights, "performance": perf, "assets": out}


@api.get("/strategy/montecarlo")
async def strategy_montecarlo(user=Depends(current_user)):
    import audit
    from luchii_backtest import monte_carlo_ruin
    rows = await audit.closed(user["id"])
    bot = await get_bot(user)
    eq = float(bot.get("live_equity") or bot.get("start_equity") or 1000.0)
    return {"source": "live trade audit", **monte_carlo_ruin([r["net_pnl"] for r in rows if r.get("net_pnl") is not None], starting_equity=eq)}


WF_JOBS: dict = {}


async def _run_wf(uid: str, asset: str, days: int):
    import concurrent.futures
    import luchii_backtest as lb
    key = f"{uid}:{asset}"
    try:
        WF_JOBS[key].update(status="running", stage="fetching candles + walk-forward (separate process)")
        with concurrent.futures.ProcessPoolExecutor(max_workers=1) as pool:
            doc = await asyncio.get_running_loop().run_in_executor(pool, lb.validation_job, asset, days)
        doc.update(user_id=uid, ts=now_ms(), status="done")
        await db.backtests.update_one({"user_id": uid, "asset": asset}, {"$set": doc}, upsert=True)
        WF_JOBS[key] = {"status": "done", "ts": now_ms()}
    except Exception as e:  # noqa: BLE001
        WF_JOBS[key] = {"status": "error", "error": str(e)[:200], "ts": now_ms()}


@api.post("/strategy/walkforward/{asset}")
async def strategy_walkforward_start(asset: Literal["BTC", "ETH", "SOL"], days: int = 30, user=Depends(current_user)):
    key = f"{user['id']}:{asset}"
    if WF_JOBS.get(key, {}).get("status") in ("queued", "running"):
        return {"status": WF_JOBS[key]["status"], "asset": asset}
    WF_JOBS[key] = {"status": "queued", "ts": now_ms(), "stage": "fetching candles"}
    asyncio.create_task(_run_wf(user["id"], asset, max(7, min(days, 90))))
    return {"status": "queued", "asset": asset, "days": days}


@api.get("/strategy/walkforward/{asset}")
async def strategy_walkforward_get(asset: Literal["BTC", "ETH", "SOL"], user=Depends(current_user)):
    job = WF_JOBS.get(f"{user['id']}:{asset}") or {}
    doc = await db.backtests.find_one({"user_id": user["id"], "asset": asset}, {"_id": 0, "user_id": 0})
    return {"job": job, "result": doc}


@api.get("/strategy/metrics")
async def strategy_metrics(user=Depends(current_user)):
    bot = await get_bot(user)
    out = {}
    for a in bot.get("assets") or ["BTC"]:
        d = engine.decide(bot, a)
        lm = d if bot.get("strategy", "luchii003") == "luchii003" else engine.luchii_signal(bot, a)
        m = lm.get("metrics") or {}
        out[a] = {"action": d["action"], "score": lm.get("score", 0), "regime": lm.get("regime", "-"), "reason": d["reason"],
                  "trend_pct": m.get("trend_pct"), "volume_ratio": m.get("volume_ratio"), "adx": m.get("adx"), "rsi": m.get("rsi"),
                  "atr": m.get("atr"), "price": m.get("price"), "stop": d.get("stop") or lm.get("stop"), "expected_move_pct": round(m["atr"] / m["price"] * 100 * 2.5, 3) if m.get("atr") and m.get("price") else None}
    return {"strategy": bot.get("strategy", "luchii003"), "assets": out}


@api.post("/bot/config")
async def bot_config(body: ConfigIn, user=Depends(current_user)):
    await get_bot(user)
    upd = body.model_dump(exclude_none=True)
    if upd.get("mode") == "live" and not await get_creds(user["id"]):
        bad("Live mode needs a connected exchange")
    if upd:
        await set_bot(user["id"], **upd)
        if "strategy" in upd:
            await engine.log(db, user["id"], {"action": "HOLD", "engine": "system", "confidence": 1.0,
                                              "reason": f"strategy switched → {'AGGRESSIVE SCALP' if upd['strategy'] == 'aggressive' else 'LUCHII_003'} · applies to next entry · open positions still managed"}, SNAPSHOT.get("price"))
    return await bot_view(await get_bot(user))


@api.post("/bot/start")
async def bot_start(user=Depends(current_user)):
    bot = await get_bot(user)
    if bot.get("kill_switch"):
        bad("Kill switch engaged — re-arm first")
    if bot["mode"] == "live" and not await get_creds(user["id"]):
        bad("Live mode needs a connected exchange")
    await set_bot(user["id"], status="running")
    await engine.log(db, user["id"], {"action": "HOLD", "engine": "system", "confidence": 1.0,
                                      "reason": f"engine engaged · {bot['mode'].upper()} · {', '.join(bot.get('assets') or ['BTC'])} · {'LUCHII_003 risk-based sizing (ATR stop, tiered TP, staged trail)' if bot.get('strategy', 'luchii003') == 'luchii003' else (str(bot.get('size_pct') or 0) + '% of balance' if bot.get('size_pct') else '$' + str(engine.order_usd(bot))) + ' per trade · ' + ('AGGRESSIVE' if bot.get('aggressive', True) else 'STANDARD')}"}, SNAPSHOT.get("price"))
    await alerts.notify(bot, "▶ Luchii engine engaged")
    return {"ok": True}


@api.post("/bot/stop")
async def bot_stop(user=Depends(current_user)):
    await get_bot(user)
    await set_bot(user["id"], status="stopped")
    await engine.log(db, user["id"], {"action": "HOLD", "engine": "system", "confidence": 1.0, "reason": "engine halted"}, SNAPSHOT.get("price"))
    return {"ok": True}


@api.post("/bot/kill")
async def bot_kill(user=Depends(current_user)):
    bot = await get_bot(user)
    await set_bot(user["id"], kill_switch=True, status="stopped", pending=None)
    await engine.log(db, user["id"], {"action": "HOLD", "engine": "guard", "confidence": 1.0, "reason": "KILL SWITCH ENGAGED"}, SNAPSHOT.get("price"))
    await alerts.notify(bot, "🛑 Kill switch engaged")
    return {"ok": True}


@api.post("/bot/kill/reset")
async def bot_kill_reset(user=Depends(current_user)):
    await get_bot(user)
    await set_bot(user["id"], kill_switch=False)
    return {"ok": True}


@api.post("/bot/reset")
async def bot_reset(user=Depends(current_user)):
    bot = await get_bot(user)
    start = bot.get("live_equity") or None
    await set_bot(user["id"], status="stopped", kill_switch=False, pending=None, cash=engine.START_CASH, position_qty=0.0,
                  start_equity=start, start_ts=now_ms() if start else None, alt_positions={}, peak_price=0.0, tp_taken=False, stop_price=0.0, tiers_done=0, entry_atr=0.0,
                  entry_price=0.0, realized_pnl=0.0, trades_count=0, wins=0, losses=0)
    for c in (db.bot_logs, db.bot_trades, db.bot_pnl):
        await c.delete_many({"user_id": user["id"]})
    return {"ok": True}


@api.get("/bot/logs")
async def bot_logs(user=Depends(current_user)):
    return await db.bot_logs.find({"user_id": user["id"]}, {"_id": 0, "user_id": 0}).sort("ts", -1).to_list(60)


@api.get("/bot/logs/day")
async def bot_logs_day(user=Depends(current_user)):
    since = now_ms() - 24 * 3600 * 1000
    return await db.bot_logs.find({"user_id": user["id"], "ts": {"$gte": since}}, {"_id": 0, "user_id": 0}).sort("ts", -1).to_list(3000)


@api.get("/bot/trades")
async def bot_trades(user=Depends(current_user)):
    return await db.bot_trades.find({"user_id": user["id"]}, {"_id": 0, "user_id": 0}).sort("ts", -1).to_list(50)


@api.get("/bot/pnl")
async def bot_pnl(user=Depends(current_user)):
    bot = await get_bot(user)
    q = {"user_id": user["id"]}
    if bot.get("start_ts"):
        q["ts"] = {"$gte": bot["start_ts"]}
    rows = await db.bot_pnl.find(q, {"_id": 0, "user_id": 0}).sort("ts", -1).to_list(300)
    return rows[::-1]


@api.get("/bot/pending")
async def bot_pending(user=Depends(current_user)):
    p = (await get_bot(user)).get("pending")
    return {k: v for k, v in p.items() if k != "decision"} if p else None


@api.post("/bot/pending/approve")
async def pending_approve(user=Depends(current_user)):
    bot = await get_bot(user)
    p = bot.get("pending") or bad("Nothing pending")
    await set_bot(user["id"], pending=None)
    bot["pending"] = None
    await engine.execute(bot, {**p["decision"], "engine": "manual"}, p.get("asset", "BTC"))
    return {"ok": True}


@api.post("/bot/pending/reject")
async def pending_reject(user=Depends(current_user)):
    bot = await get_bot(user)
    p = bot.get("pending") or bad("Nothing pending")
    await set_bot(user["id"], pending=None)
    await engine.log(db, user["id"], {"action": p["action"], "engine": "manual", "confidence": 1.0, "reason": "rejected by operator"}, p["price"], "REJECTED")
    return {"ok": True}


@api.get("/leaderboard")
async def leaderboard(user=Depends(current_user)):
    bots = await db.bots.find({}, {"_id": 0, "name": 1, "realized_pnl": 1}).sort([("realized_pnl", -1), ("name", 1)]).to_list(50)
    return [{"rank": i + 1, "name": b["name"], "pnl": round(b.get("realized_pnl", 0), 2)} for i, b in enumerate(bots)]


@api.get("/market/snapshot")
async def market_snapshot():
    return SNAPSHOT


# ---------- exchange ----------
class ExchangeIn(BaseModel):
    exchange: str
    api_key: str
    secret: str
    password: str = ""


@api.get("/exchange/list")
async def exchange_list():
    return EXCHANGES


@api.post("/exchange/connect")
async def exchange_connect(body: ExchangeIn, user=Depends(current_user)):
    if body.exchange not in EXCHANGES:
        bad("Unsupported exchange")
    await get_bot(user)
    secret = body.secret.strip().replace("\\n", "\n")
    try:
        bal, host, perms = await verify(body.exchange, body.api_key.strip(), secret, body.password.strip())
    except Exception as e:
        bad(f"Exchange verification failed: {friendly(e)}")
    await db.exchange_credentials.update_one({"user_id": user["id"]}, {"$set": {
        "user_id": user["id"], "exchange": body.exchange, "api_key": encrypt(body.api_key.strip()), "hostname": host,
        "secret": encrypt(secret), "password": encrypt(body.password.strip()), "permissions": perms, "created_at": iso_now()}}, upsert=True)
    await set_bot(user["id"], mode="live", start_equity=None, start_ts=None)
    await engine.refresh_live_equity({"user_id": user["id"]})
    return {"ok": True, "balances": nonzero_balances(bal), "permissions": perms, "hostname": host, "warnings": perm_warnings(perms)}


def perm_warnings(perms):
    if not perms:
        return []
    w = []
    if not perms.get("trade"):
        w.append("This API key has no TRADE permission, so the bot can't place orders. Edit the key on the exchange and enable Trade.")
    if not perms.get("withdraw"):
        w.append("This API key has no WITHDRAW permission, so withdrawals will be rejected.")
    return w


@api.get("/exchange/capabilities")
async def exchange_capabilities(user=Depends(current_user)):
    creds = await get_creds(user["id"]) or bad("No exchange connected")

    async def run(ex):
        perms = await permissions(ex, creds["exchange"])
        symbol = await resolve_symbol(ex, creds["exchange"])
        if perms:
            await db.exchange_credentials.update_one({"user_id": user["id"]}, {"$set": {"permissions": perms}})
        has = {k: bool(ex.has.get(k)) for k in ("createOrder", "fetchDepositAddress", "withdraw", "transfer", "fetchDeposits", "fetchWithdrawals")}
        return {"exchange": creds["exchange"], "hostname": creds.get("hostname"), "symbol": symbol,
                "permissions": perms, "has": has, "accounts": ACCOUNTS.get(creds["exchange"]), "warnings": perm_warnings(perms)}
    return await with_exchange(user["id"], run)


@api.post("/exchange/sync")
async def exchange_sync(user=Depends(current_user)):
    creds = await get_creds(user["id"]) or bad("No exchange connected")

    async def run(ex):
        bal = await all_balances(ex, creds["exchange"])
        eq = total_usd(bal, SNAPSHOT.get("price") or 0)
        await set_bot(user["id"], live_equity=eq)
        return {"balances": bal, "equity": eq, "ts": now_ms()}
    return await with_exchange(user["id"], run)


class OrderIn(BaseModel):
    side: Literal["BUY", "SELL"]
    usd: Optional[float] = Field(None, gt=0)
    qty: Optional[float] = Field(None, gt=0)


@api.post("/exchange/order")
async def exchange_order(body: OrderIn, user=Depends(current_user)):
    creds = await get_creds(user["id"]) or bad("No exchange connected")
    bot = await get_bot(user)

    async def run(ex):
        symbol = await resolve_symbol(ex, creds["exchange"])
        price = (await ex.fetch_ticker(symbol))["last"]
        if body.side == "BUY":
            usd = body.usd or engine.order_usd(bot)
            await engine.auto_fund(ex, user["id"], creds["exchange"], symbol, "BUY", usd, 0, bot.get("auto_fund", True))
            o = await market_order(ex, symbol, "BUY", usd, 0, price)
        else:
            if body.qty:
                await engine.auto_fund(ex, user["id"], creds["exchange"], symbol, "SELL", 0, body.qty, bot.get("auto_fund", True))
            elif bot.get("auto_fund", True):
                await engine.auto_fund(ex, user["id"], creds["exchange"], symbol, "SELL", 0, float("inf"), True)
            qty = body.qty or float(((await ex.fetch_balance()).get("BTC") or {}).get("free") or 0)
            if qty <= 0:
                bad("No BTC in the trading account to sell")
            o = await market_order(ex, symbol, "SELL", 0, qty, price)
        return o, price, symbol

    o, price, symbol = await with_exchange(user["id"], run)
    avg = float(o.get("average") or price)
    qty = float(o.get("filled") or 0) or (body.qty or (body.usd or engine.order_usd(bot)) / price)
    await engine.record_fill(bot, body.side, avg, round(qty, 8), f"LIVE FILLED #{o.get('id')}",
                             {"action": body.side, "engine": "manual", "confidence": 1.0, "reason": f"manual {body.side} on {creds['exchange']}"})
    await engine.refresh_live_equity(bot)
    return {"id": str(o.get("id")), "status": o.get("status"), "filled": qty, "average": avg, "symbol": symbol}


@api.post("/exchange/test-order")
async def exchange_test_order(user=Depends(current_user)):
    creds = await get_creds(user["id"]) or bad("No exchange connected")
    bot = await get_bot(user)

    async def run(ex):
        symbol = await resolve_symbol(ex, creds["exchange"])
        m = ex.market(symbol)
        price = (await ex.fetch_ticker(symbol))["last"]
        lim = m.get("limits") or {}
        min_cost = max(float((lim.get("cost") or {}).get("min") or 0), float((lim.get("amount") or {}).get("min") or 0) * price, 5.0)
        usd = round(min_cost * 1.1, 2)
        await engine.auto_fund(ex, user["id"], creds["exchange"], symbol, "BUY", usd, 0, bot.get("auto_fund", True))
        buy = await market_order(ex, symbol, "BUY", usd, 0, price)
        await asyncio.sleep(2)
        filled = float(buy.get("filled") or 0) or usd / price
        free_btc = float(((await ex.fetch_balance()).get("BTC") or {}).get("free") or 0)
        sell = await market_order(ex, symbol, "SELL", 0, min(filled, free_btc), price)
        pick = lambda o: {"id": str(o.get("id")), "status": o.get("status"), "filled": o.get("filled"), "average": o.get("average")}  # noqa: E731
        return {"symbol": symbol, "usd": usd, "price": price, "buy": pick(buy), "sell": pick(sell)}

    result = await with_exchange(user["id"], run)
    for side in ("buy", "sell"):
        await engine.log(db, user["id"], {"action": side.upper(), "engine": "manual", "confidence": 1.0,
                                          "reason": f"Live test order ${result['usd']} on {creds['exchange']} · #{result[side]['id']}"},
                         float(result[side]["average"] or result["price"]), "LIVE FILLED")
    await alerts.notify(await get_bot(user), f"✅ Live test order on {creds['exchange']}: BUY+SELL ${result['usd']} BTC")
    return result


@api.post("/exchange/disconnect")
async def exchange_disconnect(user=Depends(current_user)):
    await db.exchange_credentials.delete_one({"user_id": user["id"]})
    await set_bot(user["id"], status="stopped", live_equity=None, start_equity=None, start_ts=None)
    return {"ok": True}


# ---------- wallet ----------
class WithdrawIn(BaseModel):
    currency: str = "BTC"
    amount: float = Field(gt=0)
    address: str
    network: Optional[str] = None
    tag: Optional[str] = None


class TransferIn(BaseModel):
    currency: str = "USDT"
    amount: float = Field(gt=0)
    direction: Literal["to_trading", "to_funding"] = "to_trading"


@api.get("/wallet/real/balance")
async def wallet_balance(user=Depends(current_user)):
    creds = await get_creds(user["id"]) or bad("No exchange connected")

    async def run(ex):
        b = await all_balances(ex, creds["exchange"])
        merged = {}
        for acc in b.values():
            for k, v in acc.items():
                merged[k] = merged.get(k, 0) + v
        return {"balances": merged, "accounts": b}
    return await with_exchange(user["id"], run)


@api.get("/wallet/real/networks")
async def wallet_networks(currency: str = "BTC", user=Depends(current_user)):
    async def run(ex):
        await ex.load_markets()
        cur = (ex.currencies or {}).get(currency.upper()) or {}
        return [{"id": k, "withdraw": bool(n.get("withdraw", True)), "deposit": bool(n.get("deposit", True)),
                 "fee": n.get("fee"), "min": ((n.get("limits") or {}).get("withdraw") or {}).get("min")}
                for k, n in (cur.get("networks") or {}).items()]
    return await with_exchange(user["id"], run)


@api.get("/wallet/real/deposit-address")
async def wallet_address(currency: str = "BTC", network: Optional[str] = None, user=Depends(current_user)):
    async def run(ex):
        await ex.load_markets()
        a = await ex.fetch_deposit_address(currency.upper(), {"network": network} if network else {})
        return {"address": a.get("address"), "tag": a.get("tag"), "network": a.get("network") or network, "currency": currency.upper()}
    return await with_exchange(user["id"], run)


@api.post("/wallet/real/withdraw")
async def wallet_withdraw(body: WithdrawIn, user=Depends(current_user)):
    params = {"network": body.network} if body.network else {}

    async def run(ex):
        await ex.load_markets()
        r = await ex.withdraw(body.currency.upper(), body.amount, body.address.strip(), body.tag or None, params)
        return {"ok": True, "id": r.get("id"), "status": r.get("status")}
    res = await with_exchange(user["id"], run)
    try:
        await ledger.record_withdrawal(user["id"], body.currency, body.amount, str(res.get("id") or f"{user['id']}:{now_ms()}"), body.address.strip())
    except ledger.LedgerError as e:
        logging.warning("withdrawal ledger posting skipped: %s", e)
    await alerts.notify(await get_bot(user), f"📤 Withdrawal submitted: {body.amount} {body.currency.upper()} → {body.address[:10]}…")
    return res


@api.post("/wallet/real/transfer")
async def wallet_transfer(body: TransferIn, user=Depends(current_user)):
    creds = await get_creds(user["id"]) or bad("No exchange connected")
    acc = ACCOUNTS.get(creds["exchange"]) or bad(f"{creds['exchange']} uses a single account, no transfer needed")
    src, dst = acc if body.direction == "to_trading" else acc[::-1]

    async def run(ex):
        await ex.load_markets()
        r = await ex.transfer(body.currency.upper(), body.amount, src, dst)
        return {"ok": True, "id": r.get("id"), "from": src, "to": dst}
    res = await with_exchange(user["id"], run)
    await engine.refresh_live_equity({"user_id": user["id"]})
    return res


@api.get("/wallet/real/history")
async def wallet_history(user=Depends(current_user)):
    async def run(ex):
        out = []
        for kind, fn in (("deposit", ex.fetch_deposits), ("withdrawal", ex.fetch_withdrawals)):
            try:
                for t in await fn(None, None, 20):
                    out.append({"type": kind, "currency": t.get("currency"), "amount": t.get("amount"), "status": t.get("status"),
                                "network": t.get("network"), "address": t.get("address"), "txid": t.get("txid"), "ts": t.get("timestamp")})
            except Exception:
                pass
        return sorted(out, key=lambda x: x["ts"] or 0, reverse=True)
    rows = await with_exchange(user["id"], run)
    for t in rows:  # mirror confirmed exchange deposits into the ledger, idempotent on txid
        if t["type"] == "deposit" and t.get("status") == "ok" and t.get("txid") and t.get("amount") and (t.get("currency") or "").upper() in ledger.USER_CCY:
            try:
                await ledger.record_deposit(user["id"], t["currency"], t["amount"], t["txid"], txid=t["txid"])
            except ledger.LedgerError:
                pass
    return rows


class Web3In(BaseModel):
    provider: str
    chain: Literal["btc", "evm"]
    address: str = Field(min_length=10)


@api.get("/wallet/web3")
async def web3_list(user=Depends(current_user)):
    rows = await db.web3_wallets.find({"user_id": user["id"]}, {"_id": 0, "user_id": 0}).to_list(20)
    bals = await asyncio.gather(*[web3bal.balance(w["chain"], w["address"]) for w in rows])
    for w, b in zip(rows, bals):
        w["balance"] = b
        w["usd"] = round(b * (SNAPSHOT.get("price") if w["chain"] == "btc" else SNAPSHOT.get("eth") or 0), 2) if b is not None else None
    return rows


@api.post("/wallet/web3/link")
async def web3_link(body: Web3In, user=Depends(current_user)):
    await db.web3_wallets.update_one({"user_id": user["id"], "address": body.address.strip()},
                                     {"$set": {**body.model_dump(), "address": body.address.strip()}}, upsert=True)
    return {"ok": True}


@api.delete("/wallet/web3/{address}")
async def web3_unlink(address: str, user=Depends(current_user)):
    await db.web3_wallets.delete_one({"user_id": user["id"], "address": address})
    return {"ok": True}


# ---------- alerts ----------
class ChatIdIn(BaseModel):
    chat_id: str = Field(min_length=1)


class AlertsCfgIn(BaseModel):
    alerts_enabled: bool


@api.get("/alerts/status")
async def alerts_status(user=Depends(current_user)):
    bot = await get_bot(user)
    return {"chat_id": bot.get("telegram_chat_id"), "enabled": bot.get("alerts_enabled", True)}


@api.post("/alerts/link")
async def alerts_link(user=Depends(current_user)):
    await get_bot(user)
    chat = await alerts.latest_chat()
    if not chat:
        bad("No Telegram messages found. Send any message to the bot first, then press LINK again.")
    await set_bot(user["id"], telegram_chat_id=chat["id"])
    return {"chat_id": chat["id"], "name": chat.get("first_name") or chat.get("username") or chat.get("title") or str(chat["id"])}


@api.post("/alerts/set")
async def alerts_set(body: ChatIdIn, user=Depends(current_user)):
    await get_bot(user)
    await set_bot(user["id"], telegram_chat_id=body.chat_id.strip())
    return {"ok": True}


@api.post("/alerts/test")
async def alerts_test(user=Depends(current_user)):
    bot = await get_bot(user)
    if not bot.get("telegram_chat_id"):
        bad("Link Telegram first")
    r = await alerts.send(bot["telegram_chat_id"], "✅ Luchii test alert — your terminal is linked.")
    if not r.get("ok"):
        bad(f"Telegram error: {r.get('description')}")
    return {"ok": True}


@api.post("/alerts/config")
async def alerts_config(body: AlertsCfgIn, user=Depends(current_user)):
    await get_bot(user)
    await set_bot(user["id"], alerts_enabled=body.alerts_enabled)
    return {"ok": True}


# ---------- brokers ----------
class MetaIn(BaseModel):
    token: str = Field(min_length=10)
    login: str
    password: str
    server: str
    platform: Literal["mt4", "mt5"] = "mt5"
    symbols: str = "BTCUSD"
    volume: float = Field(0.01, gt=0)


class PocketIn(BaseModel):
    ssid: str = Field(min_length=1)
    is_demo: bool = False
    asset: str = "BTCUSD_otc"
    amount: float = Field(1, gt=0)
    duration: int = Field(60, ge=5)


@api.get("/brokers/status")
async def brokers_status(user=Depends(current_user)):
    bot = await get_bot(user)
    creds = await get_creds(user["id"])
    mt, po = bot.get("metaapi") or {}, bot.get("pocketoption") or {}
    return {
        "exchange": {"connected": bool(creds), "name": creds["exchange"] if creds else None, "metrics": None},
        "metaapi": {"connected": bool(mt), "platform": mt.get("platform"), "server": mt.get("server"), "symbols": mt.get("symbols"),
                    "volume": mt.get("volume"), "warm": bool(mt.get("warm")), "metrics": mt.get("metrics")},
        "pocketoption": {"connected": bool(po), "asset": po.get("asset"), "amount": po.get("amount"), "duration": po.get("duration"),
                         "is_demo": po.get("is_demo"), "metrics": po.get("metrics")},
    }


@api.post("/brokers/metaapi/connect")
async def mt_connect(body: MetaIn, user=Depends(current_user)):
    await get_bot(user)
    try:
        acc = await brokers.provision(body.token.strip(), body.login.strip(), body.password, body.server.strip(), body.platform)
    except Exception as e:
        bad(str(e)[:240])
    await set_bot(user["id"], metaapi={"token": body.token.strip(), "platform": body.platform, "server": body.server.strip(),
                                       "symbols": body.symbols, "volume": body.volume, "warm": False, **acc})
    return {"ok": True, "note": "Account deploying on MetaApi — first connection can take a few minutes."}


@api.get("/brokers/metaapi/info")
async def mt_info(user=Depends(current_user)):
    mt = (await get_bot(user)).get("metaapi") or bad("MetaTrader not connected")
    try:
        i = await brokers.account_info(mt)
    except Exception as e:
        bad(str(e)[:240])
    await db.bots.update_one({"user_id": user["id"]}, {"$set": {"metaapi.warm": True}})
    return {"balance": i.get("balance"), "equity": i.get("equity"), "currency": i.get("currency")}


@api.post("/brokers/metaapi/disconnect")
async def mt_disconnect(user=Depends(current_user)):
    await set_bot(user["id"], metaapi=None)
    return {"ok": True}


@api.post("/brokers/pocketoption/connect")
async def po_connect(body: PocketIn, user=Depends(current_user)):
    await get_bot(user)
    try:
        balance = await pocket.connect(user["id"], body.ssid.strip())
    except Exception as e:
        pocket.drop(user["id"])
        bad(f"PocketOption connection failed: {str(e)[:200] or type(e).__name__}")
    await set_bot(user["id"], pocketoption={**body.model_dump(), "ssid": body.ssid.strip(), "warm": True})
    return {"ok": True, "balance": balance}


@api.post("/brokers/pocketoption/disconnect")
async def po_disconnect(user=Depends(current_user)):
    pocket.drop(user["id"])
    await set_bot(user["id"], pocketoption=None)
    return {"ok": True}


@api.post("/brokers/mirror-test")
async def mirror_test(user=Depends(current_user)):
    bot = await get_bot(user)
    if not bot.get("metaapi") and not bot.get("pocketoption"):
        bad("No brokers linked")
    asyncio.create_task(engine.mirror_all(db, bot, "BUY"))
    return {"ok": True}


# ---------- solana DEX ----------
class SolImportIn(BaseModel):
    secret: str = Field(..., min_length=20)


class TokenCfg(BaseModel):
    tp_pct: Optional[float] = Field(None, ge=0, le=10000)
    sl_pct: Optional[float] = Field(None, ge=0, le=100)


class SolConfigIn(BaseModel):
    tokens: Optional[list[str]] = Field(None, max_length=5)
    amount_sol: Optional[float] = Field(None, gt=0)
    slippage_bps: Optional[int] = Field(None, ge=10, le=5000)
    enabled: Optional[bool] = None
    token_tp_pct: Optional[float] = Field(None, ge=0, le=10000)
    token_sl_pct: Optional[float] = Field(None, ge=0, le=100)
    trailing_stop: Optional[bool] = None
    partial_tp: Optional[bool] = None
    token_cfg: Optional[dict[str, TokenCfg]] = None


class SolSwapIn(BaseModel):
    mint: str
    side: Literal["BUY", "SELL"]


async def sol_view(user) -> dict:
    w = await solana_dex.get_wallet(user["id"])
    if not w:
        return {"connected": False}
    if not w.get("tokens"):
        await db.solana_wallets.update_one({"user_id": user["id"]}, {"$set": {"tokens": solana_dex.DEFAULT_MINTS}})
        w["tokens"] = list(solana_dex.DEFAULT_MINTS)
    out = {k: v for k, v in w.items() if k != "secret_enc"}
    out["connected"] = True
    try:
        bal, info, px = await asyncio.gather(solana_dex.balances(w["pubkey"]), solana_dex.token_info(w.get("tokens") or []),
                                             solana_dex.prices(w.get("tokens") or []))
        out["sol_balance"] = bal["sol"]
        out["token_balances"] = {}
        for m in w.get("tokens") or []:
            pos = (w.get("positions") or {}).get(m) or {}
            tp, sl = solana_dex.token_targets(w, m)
            entry, p = float(pos.get("entry_usd") or 0), px.get(m) or (info.get(m) or {}).get("price")
            peak = max(float(pos.get("peak_usd") or 0), entry)
            out["token_balances"][m] = {**(bal["tokens"].get(m) or {"ui": 0.0, "raw": 0}), **info.get(m, {}), "price": p,
                                        "entry_usd": entry or None, "move_pct": round((p - entry) / entry * 100, 2) if p and entry else None,
                                        "peak_usd": peak or None, "from_peak_pct": round((p - peak) / peak * 100, 2) if p and peak else None,
                                        "tp_taken": bool(pos.get("tp_taken")),
                                        "tp_pct": tp, "sl_pct": sl, "overridden": m in (w.get("token_cfg") or {})}
    except Exception as e:
        out["error"] = str(e)[:160]
    return out


@api.get("/solana/wallet")
async def sol_wallet(user=Depends(current_user)):
    return await sol_view(user)


@api.post("/solana/wallet/import")
async def sol_import(body: SolImportIn, user=Depends(current_user)):
    try:
        pub = await solana_dex.import_wallet(user["id"], body.secret)
    except Exception:
        bad("Invalid Solana private key. Paste the base58 key from Phantom/Solflare or the JSON byte array.")
    return {"ok": True, "pubkey": pub}


@api.delete("/solana/wallet")
async def sol_remove(user=Depends(current_user)):
    await db.solana_wallets.delete_one({"user_id": user["id"]})
    return {"ok": True}


@api.post("/solana/config")
async def sol_config(body: SolConfigIn, user=Depends(current_user)):
    if not await solana_dex.get_wallet(user["id"]):
        bad("Import a Solana wallet first")
    upd = body.model_dump(exclude_none=True)
    if "tokens" in upd:
        upd["tokens"] = list(dict.fromkeys(t.strip() for t in upd["tokens"] if 32 <= len(t.strip()) <= 44))
    if "token_cfg" in upd:
        for m, c in upd.pop("token_cfg").items():
            if 32 <= len(m) <= 44 and m.isalnum():
                for k, v in c.items():
                    if v is not None:
                        upd[f"token_cfg.{m}.{k}"] = v
    if upd:
        await db.solana_wallets.update_one({"user_id": user["id"]}, {"$set": upd})
    return await sol_view(user)


@api.delete("/solana/config/token/{mint}")
async def sol_token_reset(mint: str, user=Depends(current_user)):
    if not mint.isalnum():
        bad("Invalid mint")
    await db.solana_wallets.update_one({"user_id": user["id"]}, {"$unset": {f"token_cfg.{mint}": ""}})
    return await sol_view(user)


@api.get("/solana/tokens/search")
async def sol_search(q: str, user=Depends(current_user)):
    try:
        return await solana_dex.search(q.strip())
    except Exception as e:
        bad(f"Token search failed: {str(e)[:120]}")


@api.post("/solana/swap")
async def sol_swap(body: SolSwapIn, user=Depends(current_user)):
    w = await solana_dex.get_wallet(user["id"]) or bad("Import a Solana wallet first")
    bot = await get_bot(user)
    try:
        bal = await solana_dex.balances(w["pubkey"])
        if body.side == "BUY":
            res = await solana_dex.swap(user["id"], solana_dex.SOL, body.mint, int(float(w.get("amount_sol") or 0) * solana_dex.LAMPORTS))
        else:
            raw = (bal["tokens"].get(body.mint) or {}).get("raw", 0) or bad("No balance of that token to sell")
            res = await solana_dex.swap(user["id"], body.mint, solana_dex.SOL, raw)
    except HTTPException:
        raise
    except Exception as e:
        await engine.log(db, user["id"], {"action": body.side, "engine": "solana", "confidence": 1.0,
                                          "reason": f"Manual Solana swap error: {str(e)[:140]}"}, SNAPSHOT.get("price"), "REJECTED")
        bad(f"Swap failed: {str(e)[:200]}")
    await engine.log(db, user["id"], {"action": body.side, "engine": "solana", "confidence": 1.0,
                                      "reason": f"Manual Solana swap {body.mint[:6]}… · {res['status']} · {res['ms']}ms"},
                     SNAPSHOT.get("price"), f"SOL #{res['sig'][:10]}")
    await alerts.notify(bot, f"◎ Solana {body.side} {body.mint[:6]}… {res['status']}")
    return res


@api.get("/solana/swaps")
async def sol_swaps(user=Depends(current_user)):
    return [s async for s in db.solana_swaps.find({"user_id": user["id"]}, {"_id": 0}).sort("ts", -1).limit(50)]


# ---------- chat / help ----------
MODELS = [{"id": "luchii-4.7", "name": "Luchii 4.7"}, {"id": "luchii-4.7-pro", "name": "Luchii 4.7 Pro"},
          {"id": "luchii-mini", "name": "Luchii Mini"}]
OFFLINE = "Luchii is temporarily unavailable. Please try again later."


class ChatIn(BaseModel):
    conversation_id: Optional[str] = None
    model: str = "luchii-4.7"
    message: str = Field(min_length=1)


class ConvPatch(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None


class HelpIn(BaseModel):
    message: str = Field(min_length=1)
    session_id: Optional[str] = None


@api.get("/chat/models")
async def chat_models():
    return MODELS


@api.get("/chat/conversations")
async def chat_list(user=Depends(current_user)):
    return await db.conversations.find({"user_id": user["id"]}, {"_id": 0, "user_id": 0, "messages": 0}).sort(
        [("pinned", -1), ("created_at", -1)]).to_list(200)


@api.get("/chat/conversations/{cid}")
async def chat_get(cid: str, user=Depends(current_user)):
    c = await db.conversations.find_one({"id": cid, "user_id": user["id"]}, {"_id": 0, "user_id": 0})
    return c or bad("Conversation not found", 404)


@api.patch("/chat/conversations/{cid}")
async def chat_patch(cid: str, body: ConvPatch, user=Depends(current_user)):
    upd = body.model_dump(exclude_none=True)
    if upd:
        await db.conversations.update_one({"id": cid, "user_id": user["id"]}, {"$set": upd})
    return {"ok": True}


@api.delete("/chat/conversations/{cid}")
async def chat_delete(cid: str, user=Depends(current_user)):
    await db.conversations.delete_one({"id": cid, "user_id": user["id"]})
    return {"ok": True}


@api.post("/chat/message")
async def chat_message(body: ChatIn, user=Depends(current_user)):
    bad(OFFLINE, 503)


@api.post("/help/chat")
async def help_chat(body: HelpIn, user=Depends(current_user)):
    bad("Assistant is temporarily unavailable. Please try again later.", 503)


class ContactIn(BaseModel):
    name: str = ""
    email: str = ""
    subject: str = ""
    message: str = Field(min_length=1)


@api.post("/contact")
async def contact(body: ContactIn, request: Request):
    await db.contact_messages.insert_one({"id": str(uuid.uuid4()), **body.model_dump(), "ts": now_ms()})
    return {"ok": True}


@api.get("/")
async def root():
    return {"service": "luchii", "ok": True}


app.include_router(api)
api.include_router(ledger_routes.r)
app.include_router(ledger_routes.r, prefix="/api")
app.add_middleware(CORSMiddleware, allow_origins=os.environ["CORS_ORIGINS"].split(","), allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup():
    await seed_admin()
    await ledger.ensure_indexes()
    await ledger.seed_system_accounts()
    async for u in db.users.find({}, {"_id": 1}):
        await ledger.provision_user(str(u["_id"]))
    asyncio.create_task(webhooks.worker())
    await db.bots.create_index("user_id", unique=True)
    await db.bot_logs.create_index([("user_id", 1), ("ts", -1)])
    await db.bot_trades.create_index([("user_id", 1), ("ts", -1)])
    await db.bot_pnl.create_index([("user_id", 1), ("ts", -1)])
    import audit
    await audit.ensure_indexes()
    asyncio.create_task(market.loop())
    asyncio.create_task(engine.scheduler())
    asyncio.create_task(solana_dex.watcher())
    if not await db.meta.find_one({"_id": "boost_v1"}):
        await db.bots.update_many({}, {"$set": engine.BOOST_DEFAULTS})
        await db.meta.insert_one({"_id": "boost_v1", "ts": now_ms()})
    if not await db.meta.find_one({"_id": "luchii003_v1"}):
        await db.bots.update_many({}, {"$set": engine.LUCHII003_DEFAULTS})
        await db.meta.insert_one({"_id": "luchii003_v1", "ts": now_ms()})
