"""Webhook intake: raw-body HMAC verification, replay protection, job queue with jittered retries + dead letters."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import time
import uuid
from typing import Optional

import ledger
from db import db, now_ms

log = logging.getLogger("webhooks")
PROVIDERS = ("luchii", "paypal", "square", "cashapp", "okx")
MAX_AGE_S = 300
DELAYS = [30, 60, 300, 900, 1800, 3600, 7200, 21600, 86400]
RETRYABLE = ("timeout", "network", "rate", "temporar", "deadlock", "connection")


class WebhookError(Exception):
    def __init__(self, code: str, status: int, msg: str):
        super().__init__(msg)
        self.code, self.status = code, status


def _secret(provider: str) -> Optional[str]:
    return os.environ.get(f"WEBHOOK_SECRET_{provider.upper()}")


def verify(provider: str, headers: dict, raw: bytes) -> dict:
    """Returns {event_id, event_type, body}. Raises WebhookError on bad signature / replay window."""
    secret = _secret(provider)
    if not secret:
        raise WebhookError("PROVIDER_NOT_CONFIGURED", 503, f"{provider} webhook secret not configured")
    h = {k.lower(): v for k, v in headers.items()}
    if provider == "square":
        sig = h.get("x-square-hmacsha256-signature") or ""
        url = os.environ.get("WEBHOOK_PUBLIC_URL_SQUARE", "")
        expected = hmac.new(secret.encode(), (url + raw.decode()).encode(), hashlib.sha256).digest()
        import base64
        ok = hmac.compare_digest(base64.b64encode(expected).decode(), sig)
    elif provider == "okx":
        ts, sig = h.get("ok-access-timestamp") or "", h.get("ok-access-sign") or ""
        import base64
        expected = hmac.new(secret.encode(), (ts + "POST" + "/api/webhooks/okx" + raw.decode()).encode(), hashlib.sha256).digest()
        ok = hmac.compare_digest(base64.b64encode(expected).decode(), sig)
        _check_ts(ts)
    else:  # luchii-internal style (PayPal/CashApp use the same signed-envelope relay until provider SDK verification is wired)
        ts, sig = h.get("x-timestamp") or "", h.get("x-signature") or ""
        if not ts or not sig:
            raise WebhookError("INVALID_SIGNATURE", 401, "Missing signature headers")
        _check_ts(ts)
        expected = hmac.new(secret.encode(), f"{ts}.{raw.decode()}".encode(), hashlib.sha256).hexdigest()
        ok = hmac.compare_digest(expected, sig)
    if not ok:
        raise WebhookError("INVALID_SIGNATURE", 401, "Signature validation failed")
    try:
        body = json.loads(raw.decode() or "{}")
    except ValueError:
        raise WebhookError("INVALID_PAYLOAD", 400, "Body is not JSON")
    eid = h.get("x-event-id") or body.get("eventId") or body.get("event_id") or body.get("id")
    if not eid:
        raise WebhookError("INVALID_PAYLOAD", 400, "Missing event id")
    return {"event_id": str(eid), "event_type": body.get("eventType") or body.get("event_type") or body.get("type") or "unknown", "body": body}


def _check_ts(ts: str):
    try:
        t = float(ts)
        t = t / 1000 if t > 1e11 else t
    except ValueError:
        raise WebhookError("EXPIRED_TIMESTAMP", 401, "Bad timestamp")
    if abs(time.time() - t) > MAX_AGE_S:
        raise WebhookError("EXPIRED_TIMESTAMP", 401, "Timestamp outside 5-minute window")


async def receive(provider: str, headers: dict, raw: bytes) -> dict:
    ev = verify(provider, headers, raw)
    try:
        await db.webhook_receipts.insert_one({"id": str(uuid.uuid4()), "provider": provider, "event_id": ev["event_id"], "event_type": ev["event_type"],
                                              "signature": headers.get("x-signature") or headers.get("X-Signature"), "verified": True, "processed": False,
                                              "received_at": now_ms()})
    except Exception:
        raise WebhookError("DUPLICATE_EVENT", 409, "Duplicate webhook")
    job = {"id": str(uuid.uuid4()), "provider": provider, "event_id": ev["event_id"], "event_type": ev["event_type"], "payload": ev["body"],
           "status": "PENDING", "retry_count": 0, "max_retries": 10, "next_attempt_at": now_ms(), "last_error": None, "created_at": now_ms(), "processed_at": None}
    await db.webhook_jobs.insert_one(dict(job))
    return {"accepted": True, "jobId": job["id"], "eventId": ev["event_id"]}


def retry_delay(retries: int) -> int:
    base = DELAYS[min(retries, len(DELAYS) - 1)]
    return random.randint(0, base)  # full jitter


async def process_job(job: dict):
    p, body, et = job["provider"], job["payload"], job["event_type"]
    if p == "luchii" or p == "okx":
        if et in ("deposit.completed", "DEPOSIT"):
            await ledger.record_deposit(body["userId"], body["currency"], body["amount"], f"{p}:{job['event_id']}", provider=p.upper(), txid=body.get("txid"))
        elif et in ("withdrawal.completed", "WITHDRAWAL"):
            await ledger.record_withdrawal(body["userId"], body["currency"], body["amount"], f"{p}:{job['event_id']}", body.get("destination", ""),
                                           fee=body.get("fee", 0), provider=p.upper())
    elif p in ("paypal", "square", "cashapp"):
        if et in ("PAYMENT.CAPTURE.COMPLETED", "payment.completed", "payment.updated"):
            amt, uid = body.get("amount"), body.get("userId")
            if not (amt and uid):
                raise ValueError("Fiat payload missing amount/userId mapping")
            clearing = {"paypal": "PAYPAL_CLEARING_USD", "square": "SQUARE_CLEARING_USD", "cashapp": "CASHAPP_CLEARING_USD"}[p]
            await ledger.post_journal([{"account": clearing, "debit": amt}, {"account": ledger.user_code(uid, "USD", "PENDING"), "credit": amt}],
                                      source=p.upper(), description=f"{p} deposit pending settlement", reference_type="FIAT_DEPOSIT",
                                      reference_id=job["event_id"], idempotency_key=f"fiat:{p}:{job['event_id']}", actor_id=uid)
    await db.webhook_receipts.update_one({"provider": p, "event_id": job["event_id"]}, {"$set": {"processed": True, "processed_at": now_ms()}})


async def run_once() -> int:
    n = 0
    async for job in db.webhook_jobs.find({"status": "PENDING", "next_attempt_at": {"$lte": now_ms()}}).limit(20):
        n += 1
        await db.webhook_jobs.update_one({"id": job["id"]}, {"$set": {"status": "PROCESSING"}})
        try:
            await asyncio.wait_for(process_job(job), timeout=30)
            await db.webhook_jobs.update_one({"id": job["id"]}, {"$set": {"status": "COMPLETED", "processed_at": now_ms()}})
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            retryable = any(k in msg.lower() for k in RETRYABLE) or isinstance(e, asyncio.TimeoutError)
            rc = job["retry_count"] + 1
            if retryable and rc < job["max_retries"]:
                await db.webhook_jobs.update_one({"id": job["id"]}, {"$set": {"status": "PENDING", "retry_count": rc, "last_error": msg,
                                                                              "next_attempt_at": now_ms() + retry_delay(rc) * 1000}})
            else:
                await db.webhook_jobs.update_one({"id": job["id"]}, {"$set": {"status": "FAILED", "retry_count": rc, "last_error": msg}})
                await db.webhook_dead_letters.insert_one({"id": str(uuid.uuid4()), "webhook_job_id": job["id"], "provider": job["provider"],
                                                          "event_id": job["event_id"], "event_type": job["event_type"], "payload": job["payload"],
                                                          "error_message": msg, "failed_at": now_ms(), "replay_status": "NOT_REPLAYED", "replay_attempts": 0})
                log.error("webhook job %s dead-lettered: %s", job["id"], msg)
    return n


async def worker():
    while True:
        try:
            await run_once()
            await ledger.publish_outbox()
        except Exception as e:  # noqa: BLE001
            log.exception("webhook worker: %s", e)
        await asyncio.sleep(5)


async def replay_dead_letter(dl_id: str, actor_id: str) -> dict:
    r = await db.webhook_dead_letters.find_one_and_update({"id": dl_id, "replay_status": "NOT_REPLAYED"},
                                                          {"$set": {"replay_status": "REPLAY_QUEUED", "replayed_at": now_ms(), "replayed_by": actor_id},
                                                           "$inc": {"replay_attempts": 1}})
    if not r:
        raise WebhookError("ALREADY_REPLAYED", 409, "Dead letter missing or already replayed")
    job = {"id": f"replay:{dl_id}", "provider": r["provider"], "event_id": r["event_id"], "event_type": r["event_type"], "payload": r["payload"],
           "status": "PENDING", "retry_count": 0, "max_retries": 10, "next_attempt_at": now_ms(), "last_error": None, "created_at": now_ms(),
           "processed_at": None, "replay_of": dl_id}
    await db.webhook_jobs.update_one({"id": job["id"]}, {"$setOnInsert": job}, upsert=True)
    await ledger.audit_event(actor_id, "webhook_dead_letter", dl_id, "DEAD_LETTER_REPLAY", {"replay_status": "NOT_REPLAYED"}, {"replay_status": "REPLAY_QUEUED"})
    return {"queued": True, "jobId": job["id"]}
