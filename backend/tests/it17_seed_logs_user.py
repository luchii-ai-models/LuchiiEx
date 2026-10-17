# Seed helper: registers a TEST_ user and inserts 24h bot_logs for UI full-log filter testing. Prints token.
import os, time, uuid, asyncio, requests
from dotenv import load_dotenv
load_dotenv("/app/backend/.env"); load_dotenv("/app/frontend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
email = f"test_it17ui_{uuid.uuid4().hex[:6]}@example.com"
r = requests.post(f"{BASE}/api/auth/register", json={"email": email, "password": "Test@12345", "name": "TEST it17ui"}).json()
tok, uid = r["token"], r["user"]["id"]


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    now = int(time.time() * 1000)
    rows = [("BUY", "PAPER FILLED", "[BTC] test buy"), ("SELL", "PAPER FILLED", "[BTC] BREAK-EVEN+FEES lock hit"),
            ("HOLD", None, "[ETH] hold"), ("HOLD", "WAITING", "[SOL] waiting"), ("BUY", None, "[SOL] old"),]
    docs = [{"user_id": uid, "ts": now - i * 60000, "action": a, "price": 100.0, "pct": 0.0, "engine": "algo", "confidence": 0.5, "reason": rs, "fill": f}
            for i, (a, f, rs) in enumerate(rows)]
    docs[-1]["ts"] = now - 25 * 3600 * 1000  # older than 24h -> must be excluded
    await db.bot_logs.insert_many(docs)

asyncio.run(main())
print(tok)
