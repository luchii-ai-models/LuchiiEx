# Iteration 6: bot risk controls (TP/SL/filters) + Solana DEX (Jupiter) wallet/config/search/swap + engine.signal unit
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"
DACITY = "nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump"


def _register():
    email = f"test_sol_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": "TestPass@123", "name": "TEST Sol"}, timeout=30)
    assert r.status_code in (200, 201), r.text
    j = r.json()
    return {"Authorization": f"Bearer {j['token']}"}, j["user"]["id"]


def _keypair():
    from solders.keypair import Keypair
    return str(Keypair())


@pytest.fixture(scope="module")
def users():
    a, aid = _register()
    b, bid = _register()
    yield (a, aid, b, bid)
    from pymongo import MongoClient
    from bson import ObjectId
    c = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    for uid in (aid, bid):
        c.solana_wallets.delete_many({"user_id": uid})
        c.solana_swaps.delete_many({"user_id": uid})
        c.bots.delete_many({"user_id": uid})
        c.bot_logs.delete_many({"user_id": uid})
        c.bot_pnl.delete_many({"user_id": uid})
        c.users.delete_many({"_id": {"$in": [ObjectId(uid)]}})


# ---- risk controls config ----
class TestRiskConfig:
    def test_config_save_and_echo(self, users):
        h = users[0]
        r = requests.post(f"{API}/bot/config", headers=h, json={"take_profit_pct": 2.5, "stop_loss_pct": 0.8,
                                                                  "trend_filter": False, "volume_filter": False}, timeout=30)
        assert r.status_code == 200, r.text
        s = requests.get(f"{API}/bot/state", headers=h, timeout=30).json()
        assert s["take_profit_pct"] == 2.5 and s["stop_loss_pct"] == 0.8
        assert s["trend_filter"] is False and s["volume_filter"] is False

    def test_defaults_for_fresh_user(self, users):
        s = requests.get(f"{API}/bot/state", headers=users[2], timeout=30).json()
        assert s["take_profit_pct"] == 0.6 and s["stop_loss_pct"] == 0.4 and s["aggressive"] is True and s["size_pct"] == 50
        assert s["trend_filter"] is True and s["volume_filter"] is True

    @pytest.mark.parametrize("body", [{"take_profit_pct": 101}, {"take_profit_pct": -1}, {"stop_loss_pct": -0.5},
                                      {"mode": "dry"}])
    def test_config_rejects_invalid(self, users, body):
        r = requests.post(f"{API}/bot/config", headers=users[0], json=body, timeout=30)
        assert r.status_code == 422, r.text


# ---- solana wallet ----
class TestSolana:
    def test_auth_required(self):
        for m, p in [("get", "/solana/wallet"), ("post", "/solana/wallet/import"), ("delete", "/solana/wallet"),
                     ("post", "/solana/config"), ("get", "/solana/tokens/search?q=x"), ("post", "/solana/swap"),
                     ("get", "/solana/swaps")]:
            r = getattr(requests, m)(f"{API}{p}", json={}, timeout=30) if m == "post" else getattr(requests, m)(f"{API}{p}", timeout=30)
            assert r.status_code == 401, f"{m} {p} -> {r.status_code}"

    def test_fresh_not_connected_and_config_before_import(self, users):
        h = users[0]
        assert requests.get(f"{API}/solana/wallet", headers=h, timeout=30).json() == {"connected": False}
        r = requests.post(f"{API}/solana/config", headers=h, json={"enabled": True}, timeout=30)
        assert r.status_code == 400 and "Import" in r.json()["detail"]
        r = requests.post(f"{API}/solana/swap", headers=h, json={"mint": DACITY, "side": "BUY"}, timeout=30)
        assert r.status_code == 400

    def test_import_garbage(self, users):
        r = requests.post(f"{API}/solana/wallet/import", headers=users[0], json={"secret": "x" * 40}, timeout=30)
        assert r.status_code == 400 and "Invalid Solana private key" in r.json()["detail"]

    def test_import_valid_and_get(self, users):
        h = users[0]
        r = requests.post(f"{API}/solana/wallet/import", headers=h, json={"secret": _keypair()}, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is True and 32 <= len(j["pubkey"]) <= 44
        g = requests.get(f"{API}/solana/wallet", headers=h, timeout=40).json()
        assert g["connected"] is True and g["pubkey"] == j["pubkey"]
        assert "secret_enc" not in g and "_id" not in g
        assert g.get("sol_balance") == 0, g

    def test_isolation(self, users):
        assert requests.get(f"{API}/solana/wallet", headers=users[2], timeout=30).json()["connected"] is False

    def test_config_save_and_filter(self, users):
        h = users[0]
        r = requests.post(f"{API}/solana/config", headers=h, json={"tokens": [DACITY, "short", "z" * 50],
                                                                     "amount_sol": 0.02, "slippage_bps": 300, "enabled": True}, timeout=40)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["tokens"] == [DACITY]
        assert j["amount_sol"] == 0.02 and j["slippage_bps"] == 300 and j["enabled"] is True
        assert j["token_balances"][DACITY]["symbol"] == "Dacity", j.get("token_balances")
        assert j["token_balances"][DACITY]["raw"] == 0

    def test_config_validation(self, users):
        h = users[0]
        for body in [{"slippage_bps": 5}, {"amount_sol": 0}, {"tokens": [DACITY] * 6}]:
            assert requests.post(f"{API}/solana/config", headers=h, json=body, timeout=30).status_code == 422, body

    def test_search(self, users):
        r = requests.get(f"{API}/solana/tokens/search", headers=users[0], params={"q": "Dacity"}, timeout=30)
        assert r.status_code == 200, r.text
        lst = r.json()
        assert isinstance(lst, list) and lst
        for k in ("mint", "symbol", "name", "liquidity"):
            assert k in lst[0]
        assert any(t["mint"] == DACITY for t in lst)

    def test_swap_buy_empty_wallet(self, users):
        r = requests.post(f"{API}/solana/swap", headers=users[0], json={"mint": DACITY, "side": "BUY"}, timeout=60)
        assert r.status_code == 400, r.text
        d = r.json()["detail"]
        assert d.startswith("Swap failed: need") and "SOL" in d
        assert "sig" not in r.json()

    def test_swap_sell_no_balance(self, users):
        r = requests.post(f"{API}/solana/swap", headers=users[0], json={"mint": DACITY, "side": "SELL"}, timeout=60)
        assert r.status_code == 400, r.text

    def test_swap_invalid_side(self, users):
        r = requests.post(f"{API}/solana/swap", headers=users[0], json={"mint": DACITY, "side": "HOLD"}, timeout=30)
        assert r.status_code == 422

    def test_delete(self, users):
        h = users[0]
        r = requests.delete(f"{API}/solana/wallet", headers=h, timeout=30)
        assert r.status_code == 200 and r.json()["ok"] is True
        assert requests.get(f"{API}/solana/wallet", headers=h, timeout=30).json()["connected"] is False


# ---- engine.signal unit ----
def _candles(n=40, base=100.0, rising=True, vol_last=1.0, vol_avg=1.0):
    out = []
    for i in range(n):
        c = base + (i * 0.1 if rising else -i * 0.1)
        out.append({"c": c, "v": vol_avg})
    out[-2]["v"] = vol_last
    return out


class TestSignal:
    def _set(self, candles, rsi=55):
        from market import SNAPSHOT
        SNAPSHOT.clear()
        SNAPSHOT.update(candles=candles, price=candles[-1]["c"], rsi=rsi)
        return SNAPSHOT

    def _bot(self, **kw):
        b = {"position_qty": 0.0, "entry_price": 0.0, "take_profit_pct": 1.5, "stop_loss_pct": 1.0,
             "trend_filter": True, "volume_filter": True, "aggressive": False}
        b.update(kw)
        return b

    def test_take_profit(self):
        import engine
        s = self._set(_candles())
        d = engine.signal(self._bot(position_qty=0.01, entry_price=s["price"] / 1.02))
        assert d["action"] == "SELL" and "TAKE PROFIT" in d["reason"]

    def test_stop_loss(self):
        import engine
        s = self._set(_candles())
        d = engine.signal(self._bot(position_qty=0.01, entry_price=s["price"] * 1.02))
        assert d["action"] == "SELL" and "STOP LOSS" in d["reason"]

    def test_volume_filter_hold(self):
        import engine
        self._set(_candles(vol_last=0.1, vol_avg=5.0))
        d = engine.signal(self._bot())
        assert d["action"] == "HOLD" and "volume filter" in d["reason"]
        d2 = engine.signal(self._bot(volume_filter=False))
        assert d2["action"] == "BUY"

    def test_trend_filter_hold(self):
        import engine
        c = _candles()
        c[-1]["c"] = 50.0  # price below SMA25 while SMA7>SMA25 still
        c = c[:-1] + [{"c": c[-2]["c"] + 0.01, "v": 1.0}]
        s = self._set(c)
        s["price"] = 90.0  # below SMA25
        d = engine.signal(self._bot())
        assert d["action"] == "HOLD" and "trend filter" in d["reason"]
        d2 = engine.signal(self._bot(trend_filter=False, volume_filter=False))
        assert d2["action"] == "BUY"
