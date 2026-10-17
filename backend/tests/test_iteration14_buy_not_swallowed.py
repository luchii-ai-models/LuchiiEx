# Regression: a BUY that entry_blocked allows ((None, gate)) must reach execute(), not be logged as WAITING.
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

import engine  # noqa: E402


def test_allowed_buy_reaches_execute(monkeypatch):
    async def run():
        import audit
        import gates
        import solana_dex
        old = engine.db, solana_dex.db, audit.db, gates.db
        engine.db = solana_dex.db = audit.db = gates.db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        uid = f"t14_{uuid.uuid4().hex[:8]}"
        calls = {"execute": 0, "wait": 0}
        try:
            await engine.db.bots.insert_one({**engine.DEFAULT_BOT, "user_id": uid, "status": "running", "mode": "dry", "strategy": "aggressive", "assets": ["ETH"], "cash": 1000.0})
            engine.SNAPSHOT.update({"price": 100.0, "markets": {"ETH": {"price": 2400.0, "candles": [{"t": k, "o": 1, "h": 1, "l": 1, "c": 2400.0, "v": 1} for k in range(40)], "rsi": 50}}})
            monkeypatch.setattr(engine, "decide", lambda b, a: {"action": "BUY", "confidence": 0.9, "reason": "x"})

            async def eb(uid_, bot, asset, d, gate_only=False):
                return None, {}

            async def ex(bot, d, asset):
                calls["execute"] += 1

            async def wl(uid_, bot, reason):
                calls["wait"] += 1

            monkeypatch.setattr(engine, "entry_blocked", eb)
            monkeypatch.setattr(engine, "execute", ex)
            monkeypatch.setattr(engine, "wait_log", wl)
            monkeypatch.setattr(engine, "refresh_live_equity", lambda b: asyncio.sleep(0))
            bot = await engine.db.bots.find_one({"user_id": uid})
            await engine.tick_bot(bot)
        finally:
            await engine.db.bots.delete_many({"user_id": uid})
            await engine.db.bot_pnl.delete_many({"user_id": uid})
            engine.db, solana_dex.db, audit.db, gates.db = old
        assert calls == {"execute": 1, "wait": 0}, calls

    asyncio.run(run())
