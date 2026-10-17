# Iteration 13: Phase 2/3 - multi-agent voting, audit.validate, day walk-forward, Monte Carlo ruin, new strategy APIs
import os
import sys
import time
import uuid

import pytest
import requests
from bson import ObjectId
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agents  # noqa: E402
import audit  # noqa: E402
import luchii_backtest as lb  # noqa: E402
import strategy  # noqa: E402
from test_iteration10_luchii003 import uptrend, flat  # noqa: E402

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
PROTECTED = "6ad0c885f4fa421663189000"
ADMIN = {"email": "admin@gravia.io", "password": "Gravia@2026"}


def now_ms():
    return int(time.time() * 1000)


def new_user(tag):
    email = f"test_it13_{tag}_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": "TestPass123!", "name": "TEST it13"}, timeout=20)
    assert r.status_code in (200, 201), r.text
    d = r.json()
    return {"id": d["user"]["id"], "email": email, "h": {"Authorization": f"Bearer {d['token']}"}}


def drop_user(u):
    uid = u["id"]
    assert uid != PROTECTED
    for c in ("bots", "bot_trades", "bot_pnl", "gates", "audit_log", "bot_logs", "symbol_gates", "trade_audit", "backtests"):
        mdb[c].delete_many({"user_id": uid})
    if ObjectId.is_valid(uid):
        mdb.users.delete_one({"_id": ObjectId(uid)})
    mdb.users.delete_one({"email": u["email"]})


@pytest.fixture
def user():
    u = new_user("u")
    yield u
    drop_user(u)


@pytest.fixture(scope="module")
def admin_h():
    r = requests.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"admin login failed {r.status_code}")
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- agents.vote units
FULL = {"price": 110.0, "rsi": 62.0, "trend_pct": 0.3, "ema20": 108.0, "ema50": 105.0, "ema200": 100.0, "adx": 35.0,
        "atr": 1.5, "atr_ma": 1.0, "volume_ratio": 2.2, "structure": "HIGH", "regime": "TREND_BULL"}


class TestAgentsVote:
    def test_shape_and_ranges(self):
        v = agents.vote(dict(FULL))
        assert set(v["scores"]) == set(agents.AGENTS)
        assert all(0 <= s <= 100 and isinstance(s, int) for s in v["scores"].values())
        assert isinstance(v["final"], int) and v["winner"] in agents.AGENTS

    def test_chop_never_allowed(self):
        v = agents.vote({**FULL, "regime": "CHOP"})
        assert v["final"] >= 75, v
        assert v["allowed"] is False and "not allowed in CHOP" in v["reason"]

    def test_trend_bull_allowed(self):
        v = agents.vote(dict(FULL))
        assert v["winner"] in ("trend", "momentum", "breakout") and v["final"] >= 75, v
        assert v["allowed"] is True

    def test_breakout_regime_breakout_score_overrides_winner(self):
        # trend agent wins, but breakout >= 75 -> allowed in BREAKOUT
        i = {**FULL, "regime": "BREAKOUT", "adx": 40, "rsi": 65, "volume_ratio": 2.2, "atr": 1.3}
        v = agents.vote(i, {"trend": 2.0})
        assert v["scores"]["breakout"] >= 75, v
        assert v["winner"] == "trend", v
        if v["final"] >= 75:
            assert v["allowed"] is True

    def test_nan_warming_up(self):
        v = agents.vote({**FULL, "rsi": float("nan")})
        assert v["allowed"] is False and v["reason"] == "agents warming up"
        assert agents.vote({**FULL, "atr_ma": None})["allowed"] is False

    def test_weights_from_audit(self):
        assert agents.weights_from_audit([]) == {a: 1.0 for a in agents.AGENTS}
        few = [{"winner_agent": "trend", "net_pnl": 1.0}] * 9
        assert agents.weights_from_audit(few)["trend"] == 1.0
        wins = [{"winner_agent": "trend", "net_pnl": 1.0}] * 10
        assert agents.weights_from_audit(wins)["trend"] == 2.0
        loss = [{"winner_agent": "momentum", "net_pnl": -1.0}] * 10
        w = agents.weights_from_audit(loss)
        assert w["momentum"] == 0.25 and w["trend"] == 1.0


# --- audit.validate units
GOOD = {"direction": "LONG", "quantity": 1.0, "position_usd": 100.0, "entry_time": 1000, "exit_time": 2000, "trade_result": "WIN"}


class TestAuditValidate:
    def test_good(self):
        audit.validate(dict(GOOD))
        audit.validate({**GOOD, "exit_time": None, "trade_result": None})

    @pytest.mark.parametrize("bad", [{"direction": "X"}, {"quantity": 0}, {"position_usd": 0}, {"exit_time": 500}, {"trade_result": "MEH"}])
    def test_bad(self, bad):
        with pytest.raises(AssertionError):
            audit.validate({**GOOD, **bad})


# --- luchii_backtest units
def synth(days=32, start=1_700_000_000_000):
    out, p = [], 100.0
    for k in range(days * 1440):
        p *= 1.00002 if (k // 600) % 2 == 0 else 0.99998
        out.append({"t": start + k * 60_000, "o": p, "h": p * 1.0004, "l": p * 0.9996, "c": p, "v": 10.0 + (k % 7)})
    return out


W5 = [{"trades": 10, "profit_factor": 1.5, "win_rate": 50, "expectancy": 1, "max_drawdown": 5}] * 5


class TestBacktestUnits:
    def test_day_windows_count(self):
        ws = list(lb.day_windows(synth(32), 20, 5, 5, 1))
        assert len(ws) == 3
        tr, va, te, meta = ws[0]
        assert len(tr) == 20 * 1440
        assert meta["validate"][0] == meta["train"][1]

    def test_approve_rules(self):
        assert lb.approve_walk_forward([])["deployment"] == "REJECTED"
        assert lb.approve_walk_forward([{**W5[0], "trades": 0}])["deployment"] == "REJECTED"
        low = [{**w, "profit_factor": 1.05} for w in W5]
        r = lb.approve_walk_forward(low)
        assert r["deployment"] == "REJECTED" and r["checks"]["avg_pf>=1.10"] is False
        ok = lb.approve_walk_forward(W5)
        assert ok["deployment"] == "APPROVED", ok

    def test_mc_insufficient(self):
        assert lb.monte_carlo_ruin([])["deployment"] == "INSUFFICIENT_DATA"

    def test_mc_positive(self):
        pnls = [5.0] * 35 + [-3.0] * 15
        r = lb.monte_carlo_ruin(pnls)
        assert isinstance(r["probability_of_ruin"], (int, float)) and r["simulations"] == 5000
        assert "drawdown_95" in r and isinstance(r["worst_loss_streak"], int)
        assert r["deployment"] == "APPROVED", r
        assert lb.monte_carlo_ruin(pnls) == r  # deterministic seed

    def test_mc_negative(self):
        r = lb.monte_carlo_ruin([-1.0] * 20)
        assert r["deployment"] == "REJECTED" and r["checks"]["expectancy>0"] is False

    def test_walk_forward_days_shape(self):
        cs = synth(12)
        r = lb.walk_forward_days(cs, None, 6, 2, 2, step_days=2, grid={"rsi_min": [55], "atr_stop": [2.5], "trend_min": [0.08], "volume_min": [1.5]})
        assert isinstance(r["windows"], list) and isinstance(r["skipped"], int)
        assert r["deployment"] in ("APPROVED", "REJECTED")

    def test_indicator_cache(self):
        cs = synth(1)[:900]
        lb._IND_CACHE.clear()
        t0 = time.time(); a = lb.run_backtest(cs); t1 = time.time() - t0
        t0 = time.time(); b = lb.run_backtest(cs); t2 = time.time() - t0
        assert a["trades"] == b["trades"] and a["net_profit"] == b["net_profit"]
        assert t2 < t1 / 3, (t1, t2)

    def test_run_backtest_restores_rules(self):
        before = (dict(strategy.RULES["entry"]), strategy.RULES["risk"]["atr_stop_mult"])
        lb.run_backtest(synth(1)[:500], {"rsi_min": 52, "atr_stop": 2.0})
        assert (dict(strategy.RULES["entry"]), strategy.RULES["risk"]["atr_stop_mult"]) == before


# --- strategy integration
class TestStrategyAgents:
    def test_uptrend_buy_has_agents(self):
        cs = uptrend()
        i = strategy.indicators(cs, cs[-1]["c"], 70.0)
        d = strategy.evaluate("BTC", i, i, {"position_qty": 0, "entry_price": 0, "peak_price": 0})
        assert d["action"] == "BUY", d["reason"]
        assert d["winner_agent"] in agents.AGENTS and d["agents"]["allowed"] is True

    def test_chop_hold(self):
        cs = flat()
        i = strategy.indicators(cs, 100.0, 50.0)
        d = strategy.evaluate("BTC", i, i, {"position_qty": 0, "entry_price": 0, "peak_price": 0})
        assert d["action"] == "HOLD" and "winner_agent" not in d


# --- API
class TestAPI:
    @pytest.mark.parametrize("m,p", [("get", "/strategy/agents"), ("get", "/strategy/montecarlo"), ("get", "/strategy/walkforward/BTC"), ("post", "/strategy/walkforward/BTC")])
    def test_unauth(self, m, p):
        r = getattr(requests, m)(f"{BASE_URL}/api{p}", timeout=20)
        assert r.status_code in (401, 403)

    def test_agents_fresh(self, user):
        r = requests.get(f"{BASE_URL}/api/strategy/agents", headers=user["h"], timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["min_final"] == 75 and d["allowed"]["CHOP"] == [] and "trend" in d["allowed"]["TREND_BULL"]
        assert d["weights"] == {a: 1.0 for a in agents.AGENTS}
        assert set(d["performance"]) == set(agents.AGENTS)
        assert d["assets"]
        for a, v in d["assets"].items():
            assert {"scores", "final", "winner", "regime", "allowed"} <= set(v), v

    def test_montecarlo_fresh_and_with_trades(self, user):
        r = requests.get(f"{BASE_URL}/api/strategy/montecarlo", headers=user["h"], timeout=20)
        assert r.status_code == 200 and r.json()["deployment"] == "INSUFFICIENT_DATA"
        t = now_ms()
        docs = [{"trade_id": f"it13{uuid.uuid4().hex[:12]}", "user_id": user["id"], "status": "closed", "symbol": "BTC", "direction": "LONG",
                 "entry_time": t - 10000 - k, "exit_time": t - k, "quantity": 1, "position_usd": 100, "net_pnl": (5.0 if k % 3 else -3.0),
                 "trade_result": "WIN" if k % 3 else "LOSS", "winner_agent": "trend"} for k in range(10)]
        mdb.trade_audit.insert_many(docs)
        d = requests.get(f"{BASE_URL}/api/strategy/montecarlo", headers=user["h"], timeout=20).json()
        assert d["simulations"] == 5000 and d["trades"] == 10
        assert isinstance(d["probability_of_ruin"], (int, float)) and d["deployment"] in ("APPROVED", "REJECTED")
        ag = requests.get(f"{BASE_URL}/api/strategy/agents", headers=user["h"], timeout=20).json()
        assert ag["performance"]["trend"]["trades"] == 10 and ag["weights"]["trend"] == 2.0

    def test_walkforward_queue(self, user):
        r = requests.post(f"{BASE_URL}/api/strategy/walkforward/BTC?days=7", headers=user["h"], timeout=20)
        assert r.status_code == 200 and r.json()["status"] == "queued"
        r2 = requests.post(f"{BASE_URL}/api/strategy/walkforward/BTC?days=7", headers=user["h"], timeout=20)
        assert r2.json()["status"] in ("queued", "running")
        g = requests.get(f"{BASE_URL}/api/strategy/walkforward/BTC", headers=user["h"], timeout=20).json()
        assert g["job"]["status"] in ("queued", "running", "done") and "result" in g

    def test_walkforward_invalid_asset(self, user):
        assert requests.post(f"{BASE_URL}/api/strategy/walkforward/DOGE", headers=user["h"], timeout=20).status_code == 422
        assert requests.get(f"{BASE_URL}/api/strategy/walkforward/DOGE", headers=user["h"], timeout=20).status_code == 422

    def test_admin_result_shape(self, admin_h):
        g = requests.get(f"{BASE_URL}/api/strategy/walkforward/BTC", headers=admin_h, timeout=20).json()
        r = g["result"]
        assert r, "admin has no BTC result"
        assert isinstance(r["walk_forward"]["windows"], list)
        assert r["walk_forward"]["deployment"] in ("APPROVED", "REJECTED")
        assert "probability_of_ruin" in r["monte_carlo"] and r["deployment"] in ("APPROVED", "REJECTED")
        assert "_id" not in r and "user_id" not in r
