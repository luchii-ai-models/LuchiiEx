"""Iteration 3 — Auto-fund config + auto_fund engine helper + resolve_symbol + exchange order guards."""
import os
import uuid
import asyncio

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://luchii-trade-bot.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers["Content-Type"] = "application/json"
    yield s
    s.close()


@pytest.fixture(scope="module")
def user_token(session):
    email = f"TEST_autofund_{uuid.uuid4().hex[:8]}@example.com"
    r = session.post(f"{BASE_URL}/api/auth/register",
                     json={"email": email, "password": "Test@12345", "name": "AutoFund Tester"})
    assert r.status_code in (200, 201), r.text
    tok = r.json()["token"]
    session.headers["Authorization"] = f"Bearer {tok}"
    yield tok
    # cleanup
    session.headers.pop("Authorization", None)
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        async def _cleanup():
            client = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = client[os.environ["DB_NAME"]]
            u = await db.users.find_one({"email": email})
            if u:
                await db.bots.delete_many({"user_id": u["id"]})
                await db.bot_logs.delete_many({"user_id": u["id"]})
                await db.bot_trades.delete_many({"user_id": u["id"]})
                await db.bot_pnl.delete_many({"user_id": u["id"]})
                await db.users.delete_one({"id": u["id"]})
            client.close()
        asyncio.get_event_loop().run_until_complete(_cleanup())
    except Exception as e:
        print("cleanup failed:", e)


class TestAutoFundConfig:
    """GET /api/bot/state default + POST /api/bot/config auto_fund"""

    def test_bot_state_auto_fund_default_true(self, session, user_token):
        r = session.get(f"{BASE_URL}/api/bot/state")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "auto_fund" in data, f"auto_fund missing from bot state: {list(data.keys())}"
        assert data["auto_fund"] is True

    def test_bot_config_disable_auto_fund(self, session, user_token):
        r = session.post(f"{BASE_URL}/api/bot/config", json={"auto_fund": False})
        assert r.status_code == 200, r.text
        assert r.json()["auto_fund"] is False
        # GET verifies persistence
        s = session.get(f"{BASE_URL}/api/bot/state").json()
        assert s["auto_fund"] is False

    def test_bot_config_enable_auto_fund(self, session, user_token):
        r = session.post(f"{BASE_URL}/api/bot/config", json={"auto_fund": True})
        assert r.status_code == 200
        assert r.json()["auto_fund"] is True
        s = session.get(f"{BASE_URL}/api/bot/state").json()
        assert s["auto_fund"] is True


class TestExchangeOrdersGuarded:
    """Without exchange, /api/exchange/order and /api/exchange/test-order must 400"""

    def test_test_order_no_exchange(self, session, user_token):
        r = session.post(f"{BASE_URL}/api/exchange/test-order")
        assert r.status_code == 400
        assert "No exchange connected" in (r.json().get("detail") or r.text)

    def test_live_order_no_exchange(self, session, user_token):
        r = session.post(f"{BASE_URL}/api/exchange/order",
                         json={"side": "BUY", "usd": 10})
        assert r.status_code == 400
        assert "No exchange connected" in (r.json().get("detail") or r.text)


class TestStartGuarded:
    """Starting the bot in live mode without exchange must be rejected."""

    def test_start_without_exchange(self, session, user_token):
        r = session.post(f"{BASE_URL}/api/bot/start")
        assert r.status_code == 400
        assert "Live mode needs a connected exchange" in (r.json().get("detail") or r.text)


# ---- Unit tests on exchange_service.resolve_symbol & ensure_funds using a fake ccxt-like object ----

class FakeEx:
    """Mimics the subset of ccxt.async_support client used by resolve_symbol / ensure_funds."""

    def __init__(self, markets, balances, funding=None):
        self.markets = markets
        self._bal = balances  # total/free dict per currency, used by fetch_balance()
        self._fund = funding or {}
        self.transfers = []

    async def load_markets(self):
        return self.markets

    async def fetch_balance(self, params=None):
        if params and params.get("type"):
            return self._fund
        return self._bal

    def currency_to_precision(self, cur, amt):
        return f"{amt:.8f}"

    async def transfer(self, cur, amt, fr, to):
        self.transfers.append((cur, float(amt), fr, to))
        return {"id": "t1"}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_resolve_symbol_prefers_quote_with_balance():
    from exchange_service import resolve_symbol
    markets = {"BTC/USDT": {}, "BTC/USDC": {}}
    bal = {"total": {"USDT": 0, "USDC": 0}}
    fund = {"total": {"USDC": 50}}
    ex = FakeEx(markets, bal, fund)
    sym = _run(resolve_symbol(ex, "okx"))
    assert sym == "BTC/USDC"


def test_resolve_symbol_no_btc_pair_falls_back():
    from exchange_service import resolve_symbol
    ex = FakeEx({"ETH/USDT": {}}, {"total": {}}, {})
    sym = _run(resolve_symbol(ex, "okx"))
    assert sym == "BTC/USDT"


def test_ensure_funds_moves_when_insufficient():
    from exchange_service import ensure_funds
    bal = {"USDT": {"free": 2}}
    fund = {"USDT": {"free": 40}}
    ex = FakeEx({}, bal, fund)
    moved = _run(ensure_funds(ex, "okx", "USDT", 10))
    assert moved == pytest.approx(40.0)
    assert len(ex.transfers) == 1
    cur, amt, fr, to = ex.transfers[0]
    assert cur == "USDT" and fr == "funding" and to == "trading"


def test_ensure_funds_noop_when_enough():
    from exchange_service import ensure_funds
    bal = {"USDT": {"free": 100}}
    fund = {"USDT": {"free": 50}}
    ex = FakeEx({}, bal, fund)
    moved = _run(ensure_funds(ex, "okx", "USDT", 10))
    assert moved == 0.0
    assert ex.transfers == []


def test_ensure_funds_noop_for_single_account_exchange():
    from exchange_service import ensure_funds
    ex = FakeEx({}, {"USDT": {"free": 0}}, {"USDT": {"free": 999}})
    moved = _run(ensure_funds(ex, "coinbase", "USDT", 10))
    assert moved == 0.0
    assert ex.transfers == []
