"""Iteration 2: OKX friendly error, new exchange/wallet endpoints guards, web3 real balances."""
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


@pytest.fixture(scope="module")
def user_client():
    email = f"TEST_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"email": email, "password": "TestPass123!", "name": "TestOp"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    s.email = email
    return s


# --- OKX friendly error mapping ---
class TestOkxFriendlyError:
    def test_okx_real_key_fake_secret_passphrase_returns_50105(self, user_client):
        """User's real OKX US key id f6f0e... + bogus secret/passphrase should reach us.okx.com,
        get past 50119, and surface 50105 'passphrase is wrong'."""
        r = user_client.post(f"{BASE_URL}/api/exchange/connect", json={
            "exchange": "okx",
            "api_key": "f6f0e985-3779-4334-a48f-f0e9c14f5fb0",
            "secret": "A" * 32,
            "password": "wrong-passphrase",
        })
        assert r.status_code == 400, r.text
        body = r.text
        # Accept either 50105 (passphrase wrong) OR 50113 (signature wrong) because fake secret can short-circuit
        assert ("50105" in body) or ("50113" in body), f"Expected 50105/50113 friendly msg, got: {body}"
        # Prove host rotation reached a region (should not stop at 50119)
        assert "50119" not in body, f"Should have advanced past 50119 global, got: {body}"

    def test_okx_random_fake_key_returns_50119(self, user_client):
        """Random fake OKX key should yield 50119 across all regions."""
        r = user_client.post(f"{BASE_URL}/api/exchange/connect", json={
            "exchange": "okx",
            "api_key": "11111111-2222-3333-4444-555555555555",
            "secret": "B" * 32,
            "password": "x",
        })
        assert r.status_code == 400, r.text
        body = r.text
        assert "50119" in body
        # Friendly message mentions global/US/EEA
        assert "global" in body.lower() and ("us" in body.lower() or "eea" in body.lower())

    def test_nothing_persisted_after_failed_connect(self, user_client):
        state = user_client.get(f"{BASE_URL}/api/bot/state").json()
        assert state["exchange_connected"] is False
        assert state["exchange"] is None


# --- All new endpoints guarded: 400 "No exchange connected", never 500 ---
class TestNoExchangeGuards:
    @pytest.mark.parametrize("method,path,body", [
        ("GET", "/api/exchange/capabilities", None),
        ("POST", "/api/exchange/sync", {}),
        ("POST", "/api/exchange/order", {"side": "BUY", "usd": 10}),
        ("POST", "/api/exchange/order", {"side": "SELL", "qty": 0.001}),
        ("GET", "/api/wallet/real/networks?currency=BTC", None),
        ("GET", "/api/wallet/real/deposit-address?currency=BTC", None),
        ("POST", "/api/wallet/real/withdraw",
         {"currency": "BTC", "amount": 0.001, "address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"}),
        ("POST", "/api/wallet/real/transfer", {"currency": "USDT", "amount": 1, "direction": "to_trading"}),
        ("GET", "/api/wallet/real/history", None),
    ])
    def test_endpoint_guarded(self, user_client, method, path, body):
        if method == "GET":
            r = user_client.get(f"{BASE_URL}{path}")
        else:
            r = user_client.post(f"{BASE_URL}{path}", json=body)
        assert r.status_code == 400, f"{method} {path} → {r.status_code} {r.text}"
        assert r.status_code != 500
        assert "No exchange connected" in r.text, f"{method} {path} body: {r.text}"


# --- Web3 self-custody real balances ---
class TestWeb3RealBalances:
    BTC_ADDR = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
    EVM_ADDR = "0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe"

    def test_link_btc_returns_numeric_balance(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/wallet/web3/link",
                             json={"provider": "Wallet", "chain": "btc", "address": self.BTC_ADDR})
        assert r.status_code == 200
        # Give mempool.space a moment
        time.sleep(1)
        rows = user_client.get(f"{BASE_URL}/api/wallet/web3").json()
        row = next((w for w in rows if w["address"] == self.BTC_ADDR), None)
        assert row is not None
        assert isinstance(row["balance"], (int, float)), f"balance not numeric: {row}"
        assert row["balance"] >= 0
        # usd may be None if snapshot price not yet loaded; accept either
        if row.get("usd") is not None:
            assert isinstance(row["usd"], (int, float))

    def test_link_evm_returns_numeric_eth(self, user_client):
        r = user_client.post(f"{BASE_URL}/api/wallet/web3/link",
                             json={"provider": "Wallet", "chain": "evm", "address": self.EVM_ADDR})
        assert r.status_code == 200
        time.sleep(1)
        rows = user_client.get(f"{BASE_URL}/api/wallet/web3").json()
        row = next((w for w in rows if w["address"] == self.EVM_ADDR), None)
        assert row is not None
        assert isinstance(row["balance"], (int, float)), f"balance not numeric: {row}"
        assert row["balance"] >= 0

    def test_unlink_btc(self, user_client):
        r = user_client.delete(f"{BASE_URL}/api/wallet/web3/{self.BTC_ADDR}")
        assert r.status_code == 200
        rows = user_client.get(f"{BASE_URL}/api/wallet/web3").json()
        assert not any(w["address"] == self.BTC_ADDR for w in rows)

    def test_unlink_evm(self, user_client):
        r = user_client.delete(f"{BASE_URL}/api/wallet/web3/{self.EVM_ADDR}")
        assert r.status_code == 200
        rows = user_client.get(f"{BASE_URL}/api/wallet/web3").json()
        assert not any(w["address"] == self.EVM_ADDR for w in rows)
