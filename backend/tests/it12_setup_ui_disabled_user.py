# Iteration 12 helper: create a TEST user whose luchii003 gate is DISABLED (10 losses in a row) for UI testing of gate-reset-button
import os
import time
import uuid

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")
BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
email = f"test_it12_ui_{uuid.uuid4().hex[:6]}@example.com"
d = requests.post(f"{BASE}/api/auth/register", json={"email": email, "password": "TestPass123!", "name": "TEST it12 ui"}, timeout=20).json()
uid, t = d["user"]["id"], int(time.time() * 1000)
mdb.bot_trades.insert_many([{"user_id": uid, "side": "SELL", "strategy": "luchii003", "asset": "BTC", "price": 80000.0, "qty": 0.001, "pnl": 2.0 if k < 30 else -0.5, "ts": t - (40 - k) * 60_000} for k in range(40)])
print(email, uid)
