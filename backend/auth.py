import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from bson import ObjectId
from fastapi import HTTPException, Request

from db import db

ALG = "HS256"


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


def verify_password(p: str, h: str) -> bool:
    return bcrypt.checkpw(p.encode(), h.encode())


def create_token(uid: str, email: str) -> str:
    payload = {"sub": uid, "email": email, "type": "access", "exp": datetime.now(timezone.utc) + timedelta(days=7)}
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=ALG)


def public_user(u: dict) -> dict:
    return {"id": str(u["_id"]), "email": u["email"], "name": u.get("name", ""), "role": u.get("role", "user")}


async def current_user(request: Request) -> dict:
    h = request.headers.get("Authorization", "")
    token = h[7:] if h.startswith("Bearer ") else request.cookies.get("access_token")
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    u = await db.users.find_one({"_id": ObjectId(payload["sub"])})
    if not u:
        raise HTTPException(401, "User not found")
    return public_user(u)


async def seed_admin():
    await db.users.create_index("email", unique=True)
    await db.login_attempts.create_index("identifier")
    email, pw = os.environ["ADMIN_EMAIL"].lower(), os.environ["ADMIN_PASSWORD"]
    u = await db.users.find_one({"email": email})
    if not u:
        await db.users.insert_one({"email": email, "password_hash": hash_password(pw), "name": "Admin", "role": "admin",
                                   "created_at": datetime.now(timezone.utc).isoformat()})
    elif not verify_password(pw, u["password_hash"]):
        await db.users.update_one({"_id": u["_id"]}, {"$set": {"password_hash": hash_password(pw)}})


async def check_lockout(ident: str):
    a = await db.login_attempts.find_one({"identifier": ident})
    if a and a.get("count", 0) >= 5 and a.get("until", 0) > datetime.now(timezone.utc).timestamp():
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")


async def record_fail(ident: str):
    until = (datetime.now(timezone.utc) + timedelta(minutes=15)).timestamp()
    await db.login_attempts.update_one({"identifier": ident}, {"$inc": {"count": 1}, "$set": {"until": until}}, upsert=True)


async def clear_fails(ident: str):
    await db.login_attempts.delete_one({"identifier": ident})
