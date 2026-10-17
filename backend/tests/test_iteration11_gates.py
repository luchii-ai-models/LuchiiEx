# Iteration 11: strategy gates (gates.py units + DB kill switch), engine.entry_guard / risk_multiplier / decide, /api/strategy/gate + /stats
import asyncio
import copy
import os
import sys
import time
import uuid

import pytest
import requests
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402
import gates  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
PROTECTED = "6ad0c885f4fa421663189000"
mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def run_gates(coro_fn):
    async def wrap():
        old = gates.db
        gates.db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        try:
            return await coro_fn()
        finally:
            gates.db = old
    return asyncio.run(wrap())


def now_ms():
    return int(time.time() * 1000)


def cleanup(uid):
    assert uid != PROTECTED
    for c in ("bots", "bot_trades", "bot_pnl", "gates", "audit_log", "bot_logs", "trade_logs"):
        mdb[c].delete_many({"user_id": uid})


# --- pure units
class TestGateUnits:
    @pytest.mark.parametrize("pf,dd,n,exp", [(2.0, 5, 60, "OPEN"), (1.05, 5, 60, "WARNING"), (0.9, 5, 60, "RESTRICTED"),
                                             (2.0, 9, 60, "RESTRICTED"), (0.7, 5, 60, "DISABLED"), (2.0, 16, 60, "DISABLED"), (0.5, 20, 10, "OPEN")])
    def test_classify(self, pf, dd, n, exp):
        assert gates.classify(pf, dd, n) == exp

    def test_next_state_hysteresis(self):
        s, st = gates.next_state("OPEN", "RESTRICTED", 0.9, 5, 60, {"fail": 0})
        assert s == "WARNING" and st["fail"] == 1
        s, _ = gates.next_state("OPEN", "RESTRICTED", 0.9, 5, 60, {"fail": 1})
        assert s == "WARNING"
        s, _ = gates.next_state("OPEN", "RESTRICTED", 0.9, 5, 60, {"fail": 2})
        assert s == "RESTRICTED"
        s, st = gates.next_state("RESTRICTED", "OPEN", 2.0, 5, 120, {"pass": 5})
        assert s == "RESTRICTED" and st["pass"] == 6
        s, _ = gates.next_state("RESTRICTED", "OPEN", 2.0, 5, 120, {"pass": 6})
        assert s == "OPEN"
        s, _ = gates.next_state("RESTRICTED", "OPEN", 1.3, 5, 120, {"pass": 6})
        assert s == "RESTRICTED"
        s, _ = gates.next_state("RESTRICTED", "OPEN", 2.0, 5, 99, {"pass": 6})
        assert s == "RESTRICTED"
        s, _ = gates.next_state("DISABLED", "OPEN", 3.0, 1, 500, {"pass": 100})
        assert s == "DISABLED"

    def test_pf_dd(self):
        assert gates.profit_factor([100, 100, -100]) == 2
        assert gates.profit_factor([]) == 0
        assert gates.profit_factor([1, 2]) == float("inf")
        assert gates.max_drawdown_pct([100, 110, 99, 120]) == 10

    @pytest.mark.parametrize("pf,inf,exp", [(0.8, False, 0.25), (1.1, False, 0.5), (1.3, False, 1.0), (2.0, False, 1.5), (None, True, 1.0)])
    def test_risk_multiplier(self, pf, inf, exp):
        assert engine.risk_multiplier(pf, inf) == exp


def candles(n=300, g=1.002, base=100.0, last_vol=30.0):
    cs = []
    for k in range(n):
        c = base * g ** k
        cs.append({"t": k, "o": c / g, "h": c * 1.0005, "l": c / g * 0.9995, "c": c, "v": 10.0})
    cs[-2]["v"] = last_vol
    return cs


@pytest.fixture
def snap():
    saved = copy.deepcopy(engine.SNAPSHOT.get("markets"))
    yield engine.SNAPSHOT
    engine.SNAPSHOT["markets"] = saved


def mk(cs, rsi):
    return {"price": cs[-1]["c"], "candles": cs, "rsi": rsi}


class TestEntryGuard:
    D = {"action": "BUY", "score": 80, "confidence": 0.9}

    def test_all_pass(self, snap):
        cs = candles()
        snap["markets"] = {"BTC": mk(cs, 60.0), "ETH": mk(cs, 60.0)}
        assert engine.entry_guard({}, "BTC", self.D) is None
        assert engine.entry_guard({}, "ETH", self.D) is None

    def test_rsi_high(self, snap):
        cs = candles()
        snap["markets"] = {"BTC": mk(cs, 75.0)}
        assert "RSI" in engine.entry_guard({}, "BTC", self.D)

    def test_adx_low(self, snap):
        flat = [{"t": k, "o": 100 + (k % 2), "h": 101.5, "l": 99.5, "c": 100 + ((k + 1) % 2), "v": 10.0} for k in range(300)]
        flat[-2]["v"] = 30.0
        snap["markets"] = {"BTC": mk(flat, 60.0)}
        assert "ADX" in engine.entry_guard({}, "BTC", self.D)

    def test_volume_score_conf(self, snap):
        cs = candles(last_vol=10.0)
        snap["markets"] = {"BTC": mk(cs, 60.0)}
        r = engine.entry_guard({}, "BTC", {"action": "BUY", "score": 60, "confidence": 0.6})
        assert "volume" in r and "score 60" in r and "confidence" in r

    def test_eth_btc_below_ema200(self, snap):
        cs = candles()
        down = candles(g=0.998)
        snap["markets"] = {"BTC": mk(down, 40.0), "ETH": mk(cs, 60.0)}
        r = engine.entry_guard({}, "ETH", self.D)
        assert "BTC below EMA200" in r and "BTC RSI" in r and "BTC trend negative" in r
        # BTC-master filters only apply to alts; guard itself does not check direction (ADX = strength only)
        assert engine.entry_guard({}, "BTC", self.D) is None

    def test_decide_filters_buy(self, snap, monkeypatch):
        cs = candles()
        snap["markets"] = {"BTC": mk(cs, 75.0)}
        monkeypatch.setattr(engine, "luchii_signal", lambda b, a: {"action": "BUY", "score": 80, "confidence": 0.9, "reason": "x"})
        d = engine.decide({"strategy": "luchii003"}, "BTC")
        assert d["action"] == "HOLD" and "entry filtered" in d["reason"] and "RSI" in d["reason"]
        snap["markets"] = {"BTC": mk(cs, 60.0)}
        assert engine.decide({"strategy": "luchii003"}, "BTC")["action"] == "BUY"
        monkeypatch.setattr(engine, "signal", lambda b, a: {"action": "BUY", "score": 80, "confidence": 0.9, "reason": "x"})
        snap["markets"] = {"BTC": mk(cs, 75.0)}
        assert engine.decide({"strategy": "aggressive"}, "BTC")["action"] == "BUY"  # owner-chosen aggressive bypasses hard filters


# --- DB level kill switch
class TestKillSwitch:
    def test_three_losses_and_daily_loss(self):
        uid = "TEST_ks_" + uuid.uuid4().hex[:8]
        try:
            t = now_ms()
            mdb.bot_trades.insert_many([{"user_id": uid, "side": "SELL", "pnl": -1 if k else 0, "ts": t - k * 1000} for k in range(3)])
            assert run_gates(lambda: gates.kill_switch(uid)) .startswith("3 consecutive losses")
            mdb.bot_trades.delete_many({"user_id": uid})
            mdb.bot_pnl.insert_many([{"user_id": uid, "ts": t - 3600_000, "equity": 100.0}, {"user_id": uid, "ts": t, "equity": 96.0}])
            r = run_gates(lambda: gates.kill_switch(uid))
            assert r and r.startswith("daily loss 4.0%"), r
            mdb.bot_pnl.delete_many({"user_id": uid})
            mdb.bot_pnl.insert_many([{"user_id": uid, "ts": t - 3 * 86400_000, "equity": 100.0}, {"user_id": uid, "ts": t - 2 * 86400_000, "equity": 90.0},
                                     {"user_id": uid, "ts": t - 3600_000, "equity": 90.0}, {"user_id": uid, "ts": t, "equity": 90.0}])
            r = run_gates(lambda: gates.kill_switch(uid))
            assert r and "weekly drawdown" in r, r
            mdb.bot_pnl.delete_many({"user_id": uid})
            assert run_gates(lambda: gates.kill_switch(uid)) is None
        finally:
            cleanup(uid)


# --- API gate state machine
@pytest.fixture(scope="module")
def user():
    email = f"test_it11_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": "TestPass123!", "name": "TEST it11"}, timeout=20)
    assert r.status_code in (200, 201), r.text
    d = r.json()
    uid = d["user"]["id"]
    yield {"id": uid, "h": {"Authorization": f"Bearer {d['token']}"}}
    cleanup(uid)
    from bson import ObjectId
    mdb.users.delete_one({"_id": ObjectId(uid)}) if ObjectId.is_valid(uid) else None
    mdb.users.delete_one({"id": uid})
    mdb.users.delete_one({"email": email})


class TestGateAPI:
    def test_fresh_user_open(self, user):
        r = requests.get(f"{BASE_URL}/api/strategy/gate", headers=user["h"], timeout=20)
        assert r.status_code == 200, r.text
        g = r.json()
        assert g["state"] == "OPEN" and "not enough trades" in g["reason"] and "_id" not in g
        s = requests.get(f"{BASE_URL}/api/strategy/stats", headers=user["h"], timeout=20).json()
        assert {"BTC", "ETH", "SOL"} <= set(s["by_asset"]) and s["overall"]["trades"] == 0

    def test_unauth(self):
        assert requests.get(f"{BASE_URL}/api/strategy/gate", timeout=20).status_code in (401, 403)
        assert requests.get(f"{BASE_URL}/api/strategy/stats", timeout=20).status_code in (401, 403)

    def test_restricted_after_three_evals(self, user):
        uid, t = user["id"], now_ms()
        assets = ["BTC", "ETH", "SOL"]
        trades = [{"user_id": uid, "side": "SELL", "strategy": "luchii003", "asset": assets[k % 3], "pnl": 0.9 if k % 2 == 0 else -1.0, "ts": t - k * 60_000} for k in range(60)]
        mdb.bot_trades.insert_many(trades)
        mdb.bot_pnl.insert_many([{"user_id": uid, "ts": t - k * 60_000, "equity": 1000.0} for k in range(5)])
        states = []
        for _ in range(3):
            g = requests.get(f"{BASE_URL}/api/strategy/gate", headers=user["h"], timeout=20).json()
            assert g["raw"] == "RESTRICTED", g
            states.append(g["state"])
        assert states == ["WARNING", "WARNING", "RESTRICTED"], states
        assert g["profit_factor"] == 0.9 and g["trades"] == 60 and len(g["history"]) >= 2
        ev = list(mdb.audit_log.find({"user_id": uid, "event": "strategy_gate_changed"}).sort("ts", 1))
        assert [(e["oldState"], e["newState"]) for e in ev] == [("OPEN", "WARNING"), ("WARNING", "RESTRICTED")]
        s = requests.get(f"{BASE_URL}/api/strategy/stats", headers=user["h"], timeout=20).json()
        assert s["by_strategy"]["luchii003"]["trades"] == 60 and {"BTC", "ETH", "SOL"} <= set(s["by_asset"])
        assert isinstance(s["overall"]["win_rate"], (int, float)) and s["overall"]["win_rate"] == 50.0
        assert isinstance(s["overall"]["avg_gain"], (int, float)) and s["overall"]["avg_gain"] == 0.9
        # stays RESTRICTED (sticky until 7 passes)
        g = requests.get(f"{BASE_URL}/api/strategy/gate", headers=user["h"], timeout=20).json()
        assert g["state"] == "RESTRICTED"
