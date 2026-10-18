"""Double-entry ledger on MongoDB. Posted journals are immutable; corrections only via reversal journals."""
import asyncio
import hashlib
import json
import uuid
from decimal import Decimal
from typing import Optional

from pymongo.errors import DuplicateKeyError

from db import db, now_ms

EPS = Decimal("0.00000001")
TYPES = ("ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE")
DEBIT_NORMAL = ("ASSET", "EXPENSE")
USER_CCY = ("USD", "USDT", "BTC", "ETH", "SOL")
PERFORMANCE_FEE_RATE = 0.02  # 2% of realised profit per winning close → PERFORMANCE_FEES_USD

SYSTEM_ACCOUNTS = [
    ("TREASURY_USD", "Treasury USD", "ASSET", "USD"), ("TREASURY_USDT", "Treasury USDT", "ASSET", "USDT"), ("TREASURY_BTC", "Treasury BTC", "ASSET", "BTC"),
    ("PAYPAL_CLEARING_USD", "PayPal Clearing", "ASSET", "USD"), ("CASHAPP_CLEARING_USD", "Cash App Clearing", "ASSET", "USD"), ("SQUARE_CLEARING_USD", "Square Clearing", "ASSET", "USD"),
    ("OKX_MASTER_USD", "OKX Master USD", "ASSET", "USD"), ("OKX_MASTER_USDT", "OKX Master USDT", "ASSET", "USDT"), ("OKX_MASTER_BTC", "OKX Master BTC", "ASSET", "BTC"),
    ("USER_PENDING_USD", "User Pending USD", "LIABILITY", "USD"), ("USER_PENDING_USDT", "User Pending USDT", "LIABILITY", "USDT"), ("USER_PENDING_BTC", "User Pending BTC", "LIABILITY", "BTC"),
    ("CHARGEBACK_RESERVE_USD", "Chargeback Reserve", "LIABILITY", "USD"),
    ("PLATFORM_REVENUE_USD", "Platform Revenue", "REVENUE", "USD"), ("PERFORMANCE_FEES_USD", "Performance Fees", "REVENUE", "USD"),
    ("TRADING_PNL_USD", "Trading PnL", "EQUITY", "USD"), ("RETAINED_EARNINGS_USD", "Retained Earnings", "EQUITY", "USD"),
    ("EXCHANGE_FEES_USD", "Exchange Fees", "EXPENSE", "USD"), ("NETWORK_FEES_USD", "Network Fees", "EXPENSE", "USD"),
    ("CHARGEBACKS_USD", "Chargebacks", "EXPENSE", "USD"), ("REFUNDS_USD", "Refunds", "EXPENSE", "USD"),
]


class LedgerError(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _req_hash(lines) -> str:
    return hashlib.sha256(json.dumps(lines, sort_keys=True, default=str).encode()).hexdigest()


def D(x) -> Decimal:
    return Decimal(str(x or 0))


def _s(x: Decimal) -> str:
    return format(x.normalize(), "f")


async def ensure_indexes():
    await db.ledger_accounts.create_index("code", unique=True)
    await db.ledger_accounts.create_index([("user_id", 1), ("currency", 1)])
    await db.journal_entries.create_index("idempotency_key", unique=True, partialFilterExpression={"idempotency_key": {"$type": "string"}})
    await db.journal_entries.create_index([("reference_type", 1), ("reference_id", 1)])
    await db.journal_entries.create_index([("created_at", -1)])
    await db.journal_lines.create_index("journal_entry_id")
    await db.journal_lines.create_index([("account_id", 1), ("created_at", -1)])
    await db.ledger_reversals.create_index("original_journal_entry_id", unique=True)
    await db.audit_events.create_index([("entity_type", 1), ("entity_id", 1)])
    await db.audit_events.create_index([("created_at", -1)])
    await db.audit_events.create_index([("chain_sequence", 1)], unique=True)
    await db.idempotency_keys.create_index([("operation_type", 1), ("idempotency_key", 1)], unique=True)
    await db.webhook_receipts.create_index([("provider", 1), ("event_id", 1)], unique=True)
    await db.webhook_jobs.create_index([("status", 1), ("next_attempt_at", 1)])
    await db.outbox_events.create_index("event_key", unique=True)
    await db.outbox_events.create_index([("published_at", 1), ("created_at", 1)])


async def publish_outbox(limit: int = 200) -> int:
    """Claim-then-publish; today the bus is the realtime log stream (SSE poll), so publishing == marking visible."""
    n = 0
    async for ev in db.outbox_events.find({"published_at": None}).sort("created_at", 1).limit(limit):
        r = await db.outbox_events.update_one({"id": ev["id"], "published_at": None}, {"$set": {"published_at": now_ms()}, "$inc": {"publish_attempts": 1}})
        n += r.modified_count
    return n


async def seed_system_accounts():
    for code, name, typ, ccy in SYSTEM_ACCOUNTS:
        await db.ledger_accounts.update_one({"code": code}, {"$setOnInsert": {
            "id": str(uuid.uuid4()), "code": code, "name": name, "account_type": typ, "currency": ccy,
            "user_id": None, "is_system": True, "is_active": True, "created_at": now_ms()}}, upsert=True)


async def provision_user(uid: str):
    for ccy in USER_CCY:
        for kind, typ in (("WALLET", "LIABILITY"), ("PENDING", "LIABILITY"), ("TRADING", "LIABILITY")):
            code = f"USER_{uid}_{ccy}_{kind}"
            await db.ledger_accounts.update_one({"code": code}, {"$setOnInsert": {
                "id": str(uuid.uuid4()), "code": code, "name": f"User {ccy} {kind.lower()}", "account_type": typ, "currency": ccy,
                "user_id": uid, "is_system": False, "is_active": True, "created_at": now_ms()}}, upsert=True)


async def account(code: str) -> dict:
    a = await db.ledger_accounts.find_one({"code": code}, {"_id": 0})
    if not a:
        raise LedgerError(f"Unknown ledger account {code}")
    return a


def user_code(uid: str, ccy: str, kind: str = "WALLET") -> str:
    return f"USER_{uid}_{ccy.upper()}_{kind}"


async def balance(account_id: str, include_pending: bool = False) -> Decimal:
    st = {"$in": ["POSTED", "PENDING"]} if include_pending else "POSTED"
    agg = await db.journal_lines.aggregate([
        {"$match": {"account_id": account_id, "status": st}},
        {"$group": {"_id": None, "d": {"$sum": {"$toDecimal": "$debit"}}, "c": {"$sum": {"$toDecimal": "$credit"}}}}]).to_list(1)
    if not agg:
        return Decimal(0)
    a = await db.ledger_accounts.find_one({"id": account_id}, {"account_type": 1})
    d, c = D(agg[0]["d"]), D(agg[0]["c"])
    return d - c if a["account_type"] in DEBIT_NORMAL else c - d


async def post_journal(lines: list[dict], *, source: str, description: str, reference_type: str = None, reference_id: str = None,
                       idempotency_key: str = None, is_conversion: bool = False, actor_id: str = None) -> dict:
    """lines: [{"account": CODE, "debit": x} | {"account": CODE, "credit": x}]. Validates then posts atomically-enough (insert lines, then flip to POSTED)."""
    if idempotency_key:
        ex = await db.journal_entries.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
        if ex and ex.get("status") == "REJECTED":
            await db.journal_entries.delete_one({"id": ex["id"]})
            ex = None
        if ex:
            if ex.get("request_hash") and ex["request_hash"] != _req_hash(lines):
                raise LedgerError("Idempotency-Key reused with a different payload", 409)
            ex["lines"] = await db.journal_lines.find({"journal_entry_id": ex["id"]}, {"_id": 0}).to_list(None)
            return ex
    if len(lines) < 2:
        raise LedgerError("A journal needs at least two lines")
    accts, by_ccy, ccys, out = {}, {}, set(), []
    for ln in lines:
        if not isinstance(ln, dict) or not ln.get("account"):
            raise LedgerError("Each line needs an 'account' code")
        a = accts.get(ln["account"]) or await account(ln["account"])
        accts[ln["account"]] = a
        try:
            d, c = D(ln.get("debit")), D(ln.get("credit"))
        except Exception:
            raise LedgerError("debit/credit must be numeric")
        if d < 0 or c < 0 or (d > 0) == (c > 0):
            raise LedgerError(f"Line on {a['code']} must have exactly one positive side")
        ccy = (ln.get("currency") or a["currency"]).upper()
        if ccy != a["currency"]:
            raise LedgerError(f"Currency mismatch account={a['currency']} posting={ccy}")
        ccys.add(ccy)
        td, tc = by_ccy.get(ccy, (Decimal(0), Decimal(0)))
        by_ccy[ccy] = (td + d, tc + c)
        out.append({"id": str(uuid.uuid4()), "account_id": a["id"], "account_code": a["code"], "currency": ccy, "debit": _s(d), "credit": _s(c)})
    for ccy, (td, tc) in by_ccy.items():
        if abs(td - tc) > EPS:
            raise LedgerError(f"Journal out of balance in {ccy} ({_s(td)} vs {_s(tc)})")
    if len(ccys) > 1 and not is_conversion:
        raise LedgerError("Multiple currencies in one journal require is_conversion=True")
    jid = str(uuid.uuid4())
    je = {"id": jid, "source": source, "reference_type": reference_type, "reference_id": reference_id, "idempotency_key": idempotency_key,
          "request_hash": _req_hash(lines) if idempotency_key else None,
          "description": description, "is_conversion": is_conversion, "status": "PENDING", "created_at": now_ms(), "posted_at": None, "actor_id": actor_id}
    try:
        await db.journal_entries.insert_one(dict(je))
    except DuplicateKeyError:
        return await post_journal(lines, source=source, description=description, reference_type=reference_type, reference_id=reference_id,
                                  idempotency_key=idempotency_key, is_conversion=is_conversion, actor_id=actor_id)
    for ln in out:
        ln.update(journal_entry_id=jid, created_at=je["created_at"], status="PENDING")
    await db.journal_lines.insert_many([dict(ln) for ln in out])
    # liability accounts may not go negative once this journal lands (PENDING lines of concurrent journals count too)
    for a in accts.values():
        if a["account_type"] == "LIABILITY":
            if await balance(a["id"], include_pending=True) < -EPS:
                await db.journal_lines.delete_many({"journal_entry_id": jid})
                await db.journal_entries.update_one({"id": jid}, {"$set": {"status": "REJECTED", "error": f"Negative liability balance on {a['code']}"}})
                raise LedgerError(f"Insufficient balance on {a['code']}")
    await db.journal_lines.update_many({"journal_entry_id": jid}, {"$set": {"status": "POSTED"}})
    await db.journal_entries.update_one({"id": jid}, {"$set": {"status": "POSTED", "posted_at": now_ms()}})
    await db.outbox_events.update_one({"event_key": f"journal.posted:{jid}"}, {"$setOnInsert": {
        "id": str(uuid.uuid4()), "aggregate_type": "JournalEntry", "aggregate_id": jid, "event_type": "journal.posted", "event_key": f"journal.posted:{jid}",
        "payload": {"journalId": jid, "referenceType": reference_type, "description": description}, "created_at": now_ms(), "published_at": None, "publish_attempts": 0}}, upsert=True)
    await audit_event(actor_id, "journal_entry", jid, "POST", None, {"description": description, "lines": out})
    je.update(status="POSTED", lines=[{k: v for k, v in ln.items() if k != "_id"} for ln in out])
    return je


async def reverse_journal(original_id: str, reason: str, actor_id: str = None) -> dict:
    # claim first: atomically flip POSTED → REVERSING so concurrent reversals lose here, before anything is posted
    orig = await db.journal_entries.find_one_and_update({"id": original_id, "status": "POSTED"}, {"$set": {"status": "REVERSING"}}, projection={"_id": 0})
    if not orig:
        cur = await db.journal_entries.find_one({"id": original_id}, {"status": 1})
        raise LedgerError("Journal already reversed" if cur and cur.get("status") in ("REVERSING", "REVERSED") else "Original journal not found or not posted")
    lines = await db.journal_lines.find({"journal_entry_id": original_id}, {"_id": 0}).to_list(None)
    flipped = [{"account": ln["account_code"], "debit": ln["credit"], "credit": ln["debit"]} for ln in lines]
    try:
        rev = await post_journal(flipped, source="SYSTEM", description=f"REVERSAL of {original_id}: {reason}", reference_type="REVERSAL",
                                 reference_id=original_id, idempotency_key=f"rev:{original_id}", is_conversion=orig.get("is_conversion", False), actor_id=actor_id)
    except LedgerError:
        await db.journal_entries.update_one({"id": original_id, "status": "REVERSING"}, {"$set": {"status": "POSTED"}})
        raise
    await db.journal_entries.update_one({"id": original_id}, {"$set": {"status": "REVERSED", "reversed_by": rev["id"]}})
    doc = {"id": str(uuid.uuid4()), "original_journal_entry_id": original_id, "reversal_journal_entry_id": rev["id"], "reason": reason,
           "initiated_by": actor_id, "created_at": now_ms()}
    await db.ledger_reversals.insert_one(dict(doc))
    await audit_event(actor_id, "journal_entry", original_id, "REVERSE", {"status": "POSTED"}, {"status": "REVERSED", "reversal": rev["id"]})
    return doc


_audit_lock = asyncio.Lock()


async def audit_event(actor_id, entity_type, entity_id, action, old, new, ip=None, ua=None, success=True):
    """Hash-chained, append-only audit log. Chain head is serialised by a process lock + singleton doc."""
    async with _audit_lock:
        head = await db.audit_chain_head.find_one({"_id": 1}) or {"latest_hash": "GENESIS", "sequence": 0}
        seq = head["sequence"] + 1
        ev = {"id": str(uuid.uuid4()), "chain_sequence": seq, "actor_id": actor_id, "entity_type": entity_type, "entity_id": str(entity_id),
              "action": action, "old_values": old, "new_values": new, "ip_address": ip, "user_agent": ua, "success": success, "created_at": now_ms(),
              "previous_hash": head["latest_hash"]}
        ev["record_hash"] = hashlib.sha256(f"{seq}|{head['latest_hash']}|{json.dumps({k: ev[k] for k in ('actor_id', 'entity_type', 'entity_id', 'action', 'old_values', 'new_values', 'created_at')}, sort_keys=True, default=str)}".encode()).hexdigest()
        await db.audit_events.insert_one(dict(ev))
        await db.audit_chain_head.update_one({"_id": 1}, {"$set": {"latest_hash": ev["record_hash"], "latest_event_id": ev["id"], "sequence": seq, "updated_at": now_ms()}}, upsert=True)
        return ev


async def verify_audit_chain() -> dict:
    prev, n = "GENESIS", 0
    async for ev in db.audit_events.find({}, {"_id": 0}).sort("chain_sequence", 1):
        n += 1
        expect = hashlib.sha256(f"{ev['chain_sequence']}|{prev}|{json.dumps({k: ev.get(k) for k in ('actor_id', 'entity_type', 'entity_id', 'action', 'old_values', 'new_values', 'created_at')}, sort_keys=True, default=str)}".encode()).hexdigest()
        if ev.get("previous_hash") != prev or ev.get("record_hash") != expect:
            return {"ok": False, "events": n, "broken_at": ev["chain_sequence"]}
        prev = ev["record_hash"]
    return {"ok": True, "events": n}


async def idempotent(key: str, operation: str, payload: dict) -> Optional[dict]:
    """Returns the stored record if the key was seen before (same payload), else claims it atomically via the unique index."""
    h = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    try:
        await db.idempotency_keys.insert_one({"id": str(uuid.uuid4()), "idempotency_key": key, "operation_type": operation, "request_hash": h,
                                              "status": "IN_PROGRESS", "resource_id": None, "response": None, "created_at": now_ms()})
        return None
    except Exception:
        ex = await db.idempotency_keys.find_one({"operation_type": operation, "idempotency_key": key}, {"_id": 0})
        if ex and ex["request_hash"] != h:
            raise LedgerError("Idempotency-Key reused with a different payload")
        return ex


async def idempotent_done(key: str, operation: str, resource_id: str, response: dict = None):
    await db.idempotency_keys.update_one({"operation_type": operation, "idempotency_key": key},
                                         {"$set": {"status": "COMPLETED", "resource_id": resource_id, "response": response, "completed_at": now_ms()}})


# ---------- domain postings (one user == one OKX account today; OKX_MASTER_* mirrors the exchange side) ----------
def _master(ccy: str) -> str:
    return {"USD": "OKX_MASTER_USD", "USDT": "OKX_MASTER_USDT", "BTC": "OKX_MASTER_BTC"}.get(ccy.upper(), "OKX_MASTER_USD")


async def record_deposit(uid: str, ccy: str, amount, ref: str, provider: str = "OKX", txid: str = None) -> dict:
    """Exchange-confirmed deposit: DR OKX_MASTER_ccy / CR USER_ccy_WALLET. Idempotent on provider reference."""
    ccy = ccy.upper()
    je = await post_journal([{"account": _master(ccy) if ccy in ("USD", "USDT", "BTC") else f"TREASURY_{ccy}", "debit": amount},
                             {"account": user_code(uid, ccy), "credit": amount}],
                            source=provider, description=f"Deposit {amount} {ccy}", reference_type="DEPOSIT", reference_id=ref,
                            idempotency_key=f"dep:{provider}:{ref}", actor_id=uid)
    await db.deposits.update_one({"provider_reference": ref}, {"$setOnInsert": {"id": str(uuid.uuid4()), "user_id": uid, "provider": provider,
                                 "provider_reference": ref, "amount": str(amount), "currency": ccy, "status": "SETTLED", "txid": txid,
                                 "journal_entry_id": je["id"], "created_at": now_ms()}}, upsert=True)
    return je


async def record_withdrawal(uid: str, ccy: str, amount, ref: str, destination: str, fee=0, provider: str = "OKX") -> dict:
    ccy = ccy.upper()
    lines = [{"account": user_code(uid, ccy), "debit": D(amount) + D(fee)}, {"account": _master(ccy), "credit": amount}]
    if D(fee) > 0 and ccy == "USD":
        lines.append({"account": "NETWORK_FEES_USD", "credit": fee})
    elif D(fee) > 0:
        lines[1]["credit"] = D(amount) + D(fee)
    je = await post_journal(lines, source=provider, description=f"Withdrawal {amount} {ccy} → {destination[:10]}…", reference_type="WITHDRAWAL",
                            reference_id=ref, idempotency_key=f"wd:{provider}:{ref}", actor_id=uid)
    await db.withdrawals.update_one({"provider_reference": ref}, {"$setOnInsert": {"id": str(uuid.uuid4()), "user_id": uid, "amount": str(amount),
                                    "currency": ccy, "destination_type": "CRYPTO", "destination_reference": destination, "status": "SUBMITTED",
                                    "provider_reference": ref, "journal_entry_id": je["id"], "created_at": now_ms()}}, upsert=True)
    return je


async def record_trade_pnl(uid: str, trade_id: str, pnl, fee, asset: str, side: str, qty, price) -> Optional[dict]:
    """Realised P&L in USD terms: profit CR user wallet via TRADING_PNL; loss DR user wallet. Fee booked to EXCHANGE_FEES_USD."""
    pnl, fee = D(pnl), D(fee)
    lines = []
    perf_fee = (pnl * Decimal(str(PERFORMANCE_FEE_RATE))).quantize(Decimal("0.000001")) if pnl > EPS else Decimal(0)
    if pnl > EPS:
        lines += [{"account": "OKX_MASTER_USD", "debit": pnl}, {"account": "TRADING_PNL_USD", "credit": pnl},
                  {"account": "TRADING_PNL_USD", "debit": pnl}, {"account": user_code(uid, "USD"), "credit": pnl}]
        if perf_fee > EPS:
            lines += [{"account": user_code(uid, "USD"), "debit": perf_fee}, {"account": "PERFORMANCE_FEES_USD", "credit": perf_fee}]
    elif pnl < -EPS:
        lines += [{"account": user_code(uid, "USD"), "debit": -pnl}, {"account": "OKX_MASTER_USD", "credit": -pnl}]
    if fee > EPS:
        lines += [{"account": "EXCHANGE_FEES_USD", "debit": fee}, {"account": "OKX_MASTER_USD", "credit": fee}]
    if not lines:
        return None
    try:
        return await post_journal(lines, source="OKX", description=f"{side} {qty} {asset} @ {price} · pnl {pnl} fee {fee} · perf fee {perf_fee}",
                                  reference_type="TRADE_SETTLEMENT", reference_id=trade_id, idempotency_key=f"trade:{trade_id}", actor_id=uid)
    except LedgerError:
        # user wallet not funded in-ledger yet (pre-ledger balances) → book against pending so nothing is silently lost
        lines = [{"account": "USER_PENDING_USD", **({"debit": -pnl} if pnl < 0 else {"credit": pnl})} if ln["account"] == user_code(uid, "USD") else ln for ln in lines]
        return await post_journal(lines, source="OKX", description=f"{side} {qty} {asset} @ {price} · pnl {pnl} fee {fee} (pending)",
                                  reference_type="TRADE_SETTLEMENT", reference_id=trade_id, idempotency_key=f"trade:{trade_id}", actor_id=uid)


async def user_wallets(uid: str) -> list[dict]:
    out = []
    for ccy in USER_CCY:
        w = await db.ledger_accounts.find_one({"code": user_code(uid, ccy)}, {"_id": 0})
        p = await db.ledger_accounts.find_one({"code": user_code(uid, ccy, "PENDING")}, {"_id": 0})
        t = await db.ledger_accounts.find_one({"code": user_code(uid, ccy, "TRADING")}, {"_id": 0})
        if not w:
            continue
        a, pb = await balance(w["id"]), (await balance(p["id"]) if p else Decimal(0))
        tb = await balance(t["id"]) if t else Decimal(0)
        out.append({"id": w["id"], "currency": ccy, "available": float(a), "pending": float(pb), "trading": float(tb), "total": float(a + pb + tb)})
    return out


async def journals(uid: Optional[str], page: int, size: int, admin: bool = False) -> dict:
    q = {} if admin else {"$or": [{"actor_id": uid}, {"id": {"$in": await _user_journal_ids(uid)}}]}
    total = await db.journal_entries.count_documents(q)
    items = await db.journal_entries.find(q, {"_id": 0}).sort("created_at", -1).skip((page - 1) * size).limit(size).to_list(size)
    for je in items:
        je["lines"] = await db.journal_lines.find({"journal_entry_id": je["id"]}, {"_id": 0}).to_list(None)
    return {"items": items, "pagination": {"page": page, "pageSize": size, "total": total, "totalPages": max(1, -(-total // size))}}


async def _user_journal_ids(uid: str) -> list[str]:
    ids = [a["id"] async for a in db.ledger_accounts.find({"user_id": uid}, {"id": 1})]
    return await db.journal_lines.distinct("journal_entry_id", {"account_id": {"$in": ids}})


async def all_balances() -> list[dict]:
    out = []
    async for a in db.ledger_accounts.find({}, {"_id": 0}).sort("code", 1):
        out.append({**a, "balance": float(await balance(a["id"]))})
    return out


async def trial_balance() -> dict:
    agg = await db.journal_lines.aggregate([{"$match": {"status": "POSTED"}},
                                            {"$group": {"_id": "$currency", "d": {"$sum": {"$toDecimal": "$debit"}}, "c": {"$sum": {"$toDecimal": "$credit"}}}}]).to_list(None)
    return {r["_id"]: {"debits": float(D(r["d"])), "credits": float(D(r["c"])), "balanced": abs(D(r["d"]) - D(r["c"])) <= EPS} for r in agg}
