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
import market  # noqa: E402
import pocket  # noqa: E402
from auth import (check_lockout, clear_fails, create_token, current_user, hash_password, public_user,  # noqa: E402
                  record_fail, seed_admin, verify_password)
from db import db, now_ms  # noqa: E402
from engine import DEFAULT_BOT, get_creds, set_bot, with_exchange  # noqa: E402
from exchange_service import EXCHANGES, build_client, encrypt, equity_usd, market_order, nonzero_balances, symbol_for  # noqa: E402
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
    await db.bots.insert_one(doc)
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


@api.post("/bot/config")
async def bot_config(body: ConfigIn, user=Depends(current_user)):
    await get_bot(user)
    upd = body.model_dump(exclude_none=True)
    if upd.get("mode") == "live" and not await get_creds(user["id"]):
        bad("Live mode needs a connected exchange")
    if upd:
        await set_bot(user["id"], **upd)
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
                                      "reason": f"engine engaged · {bot['mode'].upper()} · ${engine.order_usd(bot)} per trade"}, SNAPSHOT.get("price"))
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
    await get_bot(user)
    await set_bot(user["id"], status="stopped", kill_switch=False, pending=None, cash=engine.START_CASH, position_qty=0.0,
                  entry_price=0.0, realized_pnl=0.0, trades_count=0, wins=0, losses=0)
    for c in (db.bot_logs, db.bot_trades, db.bot_pnl):
        await c.delete_many({"user_id": user["id"]})
    return {"ok": True}


@api.get("/bot/logs")
async def bot_logs(user=Depends(current_user)):
    return await db.bot_logs.find({"user_id": user["id"]}, {"_id": 0, "user_id": 0}).sort("ts", -1).to_list(60)


@api.get("/bot/trades")
async def bot_trades(user=Depends(current_user)):
    return await db.bot_trades.find({"user_id": user["id"]}, {"_id": 0, "user_id": 0}).sort("ts", -1).to_list(50)


@api.get("/bot/pnl")
async def bot_pnl(user=Depends(current_user)):
    rows = await db.bot_pnl.find({"user_id": user["id"]}, {"_id": 0, "user_id": 0}).sort("ts", -1).to_list(300)
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
    await engine.execute(bot, {**p["decision"], "engine": "manual"})
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
    ex = build_client(body.exchange, body.api_key.strip(), secret, body.password.strip())
    try:
        bal = await ex.fetch_balance()
    except Exception as e:
        bad(f"Exchange verification failed: {type(e).__name__}: {str(e)[:220]}")
    finally:
        await ex.close()
    await db.exchange_credentials.update_one({"user_id": user["id"]}, {"$set": {
        "user_id": user["id"], "exchange": body.exchange, "api_key": encrypt(body.api_key.strip()),
        "secret": encrypt(secret), "password": encrypt(body.password.strip()), "created_at": iso_now()}}, upsert=True)
    await set_bot(user["id"], mode="live", live_equity=equity_usd(bal, SNAPSHOT.get("price") or 0))
    return {"ok": True, "balances": nonzero_balances(bal)}


@api.post("/exchange/test-order")
async def exchange_test_order(user=Depends(current_user)):
    creds = await get_creds(user["id"]) or bad("No exchange connected")
    symbol = symbol_for(creds["exchange"])

    async def run(ex):
        await ex.load_markets()
        m = ex.market(symbol)
        price = (await ex.fetch_ticker(symbol))["last"]
        lim = m.get("limits") or {}
        min_cost = max(float((lim.get("cost") or {}).get("min") or 0), float((lim.get("amount") or {}).get("min") or 0) * price, 5.0)
        usd = round(min_cost * 1.1, 2)
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
    await set_bot(user["id"], status="stopped", live_equity=None)
    return {"ok": True}


# ---------- wallet ----------
class WithdrawIn(BaseModel):
    currency: str = "BTC"
    amount: float = Field(gt=0)
    address: str


@api.get("/wallet/real/balance")
async def wallet_balance(user=Depends(current_user)):
    async def run(ex):
        return {"balances": nonzero_balances(await ex.fetch_balance())}
    return await with_exchange(user["id"], run)


@api.get("/wallet/real/deposit-address")
async def wallet_address(currency: str = "BTC", user=Depends(current_user)):
    async def run(ex):
        a = await ex.fetch_deposit_address(currency)
        return {"address": a.get("address"), "tag": a.get("tag")}
    return await with_exchange(user["id"], run)


@api.post("/wallet/real/withdraw")
async def wallet_withdraw(body: WithdrawIn, user=Depends(current_user)):
    async def run(ex):
        r = await ex.withdraw(body.currency, body.amount, body.address.strip())
        return {"ok": True, "id": r.get("id")}
    return await with_exchange(user["id"], run)


class Web3In(BaseModel):
    provider: str
    chain: Literal["btc", "evm"]
    address: str = Field(min_length=10)


@api.get("/wallet/web3")
async def web3_list(user=Depends(current_user)):
    return await db.web3_wallets.find({"user_id": user["id"]}, {"_id": 0, "user_id": 0}).to_list(20)


@api.post("/wallet/web3/link")
async def web3_link(body: Web3In, user=Depends(current_user)):
    await db.web3_wallets.update_one({"user_id": user["id"], "address": body.address.strip()},
                                     {"$set": {**body.model_dump(), "address": body.address.strip(), "balance": None}}, upsert=True)
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
app.add_middleware(CORSMiddleware, allow_origins=os.environ["CORS_ORIGINS"].split(","), allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup():
    await seed_admin()
    await db.bots.create_index("user_id", unique=True)
    await db.bot_logs.create_index([("user_id", 1), ("ts", -1)])
    await db.bot_trades.create_index([("user_id", 1), ("ts", -1)])
    await db.bot_pnl.create_index([("user_id", 1), ("ts", -1)])
    asyncio.create_task(market.loop())
    asyncio.create_task(engine.scheduler())
