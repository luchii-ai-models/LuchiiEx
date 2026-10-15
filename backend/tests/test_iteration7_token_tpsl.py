# Iteration 7: Solana per-token TP/SL (wallet defaults + per-mint overrides), background watcher, Helius RPC balances
import os
import time
import uuid

import pytest
import requests
from pymongo import MongoClient
from bson import ObjectId

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"
DACITY = "nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump"
USDC = "EPjFWvd5ZgLcF8SNGfaJ8h28Gxk1xZt9AZFFr8U5bQWr"  # placeholder second mint key (len 44)
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
MDB = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _register():
    email = f"test_tpsl_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": "TestPass@123", "name": "TEST TPSL"}, timeout=30)
    assert r.status_code in (200, 201), r.text
    j = r.json()
    return {"Authorization": f"Bearer {j['token']}"}, j["user"]["id"]


@pytest.fixture(scope="module")
def user():
    from solders.keypair import Keypair
    h, uid = _register()
    r = requests.post(f"{API}/solana/wallet/import", headers=h, json={"secret": str(Keypair())}, timeout=30)
    assert r.status_code == 200, r.text
    yield h, uid
    for col in ("solana_wallets", "solana_swaps", "bots", "bot_logs", "bot_pnl"):
        MDB[col].delete_many({"user_id": uid})
    MDB.users.delete_many({"_id": ObjectId(uid)})


# ---- defaults & config ----
class TestTokenConfig:
    def test_defaults_after_import(self, user):
        g = requests.get(f"{API}/solana/wallet", headers=user[0], timeout=40).json()
        assert g["token_tp_pct"] == 25 and g["token_sl_pct"] == 15
        assert g["token_cfg"] == {} and g["positions"] == {}
        assert "error" not in g and g["sol_balance"] == 0

    def test_wallet_level_tp_sl_persist(self, user):
        r = requests.post(f"{API}/solana/config", headers=user[0], json={"token_tp_pct": 30, "token_sl_pct": 12, "tokens": [DACITY]}, timeout=40)
        assert r.status_code == 200, r.text
        g = requests.get(f"{API}/solana/wallet", headers=user[0], timeout=40).json()
        assert g["token_tp_pct"] == 30 and g["token_sl_pct"] == 12
        t = g["token_balances"][DACITY]
        assert t["tp_pct"] == 30 and t["sl_pct"] == 12
        assert t["entry_usd"] is None and t["move_pct"] is None
        assert isinstance(t["price"], float) and t["price"] > 0

    def test_token_cfg_override_merges(self, user):
        h = user[0]
        assert requests.post(f"{API}/solana/config", headers=h, json={"token_cfg": {DACITY: {"tp_pct": 50}}}, timeout=40).status_code == 200
        assert requests.post(f"{API}/solana/config", headers=h, json={"token_cfg": {BONK: {"tp_pct": 70, "sl_pct": 5}}}, timeout=40).status_code == 200
        g = requests.get(f"{API}/solana/wallet", headers=h, timeout=40).json()
        assert g["token_cfg"][DACITY] == {"tp_pct": 50}
        assert g["token_cfg"][BONK] == {"tp_pct": 70, "sl_pct": 5}
        t = g["token_balances"][DACITY]
        assert t["tp_pct"] == 50 and t["sl_pct"] == 12  # override wins, default fallback for sl

    @pytest.mark.parametrize("body", [{"token_sl_pct": 101}, {"token_sl_pct": -1}, {"token_tp_pct": -5},
                                      {"token_cfg": {DACITY: {"sl_pct": 150}}}, {"token_cfg": {DACITY: {"tp_pct": -1}}}])
    def test_validation_422(self, user, body):
        assert requests.post(f"{API}/solana/config", headers=user[0], json=body, timeout=30).status_code == 422


# ---- Helius RPC ----
class TestRpc:
    def test_repeated_wallet_no_rate_limit(self, user):
        for i in range(10):
            g = requests.get(f"{API}/solana/wallet", headers=user[0], timeout=40).json()
            assert "error" not in g, f"iter {i}: {g.get('error')}"
            assert g["sol_balance"] == 0


# ---- watcher ----
class TestWatcher:
    def _wait_clear(self, uid, timeout=20):
        t0 = time.time()
        while time.time() - t0 < timeout:
            w = MDB.solana_wallets.find_one({"user_id": uid})
            if not (w.get("positions") or {}):
                return time.time() - t0
            time.sleep(1)
        return None

    def test_take_profit_path_clears_position(self, user):
        uid = user[1]
        MDB.solana_wallets.update_one({"user_id": uid}, {"$set": {f"positions.{DACITY}": {"entry_usd": 0.0001, "raw": 1000}}})
        g = requests.get(f"{API}/solana/wallet", headers=user[0], timeout=40).json()
        t = g["token_balances"][DACITY]
        if t["entry_usd"]:  # may already be cleared if watcher was quick
            assert t["entry_usd"] == 0.0001 and t["move_pct"] is not None and t["move_pct"] > 50
        el = self._wait_clear(uid)
        assert el is not None, "watcher did not clear TP position"
        print(f"TP cleared after {el:.1f}s")

    def test_stop_loss_path_clears_position(self, user):
        uid = user[1]
        MDB.solana_wallets.update_one({"user_id": uid}, {"$set": {f"positions.{DACITY}": {"entry_usd": 100, "raw": 1000}}})
        el = self._wait_clear(uid)
        assert el is not None, "watcher did not clear SL position"
        print(f"SL cleared after {el:.1f}s")

    def test_no_hit_keeps_position(self, user):
        uid = user[1]
        g = requests.get(f"{API}/solana/wallet", headers=user[0], timeout=40).json()
        p = g["token_balances"][DACITY]["price"]
        MDB.solana_wallets.update_one({"user_id": uid}, {"$set": {f"positions.{DACITY}": {"entry_usd": p, "raw": 1000}}})
        time.sleep(12)
        w = MDB.solana_wallets.find_one({"user_id": uid})
        assert DACITY in (w.get("positions") or {}), "position cleared without TP/SL hit"
        MDB.solana_wallets.update_one({"user_id": uid}, {"$unset": {f"positions.{DACITY}": ""}})

    def test_backend_alive(self):
        assert requests.get(f"{API}/", timeout=20).json()["ok"] is True
