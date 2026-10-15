# Iteration 8: engine.signal() exit rules (losers cut on SMA cross, winners ride), win_rate, config regression
import os
import sys
import uuid

import pytest
import requests
from pymongo import MongoClient
from bson import ObjectId

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402
from market import SNAPSHOT  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")


def _candles(closes):
    return [{"c": c, "v": 10.0, "o": c, "h": c, "l": c, "t": i} for i, c in enumerate(closes)]


@pytest.fixture
def falling():
    """Synthetic falling series -> SMA7 < SMA25"""
    old = dict(SNAPSHOT)
    closes = [100000 - i * 50 for i in range(40)]
    SNAPSHOT.update(candles=_candles(closes), price=closes[-1], rsi=40.0, markets={"BTC": {"candles": _candles(closes), "price": closes[-1], "rsi": 40.0}})
    yield closes[-1]
    SNAPSHOT.clear(); SNAPSHOT.update(old)


@pytest.fixture
def rising():
    """Synthetic rising series -> SMA7 > SMA25, RSI high"""
    old = dict(SNAPSHOT)
    closes = [90000 + i * 50 for i in range(40)]
    SNAPSHOT.update(candles=_candles(closes), price=closes[-1], rsi=80.0, markets={"BTC": {"candles": _candles(closes), "price": closes[-1], "rsi": 80.0}})
    yield closes[-1]
    SNAPSHOT.clear(); SNAPSHOT.update(old)


def bot(**kw):
    b = {**engine.DEFAULT_BOT, "position_qty": 0.001, "take_profit_pct": 1.5, "stop_loss_pct": 1.0, "aggressive": False}
    b.update(kw)
    return b


class TestSignal:
    def test_a_losing_sma_cross_cuts(self, falling):
        d = engine.signal(bot(entry_price=falling * 1.006, peak_price=falling * 1.006))
        assert d["action"] == "SELL" and "cutting early" in d["reason"], d

    def test_a2_losing_uptrend_holds(self, rising):
        d = engine.signal(bot(entry_price=rising * 1.006, peak_price=rising * 1.006))
        assert d["action"] == "HOLD" and "in position" in d["reason"], d

    def test_b_winner_rides(self, falling):
        d = engine.signal(bot(entry_price=falling / 1.003, peak_price=falling))
        assert d["action"] == "HOLD" and "winner rides" in d["reason"], d

    def test_b2_rsi_over_75_no_exit(self, rising):
        d = engine.signal(bot(entry_price=rising / 1.003, peak_price=rising))
        assert d["action"] == "HOLD", d

    def test_c_stop_loss(self, falling):
        d = engine.signal(bot(entry_price=falling * 1.02, peak_price=0))
        assert d["action"] == "SELL" and "STOP LOSS" in d["reason"], d

    def test_d_partial_tp(self, falling):
        d = engine.signal(bot(entry_price=falling / 1.016, tp_taken=False, partial_tp=True))
        assert d["action"] == "SELL" and d.get("partial") and "PARTIAL TAKE PROFIT" in d["reason"], d

    def test_e_trailing_stop(self, falling):
        d = engine.signal(bot(entry_price=falling / 1.005, tp_taken=True, peak_price=falling * 1.02, trailing_stop=True))
        assert d["action"] == "SELL" and "TRAILING STOP" in d["reason"], d

    def test_real_market_snapshot(self):
        # Real snapshot path should not crash
        d = engine.signal(bot(entry_price=(SNAPSHOT.get("price") or 1) * 1.003))
        assert d["action"] in ("HOLD", "SELL")


@pytest.fixture(scope="module")
def user():
    mc = MongoClient(os.environ["MONGO_URL"])
    db = mc[os.environ["DB_NAME"]]
    email = f"test_it8_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": "TestPass123!", "name": "TEST it8"})
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


class TestApi:
    def test_win_rate_zero(self, user):
        s, db, uid = user
        d = s.get(f"{BASE_URL}/api/bot/state").json()
        assert d["win_rate"] == 0

    def test_win_rate_calc(self, user):
        s, db, uid = user
        db.bots.update_one({"user_id": uid}, {"$set": {"wins": 2, "losses": 3}})
        assert s.get(f"{BASE_URL}/api/bot/state").json()["win_rate"] == 40
        db.bots.update_one({"user_id": uid}, {"$set": {"wins": 2, "losses": 1}})
        assert s.get(f"{BASE_URL}/api/bot/state").json()["win_rate"] == 67

    def test_config_regression(self, user):
        s, db, uid = user
        p = {"take_profit_pct": 2.5, "stop_loss_pct": 0.8, "trailing_stop": False, "partial_tp": False,
             "trend_filter": False, "volume_filter": False}
        r = s.post(f"{BASE_URL}/api/bot/config", json=p)
        assert r.status_code == 200, r.text
        st = s.get(f"{BASE_URL}/api/bot/state").json()
        for k, v in p.items():
            assert st[k] == v, k
