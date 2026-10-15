# Iteration 9: boost (aggressive preset, size_pct, scalp TP/SL, multi-market BTC/ETH/SOL)
import asyncio
import os
import sys
import uuid

import pytest
import requests
from bson import ObjectId
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402
from market import SNAPSHOT  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")


def _candles(closes, last_vol=10.0):
    cs = [{"c": c, "v": 10.0, "o": c, "h": c, "l": c, "t": i} for i, c in enumerate(closes)]
    cs[-2]["v"] = last_vol
    return cs


@pytest.fixture
def snap():
    old = dict(SNAPSHOT)

    def _set(asset, closes, rsi, last_vol=10.0, price=None):
        m = dict(SNAPSHOT.get("markets") or {})
        m[asset] = {"candles": _candles(closes, last_vol), "price": price or closes[-1], "rsi": rsi}
        SNAPSHOT["markets"] = m
        if asset == "BTC":
            SNAPSHOT.update(candles=m[asset]["candles"], price=m[asset]["price"], rsi=rsi)
        return m[asset]["price"]
    yield _set
    SNAPSHOT.clear(); SNAPSHOT.update(old)


def rising(base=3000.0, step=2.0):
    return [base + i * step for i in range(40)]


def mk(**kw):
    b = {**engine.DEFAULT_BOT, "alt_positions": {}}
    b.update(kw)
    return b


# --- market snapshot API
class TestSnapshot:
    def test_markets(self):
        r = requests.get(f"{BASE_URL}/api/market/snapshot", timeout=15)
        assert r.status_code == 200
        d = r.json()
        for a in ("BTC", "ETH", "SOL"):
            m = d["markets"][a]
            assert m["price"] > 0 and len(m["candles"]) == 60 and m["rsi"] is not None, (a, m.get("price"))
        for k in ("price", "eth", "sol", "candles", "rsi"):
            assert d.get(k) is not None, k
        assert d["price"] == d["markets"]["BTC"]["price"]


# --- engine unit tests (synthetic SNAPSHOT)
class TestEngine:
    def test_eth_partial_tp_uses_alt_positions(self, snap):
        p = snap("ETH", rising(), 60.0)
        b = mk(alt_positions={"ETH": {"qty": 1.0, "entry": p / 1.01, "peak": p, "tp_taken": False}})
        d = engine.signal(b, "ETH")
        assert d["action"] == "SELL" and d.get("partial") and "[ETH] PARTIAL TAKE PROFIT" in d["reason"], d
        # BTC flat -> signal for BTC does not see ETH position
        snap("BTC", rising(90000, 50), 60.0)
        assert engine.signal(b, "BTC")["action"] == "BUY"

    def test_rsi_cap_aggressive_vs_standard(self, snap):
        snap("ETH", rising(), 75.0)
        a = engine.signal(mk(aggressive=True), "ETH")
        s = engine.signal(mk(aggressive=False), "ETH")
        assert a["action"] == "BUY" and "AGGRESSIVE" in a["reason"], a
        assert s["action"] == "HOLD", s

    def test_volume_filter_skipped_when_aggressive(self, snap):
        snap("SOL", rising(150, 0.2), 60.0, last_vol=1.0)
        a = engine.signal(mk(aggressive=True), "SOL")
        s = engine.signal(mk(aggressive=False), "SOL")
        assert a["action"] == "BUY", a
        assert s["action"] == "HOLD" and "volume filter" in s["reason"], s

    def test_pos_fields(self):
        assert engine.pos_fields("SOL", qty=1) == {"alt_positions.SOL.qty": 1}
        assert engine.pos_fields("BTC", qty=1, entry=2) == {"position_qty": 1, "entry_price": 2}

    def test_order_usd(self):
        assert engine.order_usd(mk(size_pct=50, position_usd=20), have=100) == 50
        assert engine.order_usd(mk(size_pct=0, position_usd=20), have=100) == 20
        assert engine.order_usd(mk(size_pct=50, position_usd=20, max_order_usd=30), have=100) == 30

    def test_equity_counts_alts(self, snap):
        pe = snap("ETH", rising(), 50.0)
        snap("BTC", rising(90000, 50), 50.0)
        b = mk(mode="paper", cash=1000.0, position_qty=0.0, alt_positions={"ETH": {"qty": 2.0, "entry": 1, "peak": 1}})
        assert engine.equity(b) == round(1000 + 2 * pe, 2)


@pytest.fixture(scope="module")
def user():
    mc = MongoClient(os.environ["MONGO_URL"])
    db = mc[os.environ["DB_NAME"]]
    email = f"test_it9_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": "TestPass123!", "name": "TEST it9"})
    assert r.status_code in (200, 201), r.text
    data = r.json()
    uid = data["user"]["id"]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {data['token']}"
    yield s, db, uid
    for c in ("bots", "bot_logs", "bot_trades", "bot_pnl", "solana_wallets"):
        db[c].delete_many({"user_id": uid})
    db.users.delete_one({"_id": ObjectId(uid)} if ObjectId.is_valid(uid) else {"id": uid})
    db.users.delete_one({"email": email})


# --- bot config API
class TestConfigApi:
    def test_defaults(self, user):
        s, _, _ = user
        d = s.get(f"{BASE_URL}/api/bot/state").json()
        assert d["aggressive"] is True and d["size_pct"] == 50 and d["assets"] == ["BTC", "ETH", "SOL"]
        assert d["take_profit_pct"] == 0.6 and d["stop_loss_pct"] == 0.4 and d["mode"] == "live"

    def test_config_accepts(self, user):
        s, _, _ = user
        r = s.post(f"{BASE_URL}/api/bot/config", json={"aggressive": False, "size_pct": 30, "assets": ["BTC", "ETH"]})
        assert r.status_code == 200, r.text
        d = s.get(f"{BASE_URL}/api/bot/state").json()
        assert d["aggressive"] is False and d["size_pct"] == 30 and d["assets"] == ["BTC", "ETH"]
        s.post(f"{BASE_URL}/api/bot/config", json={"aggressive": True, "size_pct": 50, "assets": ["BTC", "ETH", "SOL"]})

    @pytest.mark.parametrize("body", [{"assets": ["BTC", "DOGE"]}, {"assets": []}, {"size_pct": 150}, {"size_pct": -1}])
    def test_config_rejects(self, user, body):
        s, _, _ = user
        assert s.post(f"{BASE_URL}/api/bot/config", json=body).status_code == 422

    def test_paper_execute_alt_then_reset(self, user, snap):
        s, db, uid = user
        price = snap("ETH", rising(), 60.0)
        snap("BTC", rising(90000, 50), 60.0)
        db.bots.update_one({"user_id": uid}, {"$set": {"mode": "paper", "cash": 1000.0}})

        async def flow():
            bot = await engine.db.bots.find_one({"user_id": uid})
            await engine.execute(bot, {"action": "BUY", "reason": "[ETH] test buy", "confidence": 0.9}, "ETH")
            bot = await engine.db.bots.find_one({"user_id": uid})
            await engine.execute(bot, {"action": "SELL", "partial": True, "reason": "[ETH] test partial", "confidence": 0.9}, "ETH")
            return await engine.db.bots.find_one({"user_id": uid})
        b = asyncio.run(flow())
        eth = b["alt_positions"]["ETH"]
        qty = round(500 / price, 6)
        assert abs(eth["qty"] - round(qty / 2, 8)) < 1e-6 and eth["entry"] == price and eth["tp_taken"] is True, eth
        assert b["position_qty"] == 0 and b["trades_count"] == 2
        assert db.bot_trades.count_documents({"user_id": uid, "asset": "ETH"}) == 2
        db.bots.update_one({"user_id": uid}, {"$set": {"mode": "live", "peak_price": 5.0, "tp_taken": True}})
        r = s.post(f"{BASE_URL}/api/bot/reset")
        assert r.status_code == 200
        d = s.get(f"{BASE_URL}/api/bot/state").json()
        assert d["alt_positions"] == {} and d["peak_price"] == 0 and d["tp_taken"] is False and d["position_qty"] == 0
