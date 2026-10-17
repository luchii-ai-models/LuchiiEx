# Iteration 17: break-even+fees lock, maker (post-only) entries w/ market fallback (fakes only), maker_entries config, 24h logs API.
import asyncio
import os
import uuid

import pytest
import requests

import engine
import exchange_service

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
CANDLES = [{"t": k, "o": 100, "h": 100, "l": 100, "c": 100.0, "v": 1} for k in range(40)]


def _bot(peak_mult, **kw):
    return {**engine.DEFAULT_BOT, "user_id": "u", "strategy": "aggressive", "take_profit_pct": 0.6, "stop_loss_pct": 0.4,
            "fee_rate": 0.001, "alt_positions": {"SOL": {"qty": 1.0, "entry": 100.0, "peak": 100.0 * peak_mult, "tp_taken": False}}, **kw}


# --- engine.signal break-even lock ---
class TestBreakEvenLock:
    def test_lock_fires_at_threshold(self):
        engine.SNAPSHOT["markets"] = {"SOL": {"price": 100 * 1.0022, "candles": CANDLES, "rsi": 50}}
        d = engine.signal(_bot(1.0032), "SOL")
        assert d["action"] == "SELL" and "BREAK-EVEN+FEES" in d["reason"]

    def test_lock_fires_below_floor(self):
        engine.SNAPSHOT["markets"] = {"SOL": {"price": 100 * 1.0010, "candles": CANDLES, "rsi": 50}}
        d = engine.signal(_bot(1.0035), "SOL")
        assert d["action"] == "SELL" and "BREAK-EVEN+FEES" in d["reason"]

    def test_hold_above_floor(self):
        engine.SNAPSHOT["markets"] = {"SOL": {"price": 100 * 1.0025, "candles": CANDLES, "rsi": 50}}
        assert engine.signal(_bot(1.0032), "SOL")["action"] == "HOLD"

    def test_no_lock_if_peak_too_small(self):
        # peak +0.30% < fee(0.22)+0.1 -> lock not armed; price +0.1% not a trailing hit either (drop 0.2% < 0.4%)
        engine.SNAPSHOT["markets"] = {"SOL": {"price": 100 * 1.001, "candles": CANDLES, "rsi": 50}}
        d = engine.signal(_bot(1.003), "SOL")
        assert "BREAK-EVEN" not in d["reason"]

    def test_protective_stop_includes_floor(self):
        bot = _bot(1.0)
        ps = engine.protective_stop(bot, "SOL", {"stop_price": 0.0, "peak_price": 100.33}, 100.0)  # note: exactly 100.32 misses due to float (0.31999..<0.32), no epsilon
        assert abs(ps - 100.22) < 1e-6
        ps2 = engine.protective_stop(bot, "SOL", {"stop_price": 0.0, "peak_price": 100.2}, 100.0)
        assert ps2 < 100.0  # not armed below fee+0.1%


# --- exchange_service maker path with fake ccxt ---
class FakeEx:
    def __init__(self, fill_after=None, ob_raises=False, cancel_raises=False):
        self.fill_after, self.ob_raises, self.cancel_raises = fill_after, ob_raises, cancel_raises
        self.calls, self.polls, self.cancelled = [], 0, False

    def amount_to_precision(self, s, a):
        return f"{a:.6f}"

    def price_to_precision(self, s, p):
        return f"{p:.2f}"

    async def fetch_order_book(self, s, n=5):
        if self.ob_raises:
            raise RuntimeError("ob down")
        return {"bids": [[99.9, 1]], "asks": [[100.1, 1]]}

    async def create_order(self, s, typ, side, amt, px=None, params=None):
        self.calls.append((typ, side, amt, px, params))
        if typ == "market":
            return {"id": "m1", "average": 100.0, "filled": amt, "status": "closed"}
        return {"id": "L1", "status": "open", "filled": 0}

    async def fetch_order(self, oid, s, params=None):
        if oid == "m1":
            return {"id": "m1", "average": 100.0}
        self.polls += 1
        if self.fill_after is not None and self.polls >= self.fill_after:
            return {"id": "L1", "status": "closed", "filled": 0.5, "average": 99.9}
        return {"id": "L1", "status": "open", "filled": 0}

    async def cancel_order(self, oid, s, params=None):
        self.cancelled = True
        if self.cancel_raises:
            raise RuntimeError("order already filled")


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    real = asyncio.sleep

    async def _s(t):
        await real(0)
    monkeypatch.setattr(exchange_service.asyncio, "sleep", _s)


class TestMakerEntries:
    def test_maker_fill_no_market(self):
        ex = FakeEx(fill_after=1)
        o = asyncio.run(exchange_service.market_order(ex, "SOL/USDT", "BUY", 50, 0, 100.0, maker=True, wait_s=5))
        assert o.get("maker") is True
        assert [c[0] for c in ex.calls] == ["limit"]
        assert ex.calls[0][4] == {"postOnly": True} and ex.calls[0][3] == 99.9

    def test_maker_timeout_cancels_then_market(self):
        ex = FakeEx(fill_after=None)
        o = asyncio.run(exchange_service.market_order(ex, "SOL/USDT", "BUY", 50, 0, 100.0, maker=True, wait_s=0.05))
        assert ex.cancelled
        assert [c[0] for c in ex.calls] == ["limit", "market"]
        assert not o.get("maker")

    def test_orderbook_error_falls_back(self):
        ex = FakeEx(ob_raises=True)
        o = asyncio.run(exchange_service.market_order(ex, "SOL/USDT", "BUY", 50, 0, 100.0, maker=True, wait_s=5))
        assert [c[0] for c in ex.calls] == ["market"] and o["id"] == "m1"

    def test_maker_false_market_only(self):
        ex = FakeEx()
        asyncio.run(exchange_service.market_order(ex, "SOL/USDT", "BUY", 50, 0, 100.0, maker=False))
        assert [c[0] for c in ex.calls] == ["market"]


# --- API: maker_entries config + 24h logs ---
@pytest.fixture(scope="module")
def token():
    s = requests.Session()
    email = f"test_it17_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": "Test@12345", "name": "TEST it17"})
    if r.status_code not in (200, 201):
        pytest.skip(f"register failed {r.status_code} {r.text[:200]}")
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@gravia.io", "password": "Gravia@2026"})
    if r.status_code != 200:
        pytest.skip("admin login failed")
    return r.json()["token"]


class TestAPI:
    def test_maker_entries_toggle(self, token):
        h = {"Authorization": f"Bearer {token}"}
        r = requests.post(f"{BASE_URL}/api/bot/config", json={"maker_entries": False}, headers=h)
        assert r.status_code == 200 and r.json()["maker_entries"] is False
        assert requests.get(f"{BASE_URL}/api/bot/state", headers=h).json()["maker_entries"] is False
        r = requests.post(f"{BASE_URL}/api/bot/config", json={"maker_entries": True}, headers=h)
        assert r.json()["maker_entries"] is True
        assert requests.get(f"{BASE_URL}/api/bot/state", headers=h).json()["maker_entries"] is True

    def test_maker_entries_invalid_type(self, token):
        r = requests.post(f"{BASE_URL}/api/bot/config", json={"maker_entries": "notabool"}, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 422

    def test_admin_state_maker(self, admin_token):
        st = requests.get(f"{BASE_URL}/api/bot/state", headers={"Authorization": f"Bearer {admin_token}"}).json()
        assert "status" in st

    def test_logs_day_auth(self):
        assert requests.get(f"{BASE_URL}/api/bot/logs/day").status_code == 401

    def test_logs_day_admin(self, admin_token):
        import time
        r = requests.get(f"{BASE_URL}/api/bot/logs/day", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) and len(data) <= 3000
        cutoff = (time.time() - 24 * 3600 - 60) * 1000
        assert all(x["ts"] >= cutoff and "_id" not in x and "user_id" not in x for x in data)
        if len(data) > 1:
            assert data[0]["ts"] >= data[-1]["ts"]
