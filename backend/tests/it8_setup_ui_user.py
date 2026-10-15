# Helper: create/cleanup a fresh UI test user with generated (empty) Solana wallet. Usage: python it8_setup_ui_user.py [create|cleanup <email>]
import json
import os
import sys
import uuid

import requests
from pymongo import MongoClient
from solders.keypair import Keypair

API = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") + "/api"
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

if sys.argv[1] == "create":
    email = f"test_it8ui_{uuid.uuid4().hex[:6]}@example.com"
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": "TestPass123!", "name": "TEST it8ui"}).json()
    h = {"Authorization": f"Bearer {r['token']}"}
    w = requests.post(f"{API}/solana/wallet/import", headers=h, json={"secret": str(Keypair())}, timeout=40).json()
    print(json.dumps({"email": email, "token": r["token"], "uid": r["user"]["id"], "pubkey": w.get("pubkey")}))
else:
    u = db.users.find_one({"email": sys.argv[2]})
    uid = str(u["_id"])
    for c in ("bots", "bot_logs", "bot_trades", "bot_pnl", "solana_wallets"):
        print(c, db[c].delete_many({"user_id": uid}).deleted_count)
    print("user", db.users.delete_one({"_id": u["_id"]}).deleted_count)
