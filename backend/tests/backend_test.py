"""Backend regression tests for Frasberg | Luchii Trading Bot."""
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://luchii-trade-bot.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@gravia.io"
ADMIN_PASSWORD = "Gravia@2026"


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def admin_token(session):
    r = session.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def admin_client(session, admin_token):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {admin_token}"})
    return s


@pytest.fixture(scope="session")
def test_user():
    """Create a fresh TEST_ user for isolation."""
    email = f"TEST_{uuid.uuid4().hex[:8]}@example.com"
    password = "TestPass123!"
    r = requests.post(f"{BASE_URL}/api/auth/register", json={"email": email, "password": password, "name": "TestOp"})
    assert r.status_code == 200, r.text
    data = r.json()
    yield {"email": email, "password": password, "token": data["token"], "user": data["user"]}
    # cleanup not supported via API; left for operator


@pytest.fixture(scope="session")
def user_client(test_user):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {test_user['token']}"})
    return s


# --- Health ---
class TestHealth:
    def test_root(self, session):
        r = session.get(f"{BASE_URL}/api/")
        assert r.status_code == 200
        assert r.json() == {"service": "luchii", "ok": True}

    def test_exchange_list(self, session):
        r = session.get(f"{BASE_URL}/api/exchange/list")
        assert r.status_code == 200
        expected = ["binance", "binanceus", "coinbase", "kraken", "okx", "bybit", "kucoin",
                    "bitget", "gateio", "mexc", "bitfinex", "bitstamp", "gemini", "htx", "cryptocom"]
        assert r.json() == expected

    def test_market_snapshot(self, session):
        r = session.get(f"{BASE_URL}/api/market/snapshot")
        assert r.status_code == 200
        d = r.json()
        # price may be None early
        assert "price" in d or "candles" in d


# --- Auth ---
class TestAuth:
    def test_admin_login(self, session):
        r = session.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        d = r.json()
        assert "token" in d and d["user"]["email"] == ADMIN_EMAIL
        assert d["user"]["role"] == "admin"

    def test_login_invalid(self, session):
        r = session.post(f"{BASE_URL}/api/auth/login", json={"email": "nobody@nowhere.io", "password": "wrong"})
        assert r.status_code in (401, 429)

    def test_register_short_pw(self, session):
        r = session.post(f"{BASE_URL}/api/auth/register", json={"email": f"TEST_{uuid.uuid4().hex[:6]}@x.io", "password": "123"})
        assert r.status_code == 400

    def test_register_and_me(self, user_client, test_user):
        r = user_client.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == test_user["email"].lower()

    def test_unauth_me(self, session):
        r = session.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 401


# --- Bot state (fresh user) ---
class TestBotState:
    def test_fresh_state_defaults(self, user_client):
        r = user_client.get(f"{BASE_URL}/api/bot/state")
        assert r.status_code == 200
        d = r.json()
        assert d["mode"] == "live"
        assert d["status"] == "stopped"
        assert d["max_order_usd"] == 0
        assert d["daily_loss_limit"] == 0
        assert d["manual_confirm"] is False
        assert d["equity"] == 0
        assert d["exchange_connected"] is False
        assert d["name"].startswith("Luchii_")

    def test_start_without_exchange(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/bot/start")
        assert r.status_code == 400
        assert "exchange" in r.text.lower()

    def test_config_mode_dry_rejected(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/bot/config", json={"mode": "dry"})
        assert r.status_code == 422

    def test_config_position_usd(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/bot/config", json={"position_usd": 75})
        assert r.status_code == 200
        assert r.json()["position_usd"] == 75
        # verify via GET
        r2 = user_client.get(f"{BASE_URL}/api/bot/state")
        assert r2.json()["position_usd"] == 75

    def test_config_guardrails(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/bot/config", json={"max_order_usd": 200, "daily_loss_limit": 50, "manual_confirm": True})
        assert r.status_code == 200
        d = r.json()
        assert d["max_order_usd"] == 200
        assert d["daily_loss_limit"] == 50
        assert d["manual_confirm"] is True

    def test_kill_and_rearm(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/bot/kill")
        assert r.status_code == 200
        state = user_client.get(f"{BASE_URL}/api/bot/state").json()
        assert state["kill_switch"] is True
        r2 = user_client.post(f"{BASE_URL}/api/bot/kill/reset")
        assert r2.status_code == 200
        state2 = user_client.get(f"{BASE_URL}/api/bot/state").json()
        assert state2["kill_switch"] is False

    def test_reset(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/bot/reset")
        assert r.status_code == 200

    def test_logs_trades_pnl(self, user_client):
        for ep in ("logs", "trades", "pnl"):
            r = user_client.get(f"{BASE_URL}/api/bot/{ep}")
            assert r.status_code == 200
            assert isinstance(r.json(), list)


# --- Exchange ---
class TestExchange:
    def test_test_order_without_exchange(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/exchange/test-order")
        assert r.status_code == 400
        assert "No exchange connected" in r.text

    def test_fake_kraken_fails(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/exchange/connect",
                             json={"exchange": "kraken", "api_key": "FAKE_KEY", "secret": "FAKE_SECRET"})
        assert r.status_code == 400
        assert "Exchange verification failed" in r.text

    def test_fake_okx_fails(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/exchange/connect",
                             json={"exchange": "okx", "api_key": "FAKE_KEY", "secret": "FAKE_SECRET", "password": "x"})
        assert r.status_code == 400
        assert "Exchange verification failed" in r.text

    def test_unsupported_exchange(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/exchange/connect",
                             json={"exchange": "notreal", "api_key": "a", "secret": "b"})
        assert r.status_code == 400


# --- Wallet web3 ---
class TestWallet:
    def test_link_list_unlink_web3(self, user_client):
        addr = "bc1qTEST" + uuid.uuid4().hex[:16]
        r = user_client.post(f"{BASE_URL}/api/wallet/web3/link",
                             json={"provider": "manual", "chain": "btc", "address": addr})
        assert r.status_code == 200
        lst = user_client.get(f"{BASE_URL}/api/wallet/web3").json()
        assert any(w["address"] == addr for w in lst)
        d = user_client.delete(f"{BASE_URL}/api/wallet/web3/{addr}")
        assert d.status_code == 200
        lst2 = user_client.get(f"{BASE_URL}/api/wallet/web3").json()
        assert not any(w["address"] == addr for w in lst2)

    def test_wallet_balance_no_exchange(self, user_client):
        r = user_client.get(f"{BASE_URL}/api/wallet/real/balance")
        assert r.status_code == 400


# --- Alerts ---
class TestAlerts:
    def test_alerts_status(self, user_client):
        r = user_client.get(f"{BASE_URL}/api/alerts/status")
        assert r.status_code == 200
        assert "chat_id" in r.json()

    def test_alerts_set(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/alerts/set", json={"chat_id": "123456"})
        assert r.status_code == 200
        s = user_client.get(f"{BASE_URL}/api/alerts/status").json()
        assert s["chat_id"] == "123456"


# --- Brokers ---
class TestBrokers:
    def test_brokers_status(self, user_client):
        r = user_client.get(f"{BASE_URL}/api/brokers/status")
        assert r.status_code == 200
        d = r.json()
        for k in ("exchange", "metaapi", "pocketoption"):
            assert k in d

    def test_pocketoption_bogus_ssid(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/brokers/pocketoption/connect",
                             json={"ssid": "bogus_ssid_12345", "is_demo": False, "asset": "BTCUSD_otc", "amount": 1, "duration": 60})
        assert r.status_code == 400
        assert "PocketOption connection failed" in r.text


# --- Chat / Help / Models ---
class TestChatOffline:
    def test_models(self, user_client):
        r = user_client.get(f"{BASE_URL}/api/chat/models")
        assert r.status_code == 200
        names = [m["name"] for m in r.json()]
        assert names == ["Luchii 4.7", "Luchii 4.7 Pro", "Luchii Mini"]

    def test_chat_offline(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/chat/message", json={"model": "luchii-4.7", "message": "hi"})
        assert r.status_code == 503
        assert "temporarily unavailable" in r.text.lower()

    def test_help_offline(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/help/chat", json={"message": "hi"})
        assert r.status_code == 503

    def test_contact(self, session):
        r = session.post(f"{BASE_URL}/api/contact",
                         json={"name": "TEST_u", "email": "t@t.io", "subject": "x", "message": "hi"})
        assert r.status_code == 200


# --- Leaderboard ---
class TestLeaderboard:
    def test_leaderboard(self, user_client):
        r = user_client.get(f"{BASE_URL}/api/leaderboard")
        assert r.status_code == 200
        lb = r.json()
        assert isinstance(lb, list)
        if lb:
            assert "rank" in lb[0] and "name" in lb[0] and "pnl" in lb[0]
