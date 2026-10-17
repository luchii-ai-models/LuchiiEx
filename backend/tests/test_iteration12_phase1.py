# Iteration 12: Phase 1 - regime engine units, symbol kill switch, gate DISABLED + owner reset, trade audit export/buckets
import asyncio
import os
import sys
import time
import uuid

import pytest
import requests
from bson import ObjectId
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy  # noqa: E402
from test_iteration10_luchii003 import run_engine, uptrend  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
PROTECTED = "6ad0c885f4fa421663189000"


def now_ms():
    return int(time.time() * 1000)


def new_user(tag):
    email = f"test_it12_{tag}_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": "TestPass123!", "name": "TEST it12"}, timeout=20)
    assert r.status_code in (200, 201), r.text
    d = r.json()
    return {"id": d["user"]["id"], "email": email, "h": {"Authorization": f"Bearer {d['token']}"}}


def drop_user(u):
    uid = u["id"]
    assert uid != PROTECTED
    for c in ("bots", "bot_trades", "bot_pnl", "gates", "audit_log", "bot_logs", "symbol_gates", "trade_audit", "solana_wallets"):
        mdb[c].delete_many({"user_id": uid})
    if ObjectId.is_valid(uid):
        mdb.users.delete_one({"_id": ObjectId(uid)})
    mdb.users.delete_one({"email": u["email"]})


@pytest.fixture
def user():
    u = new_user("u")
    yield u
    drop_user(u)


def get(path, u):
    return requests.get(f"{BASE_URL}/api{path}", headers=u["h"], timeout=20)


def post(path, u, body=None):
    return requests.post(f"{BASE_URL}/api{path}", headers=u["h"], json=body or {}, timeout=20)


# --- regime engine units
BASE_I = {"adx": 30, "rsi": 60, "ema20": 3, "ema50": 2, "ema200": 1, "atr": 1.0, "atr_ma": 1.0, "volume_ratio": 1.0}


class TestRegime:
    def test_trend_bull(self):
        assert strategy.regime(BASE_I) == "TREND_BULL"

    def test_trend_bear(self):
        assert strategy.regime({**BASE_I, "ema20": 1, "ema50": 2, "ema200": 3, "rsi": 40}) == "TREND_BEAR"

    def test_chop(self):
        assert strategy.regime({**BASE_I, "adx": 15}) == "CHOP"

    def test_mean_reversion(self):
        assert strategy.regime({**BASE_I, "adx": 22, "rsi": 80}) == "MEAN_REVERSION"

    def test_panic(self):
        assert strategy.regime({**BASE_I, "atr": 3.5, "atr_ma": 1.0}) == "PANIC"

    def test_breakout(self):
        assert strategy.regime({**BASE_I, "atr": 1.5, "atr_ma": 1.0, "volume_ratio": 2.0}, "HIGH") == "BREAKOUT"

    def test_nan_chop(self):
        assert strategy.regime({**BASE_I, "ema200": None}) == "CHOP"

    def _full(self, **kw):
        cs = uptrend()
        i = strategy.indicators(cs, cs[-1]["c"], 70.0)
        return {**i, **kw}

    @pytest.mark.parametrize("reg", ["CHOP", "MEAN_REVERSION"])
    def test_evaluate_no_trade_regimes(self, reg):
        i = self._full(regime=reg)
        d = strategy.evaluate("BTC", i, i, {"position_qty": 0, "entry_price": 0, "peak_price": 0})
        assert d["action"] == "HOLD" and reg in d["reason"], d["reason"]

    def test_evaluate_breakdown(self):
        i = self._full(regime="BREAKOUT", structure="LOW")
        d = strategy.evaluate("BTC", i, i, {"position_qty": 0, "entry_price": 0, "peak_price": 0})
        assert d["action"] == "HOLD" and "BREAKDOWN" in d["reason"], d["reason"]


# --- auth required
class TestUnauth:
    @pytest.mark.parametrize("m,p", [("get", "/strategy/symbols"), ("post", "/strategy/symbols/SOL"), ("post", "/strategy/gate/reset"),
                                     ("get", "/trades/audit"), ("get", "/trades/audit.csv"), ("get", "/trades/buckets")])
    def test_unauth(self, m, p):
        r = getattr(requests, m)(f"{BASE_URL}/api{p}", json={"enabled": True} if m == "post" else None, timeout=20)
        assert r.status_code in (401, 403), (p, r.status_code)


# --- per-symbol gates
class TestSymbols:
    def test_fresh_defaults_and_toggle(self, user):
        r = get("/strategy/symbols", user)
        assert r.status_code == 200, r.text
        d = r.json()
        assert set(d) == {"BTC", "ETH", "SOL"}
        assert d["SOL"]["disabled"] is True and d["SOL"]["by"] == "policy"
        assert d["BTC"]["disabled"] is False and d["ETH"]["disabled"] is False
        assert "user_id" not in d["SOL"] and "_id" not in d["SOL"]
        r = post("/strategy/symbols/SOL", user, {"enabled": True})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["disabled"] is False and s["by"] == "owner" and s["since"] > 0
        assert get("/strategy/symbols", user).json()["SOL"]["disabled"] is False
        assert mdb.audit_log.count_documents({"user_id": user["id"], "event": "symbol_enabled", "symbol": "SOL"}) == 1
        r = post("/strategy/symbols/SOL", user, {"enabled": False})
        assert r.status_code == 200 and r.json()["disabled"] is True
        assert get("/strategy/symbols", user).json()["SOL"]["disabled"] is True
        assert mdb.audit_log.count_documents({"user_id": user["id"], "event": "symbol_disabled", "symbol": "SOL"}) == 1
        assert post("/strategy/symbols/DOGE", user, {"enabled": True}).status_code == 422

    def test_auto_kill_eth(self, user):
        uid, t = user["id"], now_ms()
        get("/strategy/symbols", user)
        trades = [{"user_id": uid, "side": "SELL", "strategy": "luchii003", "asset": "ETH", "pnl": 0.5 if k < 12 else -1.0, "ts": t - (25 - k) * 60_000} for k in range(25)]
        mdb.bot_trades.insert_many(trades)
        d = get("/strategy/symbols", user).json()
        assert d["ETH"]["disabled"] is True and d["ETH"]["by"] == "auto" and "PF" in d["ETH"]["reason"], d["ETH"]
        assert d["BTC"]["disabled"] is False
        assert mdb.audit_log.count_documents({"user_id": uid, "event": "symbol_disabled", "symbol": "ETH", "by": "auto"}) == 1

    def test_reenable_after_auto_kill_stays_enabled(self, user):
        """Owner re-enable should restart stats from now (since) - old losing trades must not instantly re-kill the symbol."""
        uid, t = user["id"], now_ms()
        get("/strategy/symbols", user)
        mdb.bot_trades.insert_many([{"user_id": uid, "side": "SELL", "strategy": "luchii003", "asset": "ETH", "pnl": 0.5 if k < 12 else -1.0,
                                     "ts": t - (25 - k) * 60_000} for k in range(25)])
        assert get("/strategy/symbols", user).json()["ETH"]["disabled"] is True
        time.sleep(0.05)
        assert post("/strategy/symbols/ETH", user, {"enabled": True}).json()["disabled"] is False
        d = get("/strategy/symbols", user).json()
        assert d["ETH"]["disabled"] is False, f"ETH re-killed right after owner re-enable: {d['ETH']}"


# --- gate DISABLED + owner reset
class TestGateReset:
    def test_disabled_then_reset(self, user):
        uid, t = user["id"], now_ms()
        assert get("/strategy/gate", user).json()["state"] == "OPEN"
        # 30 older wins then 10 most recent losses
        trades = [{"user_id": uid, "side": "SELL", "strategy": "luchii003", "asset": "BTC", "pnl": (2.0 if k < 30 else -0.5), "ts": t - (40 - k) * 60_000} for k in range(40)]
        mdb.bot_trades.insert_many(trades)
        g = get("/strategy/gate", user).json()
        assert g["state"] == "DISABLED" and g["raw"] == "DISABLED" and "losses in a row" in g["reason"], g
        r = post("/strategy/gate/reset", user)
        assert r.status_code == 200, r.text
        g = r.json()
        assert g["state"] == "OPEN" and g["since"] > 0 and g["history"][-1]["by"] == "owner" and "_id" not in g, g
        assert mdb.audit_log.count_documents({"user_id": uid, "event": "strategy_gate_reset"}) == 1
        g2 = get("/strategy/gate", user).json()
        assert g2["state"] == "OPEN" and g2["trades"] == 0, g2


# --- trade audit via paper engine flow
class TestTradeAudit:
    def test_paper_round_trip(self, user):
        from market import SNAPSHOT
        uid = user["id"]
        get("/bot/state", user)
        mdb.bots.update_one({"user_id": uid}, {"$set": {"mode": "dry", "cash": 10000.0}})
        import engine
        old = dict(SNAPSHOT)
        cs = uptrend(base=90000.0)
        price = cs[-1]["c"]
        SNAPSHOT["markets"] = {**(SNAPSHOT.get("markets") or {}), "BTC": {"candles": cs, "price": price, "rsi": 70.0}}

        async def flow():
            bot = await engine.db.bots.find_one({"user_id": uid})
            await engine.execute(bot, {"action": "BUY", "risk": 0.01, "stop": price * 0.98, "entry_atr": 55.5, "score": 80, "regime": "TREND_BULL",
                                       "reason": "[BTC] test buy", "confidence": 0.9}, "BTC")
            bot = await engine.db.bots.find_one({"user_id": uid})
            await asyncio.sleep(1.1)
            await engine.execute(bot, {"action": "SELL", "stop": price, "reason": "[BTC] test exit", "confidence": 0.9}, "BTC")
            await asyncio.sleep(1)
        try:
            run_engine(flow)
        finally:
            SNAPSHOT.clear(); SNAPSHOT.update(old)
            mdb.bots.update_one({"user_id": uid}, {"$set": {"mode": "live"}})
        docs = list(mdb.trade_audit.find({"user_id": uid}))
        assert len(docs) == 1 and docs[0]["status"] == "closed", docs
        a = docs[0]
        assert a["entry_price"] == price and a["exit_price"] == price
        assert a["fees"] > 0 and a["slippage"] > 0 and a["net_pnl"] < 0 and a["winner"] is False
        assert a["mae_pct"] <= 0 <= a["mfe_pct"] and a["trade_duration_sec"] >= 1
        assert a["regime"] == "TREND_BULL" and a["adx"] is not None and a["rsi"] is not None and a["signal_score"] == 80
        r = get("/trades/audit", user)
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1 and "_id" not in rows[0] and "user_id" not in rows[0] and rows[0]["trade_id"] == a["trade_id"]
        r = get("/trades/audit.csv", user)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        lines = r.text.strip().splitlines()
        assert lines[0].startswith("trade_id,strategy_id,") and len(lines) == 2, lines
        assert lines[1].startswith(a["trade_id"]) and "FALSE" in lines[1]
        r = get("/trades/buckets", user)
        assert r.status_code == 200
        b = r.json()
        assert b["trades"] == 1 and set(b["buckets"]) == {"score", "adx", "regime", "symbol"}
        assert b["buckets"]["symbol"]["BTC"]["trades"] == 1 and b["buckets"]["regime"]["TREND_BULL"]["trades"] == 1
        assert b["buckets"]["score"]["80-90"]["trades"] == 1
        assert sum(v["trades"] for v in b["buckets"]["adx"].values()) == 1

    def test_empty_csv_header_only(self, user):
        r = get("/trades/audit.csv", user)
        assert r.status_code == 200 and r.text.strip().startswith("trade_id,strategy_id") and len(r.text.strip().splitlines()) == 1
        assert get("/trades/buckets", user).json()["trades"] == 0
