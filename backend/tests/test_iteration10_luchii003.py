# Iteration 10: LUCHII_003 strategy (strategy.py units, engine wiring, API metrics/config, paper execute)
import asyncio
import math
import os
import sys
import time
import uuid

import pytest
import requests
from bson import ObjectId
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine  # noqa: E402
import strategy  # noqa: E402
from market import SNAPSHOT  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


def run_engine(coro_fn):
    """Run an engine coroutine on a fresh loop with a fresh motor client (motor binds to the first loop)."""
    async def wrap():
        import audit
        import gates
        import solana_dex
        old = engine.db, solana_dex.db, audit.db, gates.db
        engine.db = solana_dex.db = audit.db = gates.db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        try:
            return await coro_fn()
        finally:
            engine.db, solana_dex.db, audit.db, gates.db = old
    return asyncio.run(wrap())

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")


def uptrend(n=300, g=1.001, base=100.0, last_vol=100.0):
    cs = []
    for k in range(n):
        c = base * g ** k
        cs.append({"t": k, "o": c / g, "h": c * 1.0005, "l": c / g * 0.9995, "c": c, "v": 10.0})
    cs[-2]["v"] = last_vol
    return cs


def flat(n=300, p=100.0):
    return [{"t": k, "o": p, "h": p, "l": p, "c": p, "v": 10.0} for k in range(n)]


def get(url, s=None, **kw):
    s = s or requests
    for _ in range(2):
        r = s.get(url, timeout=20, **kw)
        if r.status_code != 429:
            return r
        time.sleep(3)
    return r


# --- strategy.py pure units
class TestStrategyUnits:
    def test_position_size(self):
        assert strategy.position_size(10000, 0.01, 100, 98) == 50
        assert strategy.position_size(1000, 1.0, 100, 99) <= 10
        assert strategy.position_size(10000, 0.01, 100, 100) == 0

    def test_update_stop(self):
        assert strategy.update_stop(100, 101, 0.6, 1, 98) == 100
        assert strategy.update_stop(100, 102, 1.25, 1, 100) >= 100.25
        assert strategy.update_stop(100, 110, 2.5, 1, 100) == 107
        assert strategy.update_stop(100, 110, 5.5, 1, 100) == 108

    def test_classify_regime(self):
        assert strategy.classify_regime(200, 150, 30) in ("TREND_BULL", "BREAKOUT")
        assert strategy.classify_regime(100, 150, 30) == "TREND_BEAR"
        assert strategy.classify_regime(151, 150, 15) == "CHOP"
        assert strategy.classify_regime(150, 150, 30) == "CHOP"

    def test_adx_atr(self):
        a = strategy.adx(uptrend(80))
        assert a is not None and 0 <= a <= 100, a
        cs = [{"h": 11, "l": 9, "c": 10}] * 20
        assert strategy.atr(cs, 14) == 2
        cs2 = [{"h": 10, "l": 10, "c": 10}] * 14 + [{"h": 15, "l": 12, "c": 14}]
        assert abs(strategy.atr(cs2, 14) - 5 / 14) < 1e-9  # TR = max(3, |15-10|, |12-10|) = 5

    def test_uptrend_buy(self):
        cs = uptrend()
        i = strategy.indicators(cs, cs[-1]["c"], 70.0)
        d = strategy.evaluate("BTC", i, i, {"position_qty": 0, "entry_price": 0, "peak_price": 0})
        assert d["action"] == "BUY", (d["reason"], i)
        assert d["score"] >= 80 and d["risk"] > 0 and d["stop"] < cs[-1]["c"] and d["entry_atr"] > 0
        assert i["regime"] in ("TREND_BULL", "BREAKOUT")

    def test_flat_chop(self):
        cs = flat()
        i = strategy.indicators(cs, 100.0, 50.0)
        d = strategy.evaluate("BTC", i, i, {"position_qty": 0, "entry_price": 0, "peak_price": 0})
        assert d["action"] == "HOLD" and "CHOP" in d["reason"], d["reason"]

    def test_eth_btc_master_filter(self):
        cs = uptrend()
        i = strategy.indicators(cs, cs[-1]["c"], 70.0)
        d = strategy.evaluate("ETH", i, {"bull_trend": False}, {"position_qty": 0, "entry_price": 0, "peak_price": 0})
        assert d["action"] == "HOLD" and "BTC master filter" in d["reason"], d["reason"]

    @pytest.mark.parametrize("tiers,gain,frac,tier", [(0, 1.011, 0.25, 1), (1, 1.026, 0.3333, 2), (2, 1.051, 0.5, 3)])
    def test_tiered_tp(self, tiers, gain, frac, tier):
        cs = uptrend()
        price = cs[-1]["c"]
        i = strategy.indicators(cs, price, 70.0)
        entry = price / gain
        pos = {"position_qty": 1.0, "entry_price": entry, "peak_price": price, "stop_price": entry * 0.97, "tiers_done": tiers, "entry_atr": price * 0.001}
        d = strategy.evaluate("BTC", i, i, pos)
        assert d["action"] == "SELL" and abs(d["fraction"] - frac) < 1e-3 and d["tier"] == tier, d

    def test_stop_hit(self):
        cs = uptrend()
        price = cs[-1]["c"]
        i = strategy.indicators(cs, price, 70.0)
        pos = {"position_qty": 1.0, "entry_price": price * 1.01, "peak_price": price * 1.01, "stop_price": price + 1, "tiers_done": 0, "entry_atr": 1}
        d = strategy.evaluate("BTC", i, i, pos)
        assert d["action"] == "SELL" and not d.get("fraction") and "STOP" in d["reason"], d

    def test_nan_warming_up(self):
        cs = uptrend()
        i = strategy.indicators(cs, cs[-1]["c"], 70.0)
        for bad in (None, float("nan")):
            j = {**i, "ema200": bad}
            d = strategy.evaluate("BTC", j, j, {"position_qty": 0, "entry_price": 0, "peak_price": 0})
            assert d["action"] == "HOLD" and "warming up" in d["reason"], d
        short = strategy.indicators(uptrend(100), 100.0, 60.0)
        assert strategy.evaluate("BTC", short, short, {"position_qty": 0, "entry_price": 0, "peak_price": 0})["action"] == "HOLD"


# --- engine wiring
class TestEngineWiring:
    def test_decide_dispatch(self, monkeypatch):
        monkeypatch.setattr(engine, "luchii_signal", lambda b, a: {"src": "luchii"})
        monkeypatch.setattr(engine, "signal", lambda b, a="BTC": {"src": "agg"})
        assert engine.decide({"strategy": "luchii003"}, "BTC")["src"] == "luchii"
        assert engine.decide({}, "BTC")["src"] == "luchii"
        assert engine.decide({"strategy": "aggressive"}, "BTC")["src"] == "agg"

    def test_buy_usd_sell_fraction(self):
        assert abs(engine.buy_usd({**engine.DEFAULT_BOT}, {"risk": 0.01, "stop": 98}, 10000, 100) - 4900) < 0.01
        assert engine.sell_fraction({"fraction": 0.25}) == 0.25
        assert engine.sell_fraction({"partial": True}) == 0.5
        assert engine.sell_fraction({}) == 1.0

    def test_pos_of_fields(self):
        bot = {"position_qty": 1, "entry_price": 100, "stop_price": 97, "tiers_done": 2, "entry_atr": 1.5,
               "alt_positions": {"ETH": {"qty": 2, "entry": 3000, "stop": 2900, "tiers": 1, "atr": 10}}}
        b, e = engine.pos_of(bot, "BTC"), engine.pos_of(bot, "ETH")
        assert (b["stop_price"], b["tiers_done"], b["entry_atr"]) == (97, 2, 1.5)
        assert (e["stop_price"], e["tiers_done"], e["entry_atr"]) == (2900, 1, 10)
        assert engine.pos_fields("BTC", stop=1, tiers=2, atr=3) == {"stop_price": 1, "tiers_done": 2, "entry_atr": 3}
        assert engine.pos_fields("ETH", stop=1, tiers=2, atr=3) == {"alt_positions.ETH.stop": 1, "alt_positions.ETH.tiers": 2, "alt_positions.ETH.atr": 3}


@pytest.fixture(scope="module")
def user():
    mc = MongoClient(os.environ["MONGO_URL"])
    db = mc[os.environ["DB_NAME"]]
    email = f"test_it10_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": "TestPass123!", "name": "TEST it10"})
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


# --- API: config, metrics, snapshot
class TestApi:
    def test_fresh_default_strategy(self, user):
        s, _, _ = user
        d = get(f"{BASE_URL}/api/bot/state", s).json()
        assert d["strategy"] == "luchii003" and d["assets"] == ["BTC", "ETH", "SOL"]

    def test_config_strategy(self, user):
        s, _, _ = user
        r = s.post(f"{BASE_URL}/api/bot/config", json={"strategy": "aggressive"})
        assert r.status_code == 200 and r.json()["strategy"] == "aggressive"
        assert get(f"{BASE_URL}/api/bot/state", s).json()["strategy"] == "aggressive"
        assert s.post(f"{BASE_URL}/api/bot/config", json={"strategy": "foo"}).status_code == 422
        r = s.post(f"{BASE_URL}/api/bot/config", json={"strategy": "luchii003"})
        assert r.json()["strategy"] == "luchii003"

    def test_metrics(self, user):
        s, _, _ = user
        r = get(f"{BASE_URL}/api/strategy/metrics", s)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["strategy"] == "luchii003"
        for a in ("BTC", "ETH", "SOL"):
            m = d["assets"][a]
            assert isinstance(m["score"], (int, float)) and 0 <= m["score"] <= 100, m
            assert m["regime"] in strategy.REGIMES, m
            assert m["action"] in ("BUY", "SELL", "HOLD") and isinstance(m["reason"], str) and m["reason"], m
            for k in ("adx", "rsi", "atr", "price", "trend_pct", "volume_ratio"):
                assert m[k] is None or (isinstance(m[k], (int, float)) and not math.isnan(m[k])), (a, k, m[k])
        print({a: (v["action"], v["score"], v["regime"], v["reason"]) for a, v in d["assets"].items()})

    def test_metrics_unauth(self):
        assert requests.get(f"{BASE_URL}/api/strategy/metrics", timeout=15).status_code in (401, 403)

    def test_snapshot_300(self):
        d = get(f"{BASE_URL}/api/market/snapshot").json()
        for a in ("BTC", "ETH", "SOL"):
            assert len(d["markets"][a]["candles"]) == 300, (a, len(d["markets"][a]["candles"]))

    def test_paper_execute_and_reset(self, user):
        s, db, uid = user
        db.bots.update_one({"user_id": uid}, {"$set": {"mode": "dry", "cash": 10000.0}})
        old = dict(SNAPSHOT)
        cs = uptrend(base=1000.0)
        price = cs[-1]["c"]
        SNAPSHOT["markets"] = {**(SNAPSHOT.get("markets") or {}), "ETH": {"candles": cs, "price": price, "rsi": 70.0}}
        out = {}

        async def flow():
            bot = await engine.db.bots.find_one({"user_id": uid})
            await engine.execute(bot, {"action": "BUY", "risk": 0.01, "stop": price * 0.98, "entry_atr": 1.23, "reason": "[ETH] test buy", "confidence": 0.9}, "ETH")
            out["buy"] = await engine.db.bots.find_one({"user_id": uid})
            await engine.execute(out["buy"], {"action": "SELL", "fraction": 0.25, "tier": 1, "stop": price, "reason": "[ETH] TP1", "confidence": 0.9}, "ETH")
            out["sell"] = await engine.db.bots.find_one({"user_id": uid})
        try:
            run_engine(flow)
        finally:
            SNAPSHOT.clear(); SNAPSHOT.update(old)
        e = out["buy"]["alt_positions"]["ETH"]
        exp_qty = round(round(strategy.position_size(10000, 0.01, price, price * 0.98) * price * 0.98, 2) / price, 6)
        assert abs(e["qty"] - exp_qty) < 1e-6 and abs(e["stop"] - price * 0.98) < 1e-6 and e["tiers"] == 0 and e["atr"] == 1.23, e
        e2 = out["sell"]["alt_positions"]["ETH"]
        assert abs(e2["qty"] - round(exp_qty * 0.75, 8)) < 1e-6 and e2["tiers"] == 1 and e2["entry"] == price, e2
        assert out["sell"]["trades_count"] == 2
        # BTC root fields via direct set, then reset clears all
        db.bots.update_one({"user_id": uid}, {"$set": {"mode": "live", "stop_price": 5.0, "tiers_done": 2, "entry_atr": 9.0}})
        assert s.post(f"{BASE_URL}/api/bot/reset").status_code == 200
        d = get(f"{BASE_URL}/api/bot/state", s).json()
        assert d["alt_positions"] == {} and d["stop_price"] == 0 and d["tiers_done"] == 0 and d["entry_atr"] == 0, d

    def test_paper_btc_root_fields(self, user):
        s, db, uid = user
        db.bots.update_one({"user_id": uid}, {"$set": {"mode": "dry", "cash": 10000.0}})
        old = dict(SNAPSHOT)
        cs = uptrend(base=90000.0)
        price = cs[-1]["c"]
        SNAPSHOT["markets"] = {**(SNAPSHOT.get("markets") or {}), "BTC": {"candles": cs, "price": price, "rsi": 70.0}}
        out = {}

        async def flow():
            bot = await engine.db.bots.find_one({"user_id": uid})
            await engine.execute(bot, {"action": "BUY", "risk": 0.01, "stop": price * 0.98, "entry_atr": 55.5, "reason": "[BTC] test buy", "confidence": 0.9}, "BTC")
            out["buy"] = await engine.db.bots.find_one({"user_id": uid})
            await engine.execute(out["buy"], {"action": "SELL", "fraction": 0.25, "tier": 1, "stop": price, "reason": "[BTC] TP1", "confidence": 0.9}, "BTC")
            out["sell"] = await engine.db.bots.find_one({"user_id": uid})
            await asyncio.sleep(1)
        try:
            run_engine(flow)
        finally:
            SNAPSHOT.clear(); SNAPSHOT.update(old)
            db.bots.update_one({"user_id": uid}, {"$set": {"mode": "live"}})
        b = out["buy"]
        assert abs(b["stop_price"] - price * 0.98) < 1e-6 and b["tiers_done"] == 0 and b["entry_atr"] == 55.5 and b["position_qty"] > 0, b
        q = b["position_qty"]
        sb = out["sell"]
        assert abs(sb["position_qty"] - round(q * 0.75, 8)) < 1e-8 and sb["tiers_done"] == 1 and sb["stop_price"] == price, sb
        assert s.post(f"{BASE_URL}/api/bot/reset").status_code == 200
        d = get(f"{BASE_URL}/api/bot/state", s).json()
        assert d["position_qty"] == 0 and d["stop_price"] == 0 and d["tiers_done"] == 0 and d["entry_atr"] == 0
