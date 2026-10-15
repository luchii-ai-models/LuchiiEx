"""
Iteration 5: Real Starting Balance (start_equity) tests.

Covers:
- GET /api/bot/state includes start_equity (null for a new TEST_ user without exchange)
- GET /api/bot/pnl: when start_ts not set, returns all points; when set, filters older points
- Unit test engine.refresh_live_equity with mocked deps: sets start_equity once; keeps on subsequent call
- POST /api/bot/reset sets start_equity to current live_equity (null if none)
- /api/exchange/disconnect clears start_equity / start_ts
"""
import asyncio
import os
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


# ------------ fixtures ------------

@pytest.fixture(scope="module")
def new_user():
    """Register a fresh TEST_ user; return (token, user, email). Cleanup afterward."""
    email = f"TEST_startequity_{uuid.uuid4().hex[:8]}@example.com"
    pw = "testpass123"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"email": email, "password": pw, "name": "TEST_StartEquity"})
    assert r.status_code == 200, r.text
    data = r.json()
    yield data["token"], data["user"], email
    # cleanup
    try:
        from pymongo import MongoClient
        c = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        u = c.users.find_one({"email": email})
        if u:
            uid = str(u["_id"])
            c.bots.delete_many({"user_id": uid})
            c.bot_pnl.delete_many({"user_id": uid})
            c.bot_logs.delete_many({"user_id": uid})
            c.bot_trades.delete_many({"user_id": uid})
            c.exchange_credentials.delete_many({"user_id": uid})
            c.users.delete_one({"_id": u["_id"]})
    except Exception as e:
        print("cleanup warning:", e)


def _h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ------------ API tests ------------

class TestBotStateStartEquity:
    def test_state_has_start_equity_null_for_new_user(self):
        # Use a dedicated fresh user to avoid interference from parallel tests
        email = f"TEST_statepristine_{uuid.uuid4().hex[:8]}@example.com"
        r0 = requests.post(f"{BASE_URL}/api/auth/register",
                           json={"email": email, "password": "testpass123", "name": "pristine"})
        assert r0.status_code == 200
        token = r0.json()["token"]
        r = requests.get(f"{BASE_URL}/api/bot/state", headers=_h(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert "start_equity" in body, "bot/state must include start_equity key"
        # New user without exchange — should be None (null)
        assert body["start_equity"] in (None, 0, 0.0), f"expected null-ish, got {body['start_equity']}"
        assert body.get("exchange_connected") is False
        # cleanup
        try:
            from pymongo import MongoClient
            c = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
            u = c.users.find_one({"email": email})
            if u:
                c.bots.delete_many({"user_id": str(u["_id"])})
                c.users.delete_one({"_id": u["_id"]})
        except Exception:
            pass


class TestBotPnlStartTsFilter:
    def test_pnl_returns_all_when_no_start_ts(self, new_user):
        token, user, _ = new_user
        uid = user["id"]
        # Insert fake PnL points directly to Mongo
        from pymongo import MongoClient
        c = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        c.bot_pnl.delete_many({"user_id": uid})
        now = int(time.time() * 1000)
        c.bot_pnl.insert_many([
            {"user_id": uid, "ts": now - 10_000, "equity": 0.0},
            {"user_id": uid, "ts": now - 5_000, "equity": 0.0},
            {"user_id": uid, "ts": now, "equity": 10.0},
        ])
        # Ensure start_ts not set
        c.bots.update_one({"user_id": uid}, {"$unset": {"start_ts": "", "start_equity": ""}})

        r = requests.get(f"{BASE_URL}/api/bot/pnl", headers=_h(token))
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 3

    def test_pnl_filters_older_when_start_ts_set(self, new_user):
        token, user, _ = new_user
        uid = user["id"]
        from pymongo import MongoClient
        c = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        c.bot_pnl.delete_many({"user_id": uid})
        now = int(time.time() * 1000)
        c.bot_pnl.insert_many([
            {"user_id": uid, "ts": now - 10_000, "equity": 0.0},
            {"user_id": uid, "ts": now - 5_000, "equity": 0.0},
            {"user_id": uid, "ts": now, "equity": 10.0},
            {"user_id": uid, "ts": now + 1_000, "equity": 12.0},
        ])
        # Set start_ts to filter out older $0 points
        c.bots.update_one({"user_id": uid}, {"$set": {"start_ts": now, "start_equity": 10.0}})

        r = requests.get(f"{BASE_URL}/api/bot/pnl", headers=_h(token))
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 2, f"expected 2 points >= start_ts, got {len(rows)}: {rows}"
        for row in rows:
            assert row["ts"] >= now


class TestBotReset:
    def test_reset_without_live_equity_clears_start(self, new_user):
        token, user, _ = new_user
        uid = user["id"]
        from pymongo import MongoClient
        c = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        # Ensure no live_equity
        c.bots.update_one({"user_id": uid}, {"$set": {"live_equity": None, "start_equity": 42.0, "start_ts": 123}})

        r = requests.post(f"{BASE_URL}/api/bot/reset", headers=_h(token))
        assert r.status_code == 200

        bot = c.bots.find_one({"user_id": uid})
        assert bot.get("start_equity") is None
        assert bot.get("start_ts") is None

    def test_reset_with_live_equity_sets_start_equity(self, new_user):
        token, user, _ = new_user
        uid = user["id"]
        from pymongo import MongoClient
        c = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        c.bots.update_one({"user_id": uid}, {"$set": {"live_equity": 123.45}})

        r = requests.post(f"{BASE_URL}/api/bot/reset", headers=_h(token))
        assert r.status_code == 200

        bot = c.bots.find_one({"user_id": uid})
        assert bot.get("start_equity") == 123.45
        assert isinstance(bot.get("start_ts"), (int, float))


# ------------ unit test refresh_live_equity ------------

class TestRefreshLiveEquity:
    def test_first_call_sets_start_equity_second_call_keeps_it(self):
        import engine
        from db import db as real_db

        uid = f"TEST_unit_{uuid.uuid4().hex[:8]}"

        async def run():
            # seed a bot doc
            await real_db.bots.insert_one({**engine.DEFAULT_BOT, "user_id": uid, "name": "TEST_unit"})
            try:
                fake_ex = AsyncMock()
                fake_ex.close = AsyncMock(return_value=None)

                with patch("engine.get_creds", new=AsyncMock(return_value={"user_id": uid, "exchange": "okx"})), \
                     patch("engine.client_from_doc", return_value=fake_ex), \
                     patch("engine.all_balances", new=AsyncMock(return_value={"trading": {"USDT": 8.94}})), \
                     patch("engine.total_usd", return_value=8.94):
                    await engine.refresh_live_equity({"user_id": uid})

                b1 = await real_db.bots.find_one({"user_id": uid})
                assert b1.get("start_equity") == 8.94, f"first call must set start_equity=8.94, got {b1.get('start_equity')}"
                assert b1.get("start_ts") is not None
                first_ts = b1.get("start_ts")
                assert b1.get("live_equity") == 8.94

                # Second call with higher equity should NOT change start_equity
                with patch("engine.get_creds", new=AsyncMock(return_value={"user_id": uid, "exchange": "okx"})), \
                     patch("engine.client_from_doc", return_value=fake_ex), \
                     patch("engine.all_balances", new=AsyncMock(return_value={"trading": {"USDT": 9.5}})), \
                     patch("engine.total_usd", return_value=9.5):
                    await engine.refresh_live_equity({"user_id": uid})

                b2 = await real_db.bots.find_one({"user_id": uid})
                assert b2.get("start_equity") == 8.94, f"start_equity must be unchanged, got {b2.get('start_equity')}"
                assert b2.get("start_ts") == first_ts
                assert b2.get("live_equity") == 9.5
            finally:
                await real_db.bots.delete_one({"user_id": uid})

        asyncio.get_event_loop().run_until_complete(run()) if False else asyncio.run(run())


# ------------ exchange_disconnect clears start ------------

class TestExchangeDisconnectClears:
    def test_disconnect_clears_start_equity(self, new_user):
        token, user, _ = new_user
        uid = user["id"]
        from pymongo import MongoClient
        c = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        c.bots.update_one({"user_id": uid}, {"$set": {"start_equity": 99.0, "start_ts": 999, "live_equity": 99.0}})

        r = requests.post(f"{BASE_URL}/api/exchange/disconnect", headers=_h(token))
        assert r.status_code == 200
        bot = c.bots.find_one({"user_id": uid})
        assert bot.get("start_equity") is None
        assert bot.get("start_ts") is None
        assert bot.get("live_equity") is None
