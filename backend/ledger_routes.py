"""Ledger / profile / webhook / admin routes. Mounted on the main /api router."""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

import ledger
import profile as prof
import webhooks
from auth import current_user
from db import db

r = APIRouter()

ROLE_PERMS = {
    "user": {"PROFILE", "WALLET_READ", "JOURNAL_READ", "DEPOSIT", "WITHDRAWAL_CREATE"},
    "support": {"PROFILE", "WALLET_READ", "JOURNAL_READ"},
    "treasury_admin": {"PROFILE", "WALLET_READ", "JOURNAL_READ", "JOURNAL_REVERSE", "TREASURY", "RECONCILIATION", "DEAD_LETTER_REPLAY", "ADMIN"},
    "compliance_admin": {"PROFILE", "WALLET_READ", "JOURNAL_READ", "AUDIT_READ", "KYC_MANAGE", "ADMIN"},
    "admin": {"*"}, "super_admin": {"*"},
}


def require(perm: str):
    async def dep(user=Depends(current_user)):
        perms = ROLE_PERMS.get(user.get("role", "user"), set())
        if "*" not in perms and perm not in perms:
            raise HTTPException(403, {"code": "FORBIDDEN", "message": f"{perm} required"})
        return user
    return dep


def _page(page: int, size: int):
    return max(1, page), max(1, min(size, 100))


def _err(e: Exception, code="LEDGER_ERROR", status=400):
    raise HTTPException(status, {"code": code, "message": str(e)})


# ---------- profile / settings / kyc ----------
@r.get("/profile")
async def get_profile(user=Depends(current_user)):
    return {"profile": await prof.get_profile(user), "settings": await prof.get_settings(user["id"]), "kyc": await prof.get_kyc(user["id"])}


@r.patch("/profile")
async def patch_profile(body: prof.ProfileUpdate, user=Depends(current_user)):
    try:
        out = await prof.update_profile(user, body)
    except ValueError as e:
        _err(e, "VALIDATION_ERROR")
    await ledger.audit_event(user["id"], "user_profile", user["id"], "UPDATE", None, body.model_dump(exclude_none=True))
    return out


@r.get("/settings")
async def get_settings(user=Depends(current_user)):
    return await prof.get_settings(user["id"])


@r.patch("/settings")
async def patch_settings(body: prof.SettingsUpdate, user=Depends(current_user)):
    return await prof.update_settings(user["id"], body)


@r.get("/kyc")
async def get_kyc(user=Depends(current_user)):
    return await prof.get_kyc(user["id"])


@r.post("/kyc/submit", status_code=202)
async def submit_kyc(body: prof.KycSubmit, user=Depends(current_user)):
    try:
        out = await prof.submit_kyc(user["id"], body)
    except ValueError as e:
        _err(e, "VALIDATION_ERROR")
    await ledger.audit_event(user["id"], "user_kyc", user["id"], "SUBMIT", None, {"status": "IN_REVIEW"})
    return out


class KycDecision(BaseModel):
    status: Literal["APPROVED", "REJECTED", "IN_REVIEW"]
    reference: Optional[str] = None


@r.patch("/admin/kyc/{uid}")
async def admin_kyc(uid: str, body: KycDecision, user=Depends(require("KYC_MANAGE"))):
    old = await prof.get_kyc(uid)
    out = await prof.set_kyc_status(uid, body.status, "MANUAL", body.reference)
    await ledger.audit_event(user["id"], "user_kyc", uid, "DECISION", {"status": old["status"]}, {"status": body.status})
    return out


# ---------- wallets / ledger ----------
@r.get("/wallets")
async def wallets(user=Depends(require("WALLET_READ"))):
    await ledger.provision_user(user["id"])
    return {"wallets": await ledger.user_wallets(user["id"])}


@r.get("/ledger/journals")
async def list_journals(page: int = 1, pageSize: int = 25, user=Depends(require("JOURNAL_READ"))):
    p, s = _page(page, pageSize)
    return await ledger.journals(user["id"], p, s, admin=("*" in ROLE_PERMS.get(user["role"], set())))


@r.get("/ledger/journals/{jid}")
async def get_journal(jid: str, user=Depends(require("JOURNAL_READ"))):
    je = await db.journal_entries.find_one({"id": jid}, {"_id": 0}) or _err(ValueError("Journal not found"), "NOT_FOUND", 404)
    je["lines"] = await db.journal_lines.find({"journal_entry_id": jid}, {"_id": 0}).to_list(None)
    admin = "*" in ROLE_PERMS.get(user["role"], set())
    if not admin and je.get("actor_id") != user["id"] and jid not in await ledger._user_journal_ids(user["id"]):
        raise HTTPException(403, {"code": "FORBIDDEN", "message": "Not your journal"})
    return je


class ReversalIn(BaseModel):
    journalId: str
    reason: str = Field(min_length=3, max_length=500)


@r.post("/ledger/reversals", status_code=201)
async def create_reversal(body: ReversalIn, user=Depends(require("JOURNAL_REVERSE"))):
    try:
        return await ledger.reverse_journal(body.journalId, body.reason, user["id"])
    except ledger.LedgerError as e:
        _err(e)


class JournalIn(BaseModel):
    lines: list[dict] = Field(min_length=2)
    description: str
    source: str = "SYSTEM"
    isConversion: bool = False


@r.post("/admin/ledger/journals", status_code=201)
async def admin_post_journal(body: JournalIn, user=Depends(require("TREASURY")), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    try:
        return await ledger.post_journal(body.lines, source=body.source, description=body.description, reference_type="MANUAL",
                                         idempotency_key=idempotency_key, is_conversion=body.isConversion, actor_id=user["id"])
    except ledger.LedgerError as e:
        _err(e)


class TreasuryTransferIn(BaseModel):
    asset: Literal["USD", "USDT", "BTC"]
    amount: float = Field(gt=0)
    direction: Literal["to_okx", "to_treasury"] = "to_okx"


@r.post("/treasury/transfers", status_code=201)
async def treasury_transfer(body: TreasuryTransferIn, user=Depends(require("TREASURY")), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    t, m = f"TREASURY_{body.asset}", ledger._master(body.asset)
    dr, cr = (m, t) if body.direction == "to_okx" else (t, m)
    try:
        return await ledger.post_journal([{"account": dr, "debit": body.amount}, {"account": cr, "credit": body.amount}], source="SYSTEM",
                                         description=f"Treasury transfer {body.amount} {body.asset} {body.direction}", reference_type="OKX_FUNDING_TRANSFER",
                                         idempotency_key=idempotency_key, actor_id=user["id"])
    except ledger.LedgerError as e:
        _err(e)


# ---------- admin ----------
@r.get("/admin/accounts")
async def admin_accounts(user=Depends(require("ADMIN"))):
    return {"items": await ledger.all_balances(), "trialBalance": await ledger.trial_balance()}


@r.get("/admin/audit-events")
async def admin_audit(page: int = 1, pageSize: int = 50, user=Depends(require("AUDIT_READ"))):
    p, s = _page(page, pageSize)
    total = await db.audit_events.count_documents({})
    items = await db.audit_events.find({}, {"_id": 0}).sort("chain_sequence", -1).skip((p - 1) * s).limit(s).to_list(s)
    return {"items": items, "pagination": {"page": p, "pageSize": s, "total": total, "totalPages": max(1, -(-total // s))}}


@r.get("/admin/audit-events/verify")
async def admin_audit_verify(user=Depends(require("AUDIT_READ"))):
    return await ledger.verify_audit_chain()


@r.get("/admin/dead-letters")
async def dead_letters(user=Depends(require("ADMIN"))):
    return {"items": await db.webhook_dead_letters.find({}, {"_id": 0}).sort("failed_at", -1).limit(100).to_list(100)}


@r.post("/admin/dead-letters/{dl_id}/replay", status_code=202)
async def replay(dl_id: str, user=Depends(require("DEAD_LETTER_REPLAY"))):
    try:
        return await webhooks.replay_dead_letter(dl_id, user["id"])
    except webhooks.WebhookError as e:
        raise HTTPException(e.status, {"code": e.code, "message": str(e)})


@r.get("/admin/webhook-jobs")
async def webhook_jobs(status: Optional[str] = None, user=Depends(require("ADMIN"))):
    q = {"status": status} if status else {}
    return {"items": await db.webhook_jobs.find(q, {"_id": 0}).sort("created_at", -1).limit(100).to_list(100)}


class RoleIn(BaseModel):
    role: Literal["user", "support", "treasury_admin", "compliance_admin", "admin"]


@r.patch("/admin/users/{uid}/role")
async def set_role(uid: str, body: RoleIn, user=Depends(require("*"))):
    from bson import ObjectId
    u = await db.users.find_one({"_id": ObjectId(uid)}) or _err(ValueError("User not found"), "NOT_FOUND", 404)
    await db.users.update_one({"_id": u["_id"]}, {"$set": {"role": body.role}})
    await ledger.audit_event(user["id"], "user", uid, "ROLE_CHANGE", {"role": u.get("role")}, {"role": body.role})
    return {"id": uid, "role": body.role}


# ---------- webhooks (unauthenticated, signature-verified) ----------
@r.post("/webhooks/{provider}")
async def webhook(provider: str, request: Request):
    if provider not in webhooks.PROVIDERS:
        raise HTTPException(404, {"code": "UNKNOWN_PROVIDER", "message": provider})
    raw = await request.body()
    try:
        return await webhooks.receive(provider, dict(request.headers), raw)
    except webhooks.WebhookError as e:
        raise HTTPException(e.status, {"code": e.code, "message": str(e)})
