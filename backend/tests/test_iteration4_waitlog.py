"""Iteration 4 — WAITING log throttle, OKX 51008 friendly mapping, sync_position, bot API regression."""
import os
import asyncio
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://frasberg-luchii.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@gravia.io"
ADMIN_PASSWORD = "Gravia@2026"


# ---------- API regression (admin, read-only) ----------

@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    s.headers["Content-Type"] = "application/json"
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    s.headers["Authorization"] = f"Bearer {r.json()['token']}"
    yield s
    s.close()


class TestAdminBotEndpoints:
    """Read-only regression on admin — must not start/stop the real bot."""

    def test_bot_state(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/bot/state")
        assert r.status_code == 200, r.text
        data = r.json()
        # basic shape
        for k in ("status", "mode", "position_qty", "cash"):
            assert k in data, f"missing key {k} in bot state"

    def test_bot_logs(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/bot/logs")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)


# ---------- friendly() OKX 51008 mapping ----------

def test_friendly_maps_okx_51008_exact_string():
    from exchange_service import friendly
    raw = (
        'okx {"code":"1","data":[{"clOrdId":"x","ordId":"","sCode":"51008",'
        '"sMsg":"Order failed. Your available balance is insufficient"}],'
        '"msg":"All operations failed"}'
    )
    msg = friendly(Exception(raw))
    assert "Not enough balance on OKX for this order" in msg
    assert "OKX 51008" in msg


def test_friendly_fallback_for_unknown_error():
    from exchange_service import friendly
    msg = friendly(ValueError("random boom"))
    assert "ValueError" in msg and "random boom" in msg


# ---------- Engine unit tests (fake ccxt + monkeypatched creds) ----------

class FakeCreateOrderEx:
    """Mimics ccxt client used by engine.execute."""

    def __init__(self, free_quote=0.0, free_btc=0.0, min_amount=0.0, create_result=None):
        self._free_quote = free_quote
        self._free_btc = free_btc
        self._min_amount = min_amount
        self._create_result = create_result or {"id": "ord1", "filled": None, "average": None}
        self.create_calls = []
        self.closed = False

    async def load_markets(self):
        return {"BTC/USDT": {}}

    @property
    def markets(self):
        return {"BTC/USDT": {}}

    def market(self, symbol):
        return {"limits": {"amount": {"min": self._min_amount}}}

    async def fetch_balance(self, params=None):
        # Return trading balance with free quote USDT & BTC
        if params and params.get("type"):
            return {"total": {}, "USDT": {"free": 0}, "BTC": {"free": 0}}
        return {
            "total": {"USDT": self._free_quote, "BTC": self._free_btc},
            "USDT": {"free": self._free_quote},
            "BTC": {"free": self._free_btc},
        }

    async def fetch_ticker(self, symbol):
        return {"last": 50000.0}

    def amount_to_precision(self, sym, amt):
        return f"{amt:.8f}"

    def currency_to_precision(self, cur, amt):
        return f"{amt:.8f}"

    async def transfer(self, cur, amt, fr, to):
        return {"id": "t1"}

    async def create_order(self, symbol, type_, side, amount, price=None):
        self.create_calls.append((symbol, type_, side, amount, price))
        r = dict(self._create_result)
        r.setdefault("filled", amount)
        r.setdefault("average", price or 50000.0)
        return r

    async def close(self):
        self.closed = True


# --- helpers to isolate engine from DB & market ---

class FakeCollection:
    def __init__(self):
        self.docs = []
        self.updates = []
        self._bot_state = {}

    async def insert_one(self, d):
        self.docs.append(d)
        return type("R", (), {"inserted_id": "x"})()

    async def update_one(self, flt, upd, upsert=False):
        self.updates.append((flt, upd))
        s = upd.get("$set") or {}
        self._bot_state.update(s)
        return type("R", (), {"matched_count": 1})()

    async def find_one(self, flt, proj=None):
        if "user_id" in flt:
            return dict(self._bot_state)
        return None

    def find(self, *a, **k):
        async def _iter():
            if False:
                yield None
        return _iter()


@pytest.fixture
def engine_env(monkeypatch):
    """Patch engine.db, engine.SNAPSHOT, creds, and client_from_doc with fakes."""
    import engine
    import exchange_service

    bots = FakeCollection()
    logs = FakeCollection()
    trades = FakeCollection()
    pnl = FakeCollection()

    class FakeDB:
        pass

    fake_db = FakeDB()
    fake_db.bots = bots
    fake_db.bot_logs = logs
    fake_db.bot_trades = trades
    fake_db.bot_pnl = pnl
    fake_db.exchange_credentials = FakeCollection()
    monkeypatch.setattr(engine, "db", fake_db)
    monkeypatch.setattr(engine, "SNAPSHOT", {"price": 50000.0, "candles": [{"c": 49000}, {"c": 50000}], "rsi": 50})

    async def _fake_get_creds(uid):
        return {"exchange": "okx", "api_key": "x", "secret": "x", "password": "x"}
    monkeypatch.setattr(engine, "get_creds", _fake_get_creds)

    # Dummy notify + mirror + daily-loss (to avoid network / alerts side effects)
    async def _noop(*a, **k):
        return None
    import alerts
    monkeypatch.setattr(alerts, "notify", _noop)
    monkeypatch.setattr(engine, "mirror_all", _noop)
    monkeypatch.setattr(engine, "check_daily_loss", _noop)

    # Also neutralise ensure_funds to a no-op (don't touch exchange funding)
    async def _no_funds(ex, exchange, cur, need):
        return 0.0
    monkeypatch.setattr(engine, "ensure_funds", _no_funds)

    holder = {"ex": None}

    def _fake_client(doc):
        return holder["ex"]
    monkeypatch.setattr(engine, "client_from_doc", _fake_client)

    return engine, fake_db, holder


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _mk_bot(**over):
    base = {
        "user_id": "TEST_u1", "status": "running", "mode": "live", "position_usd": 50.0, "max_order_usd": 0.0,
        "daily_loss_limit": 0.0, "manual_confirm": False, "kill_switch": False, "cash": 10000.0,
        "position_qty": 0.0, "entry_price": 0.0, "realized_pnl": 0.0, "trades_count": 0, "wins": 0, "losses": 0,
        "alerts_enabled": False, "live_equity": 0, "last_tick": 0, "pocketoption": None, "auto_fund": True,
        "last_wait_log": 0,
    }
    base.update(over)
    return base


class TestExecuteWaitLog:
    def test_buy_with_zero_quote_logs_waiting_no_order(self, engine_env):
        engine, fake_db, holder = engine_env
        ex = FakeCreateOrderEx(free_quote=0.0)
        holder["ex"] = ex
        bot = _mk_bot()
        # seed bot state in fake db so wait_log's set_bot update works
        fake_db.bots._bot_state = dict(bot)

        _run(engine.execute(bot, {"action": "BUY", "confidence": 0.7, "reason": "test", "engine": "algo"}))

        assert ex.create_calls == [], "No order must be placed when quote balance is zero"
        waiting = [l for l in fake_db.bot_logs.docs if l.get("fill") == "WAITING"]
        assert len(waiting) == 1
        assert "BUY signal skipped" in waiting[0]["reason"]
        # last_wait_log must have been set
        assert fake_db.bots._bot_state.get("last_wait_log", 0) > 0

    def test_second_buy_within_300s_no_additional_log(self, engine_env):
        engine, fake_db, holder = engine_env
        ex = FakeCreateOrderEx(free_quote=0.0)
        holder["ex"] = ex
        bot = _mk_bot(last_wait_log=time.time())  # recent
        fake_db.bots._bot_state = dict(bot)

        _run(engine.execute(bot, {"action": "BUY", "confidence": 0.7, "reason": "test", "engine": "algo"}))

        assert ex.create_calls == []
        waiting = [l for l in fake_db.bot_logs.docs if l.get("fill") == "WAITING"]
        assert len(waiting) == 0, "Throttled WAITING should NOT produce a second log within 300s"

    def test_buy_with_sufficient_quote_places_order_and_records_fill(self, engine_env):
        engine, fake_db, holder = engine_env
        ex = FakeCreateOrderEx(free_quote=1000.0, min_amount=0.00001,
                               create_result={"id": "ok1", "filled": 0.001, "average": 50000.0})
        holder["ex"] = ex
        bot = _mk_bot()
        fake_db.bots._bot_state = dict(bot)

        _run(engine.execute(bot, {"action": "BUY", "confidence": 0.7, "reason": "test", "engine": "algo"}))

        assert len(ex.create_calls) == 1, f"Expected 1 create_order, got {ex.create_calls}"
        sym, typ, side, amt, price = ex.create_calls[0]
        assert sym == "BTC/USDT" and typ == "market" and side == "buy"
        # record_fill ran -> trades_count bumped & trade inserted
        assert fake_db.bots._bot_state.get("trades_count") == 1
        assert len(fake_db.bot_trades.docs) == 1
        assert fake_db.bot_trades.docs[0]["side"] == "BUY"


# ---------- sync_position tests ----------

class TestSyncPosition:
    def test_flat_bot_with_btc_sets_position(self, engine_env):
        engine, fake_db, holder = engine_env
        bot = _mk_bot(position_qty=0.0, entry_price=0.0)
        fake_db.bots._bot_state = dict(bot)
        bal = {"trading": {"BTC": 0.0005}, "funding": {"BTC": 0.00006}}

        _run(engine.sync_position(bot, bal))

        st = fake_db.bots._bot_state
        assert st["position_qty"] == pytest.approx(0.00056, abs=1e-8)
        assert st["entry_price"] == 50000.0
        synced = [l for l in fake_db.bot_logs.docs if l.get("fill") == "SYNCED"]
        assert len(synced) == 1 and "Synced position" in synced[0]["reason"]

    def test_flat_bot_dust_btc_ignored(self, engine_env):
        engine, fake_db, holder = engine_env
        bot = _mk_bot(position_qty=0.0)
        fake_db.bots._bot_state = dict(bot)
        bal = {"trading": {"BTC": 1e-7}}

        _run(engine.sync_position(bot, bal))

        # No update should have happened (below 1e-5 threshold)
        assert fake_db.bots._bot_state.get("position_qty", 0.0) == 0.0
        synced = [l for l in fake_db.bot_logs.docs if l.get("fill") == "SYNCED"]
        assert synced == []

    def test_position_reduced_when_btc_drops_below_half(self, engine_env):
        engine, fake_db, holder = engine_env
        bot = _mk_bot(position_qty=0.001, entry_price=50000.0)
        fake_db.bots._bot_state = dict(bot)
        bal = {"trading": {"BTC": 0.0001}}  # 10% of previous

        _run(engine.sync_position(bot, bal))

        assert fake_db.bots._bot_state["position_qty"] == pytest.approx(0.0001, abs=1e-9)

    def test_position_flattened_when_btc_near_zero(self, engine_env):
        engine, fake_db, holder = engine_env
        bot = _mk_bot(position_qty=0.001, entry_price=50000.0)
        fake_db.bots._bot_state = dict(bot)
        bal = {"trading": {"BTC": 0.0}}

        _run(engine.sync_position(bot, bal))

        assert fake_db.bots._bot_state["position_qty"] == 0.0
        assert fake_db.bots._bot_state["entry_price"] == 0.0
