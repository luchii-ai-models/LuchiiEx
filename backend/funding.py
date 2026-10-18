"""Fiat rails (PayPal / Cash App / Square) → ledger → trading bridge.
Settled fiat is booked DR <PROVIDER>_CLEARING_USD / CR user wallet, then auto-bridged into the user's TRADING allocation,
which the engine trades through the house (MASTER) OKX account. A funding-transfer ticket is queued for treasury to move
the fiat payout onto OKX physically."""
import os
import uuid
from decimal import Decimal

import ledger
from db import db, now_ms

CLEARING = {"PAYPAL": "PAYPAL_CLEARING_USD", "CASHAPP": "CASHAPP_CLEARING_USD", "SQUARE": "SQUARE_CLEARING_USD"}
RAILS = {
    "paypal": ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"),
    "square": ("SQUARE_ACCESS_TOKEN", "SQUARE_LOCATION_ID"),
    "cashapp": ("CASHAPP_CLIENT_ID", "CASHAPP_API_KEY"),
}


def rail_status(rail: str) -> dict:
    keys = RAILS[rail]
    return {"rail": rail, "configured": all(os.environ.get(k) for k in keys), "env": os.environ.get(f"{rail.upper()}_ENV", "sandbox"), "needs": list(keys)}


async def trading_capital(uid: str) -> Decimal:
    acct = await db.ledger_accounts.find_one({"code": ledger.user_code(uid, "USD", "TRADING")}, {"id": 1})
    return await ledger.balance(acct["id"]) if acct else Decimal(0)


async def bridge_to_trading(uid: str, amount, ref: str) -> dict | None:
    """Move settled USD from the user's wallet into their TRADING allocation and queue the OKX funding ticket."""
    amt = ledger.D(amount)
    if amt <= 0:
        return None
    je = await ledger.post_journal([{"account": ledger.user_code(uid, "USD"), "debit": amt}, {"account": ledger.user_code(uid, "USD", "TRADING"), "credit": amt}],
                                   source="SYSTEM", description=f"Bridge ${amt} to trading", reference_type="TRADING_ALLOCATION", reference_id=ref,
                                   idempotency_key=f"bridge:{ref}", actor_id=uid)
    await db.okx_transfers.update_one({"reference_id": ref}, {"$setOnInsert": {
        "id": str(uuid.uuid4()), "user_id": uid, "reference_id": ref, "direction": "FIAT_TO_OKX", "currency": "USD", "amount": str(amt),
        "status": "QUEUED", "journal_entry_id": je["id"], "created_at": now_ms(), "settled_at": None}}, upsert=True)
    cap = await trading_capital(uid)
    await db.bots.update_one({"user_id": uid}, {"$set": {"ledger_capital": float(cap)}})
    return je


async def settle_fiat_deposit(uid: str, provider: str, ref: str, amount) -> dict:
    """Idempotent on provider reference. Books the deposit and bridges it to trading in one go."""
    provider = provider.upper()
    amt = ledger.D(amount)
    je = await ledger.post_journal([{"account": CLEARING[provider], "debit": amt}, {"account": ledger.user_code(uid, "USD"), "credit": amt}],
                                   source=provider, description=f"{provider.title()} deposit ${amt}", reference_type=f"{provider}_DEPOSIT", reference_id=ref,
                                   idempotency_key=f"dep:{provider}:{ref}", actor_id=uid)
    await db.deposits.update_one({"provider_reference": ref}, {"$setOnInsert": {
        "id": str(uuid.uuid4()), "user_id": uid, "provider": provider, "provider_reference": ref, "amount": str(amt), "currency": "USD",
        "status": "SETTLED", "journal_entry_id": je["id"], "created_at": now_ms()}}, upsert=True)
    bridge = await bridge_to_trading(uid, amt, ref)
    return {"journalId": je["id"], "bridgeJournalId": bridge["id"] if bridge else None, "tradingCapital": float(await trading_capital(uid))}


async def settle_transfer(transfer_id: str, actor_id: str, okx_ref: str | None) -> dict:
    """Treasury confirms the fiat payout landed on OKX: DR OKX_MASTER_USD / CR <clearing>."""
    t = await db.okx_transfers.find_one({"id": transfer_id}, {"_id": 0})
    if not t:
        raise ledger.LedgerError("Unknown transfer", 404)
    if t["status"] == "SETTLED":
        return t
    dep = await db.deposits.find_one({"provider_reference": t["reference_id"]}, {"provider": 1})
    clearing = CLEARING.get((dep or {}).get("provider", "PAYPAL"), "PAYPAL_CLEARING_USD")
    je = await ledger.post_journal([{"account": "OKX_MASTER_USD", "debit": t["amount"]}, {"account": clearing, "credit": t["amount"]}],
                                   source="OKX", description=f"Fiat payout → OKX ${t['amount']}", reference_type="OKX_FUNDING_TRANSFER", reference_id=transfer_id,
                                   idempotency_key=f"okxfund:{transfer_id}", actor_id=actor_id)
    await db.okx_transfers.update_one({"id": transfer_id}, {"$set": {"status": "SETTLED", "settled_at": now_ms(), "okx_reference": okx_ref, "settle_journal_id": je["id"]}})
    return {**t, "status": "SETTLED", "settle_journal_id": je["id"]}
