import asyncio
import concurrent.futures
import hmac
import os
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from auth import current_user
from db import db, now_ms
from research import registry
from research.gates import GateConfigError, RollbackMonitor, load_gates
from research.stress import health_score

r = APIRouter(prefix="/research")
JOBS: dict[str, dict] = {}


def _admin(user):
    if user.get("role") not in ("admin", "owner", "treasury", "risk"):
        raise HTTPException(403, {"code": "FORBIDDEN", "message": "risk/admin role required"})


@r.get("/gates")
async def gates(user=Depends(current_user)):
    try:
        return {"path": "backend/risk_gates.yaml", "config": load_gates().model_dump()}
    except GateConfigError as e:
        raise HTTPException(500, {"code": "GATE_CONFIG_INVALID", "message": str(e)[:300]})


async def _run(uid: str, asset: str, days: int, auto_rollback: bool = False):
    key = f"{asset}"
    try:
        from research.runner import validation_job
        JOBS[key].update(status="running", stage="fetch candles · backtest v2 · MC · crash tests (worker process)")
        with concurrent.futures.ProcessPoolExecutor(max_workers=1) as pool:
            report = await asyncio.get_running_loop().run_in_executor(pool, validation_job, asset, days)
        rec = await registry.add({"strategy": report["strategy"], "version": report["version"], "asset": asset, "status": report["promotion"]["status"],
                                  "score": report["promotion"]["score"], "reasons": report["promotion"]["reasons"], "actor": uid, "report": report})
        JOBS[key] = {"status": "done", "ts": now_ms(), "promotion": report["promotion"]["status"], "id": rec["id"]}
        if auto_rollback and report["promotion"]["status"] == "REJECT":
            prev = await db.promotions.find_one({"strategy": report["strategy"], "asset": asset, "status": "PROMOTE", "ts": {"$lt": rec["ts"]}}, {"_id": 1})
            if prev:
                await registry.rollback(report["strategy"], report["version"], [f"nightly revalidation {asset}: " + "; ".join(report["promotion"]["reasons"][:3])], f"nightly:{asset}:{rec['id']}", "cron")
    except Exception as e:  # noqa: BLE001
        JOBS[key] = {"status": "error", "error": str(e)[:300], "ts": now_ms()}


async def _sweep(run_id: str):
    for a in ("BTC", "ETH", "SOL"):
        JOBS[a] = {"status": "queued", "ts": now_ms(), "run_id": run_id}
        await _run("cron", a, 30, auto_rollback=True)
    await db.cron_runs.update_one({"run_id": run_id}, {"$set": {"finished_at": now_ms(), "results": {a: JOBS[a] for a in ("BTC", "ETH", "SOL")}}})


@r.post("/cron-revalidate", include_in_schema=False)
async def _unused():  # keeps router prefix clean; real cron route is registered below on /api/cron/revalidate
    raise HTTPException(404)


cron = APIRouter(prefix="/cron")


@cron.post("/revalidate", status_code=202)
async def cron_revalidate(request: Request, authorization: Optional[str] = Header(None), x_webhook_id: Optional[str] = Header(None)):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    secret = os.environ["WEBHOOK_CRON_SECRET"]
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not authorization or not authorization.startswith("Bearer ") or not hmac.compare_digest(token, secret):
        raise HTTPException(401, "unauthorized")
    try:
        body = await request.json() if await request.body() else {}
    except ValueError:
        raise HTTPException(400, "invalid body")
    run_id = x_webhook_id or body.get("run_id") or str(uuid.uuid4())
    if await db.cron_runs.find_one({"run_id": run_id}, {"_id": 1}):
        return {"ok": True, "duplicate": True, "run_id": run_id}
    await db.cron_runs.insert_one({"run_id": run_id, "job": "revalidate", "started_at": now_ms(), "event": body.get("event")})
    asyncio.create_task(_sweep(run_id))
    return {"ok": True, "queued": ["BTC", "ETH", "SOL"], "run_id": run_id}


@r.post("/run/{asset}")
async def run(asset: Literal["BTC", "ETH", "SOL"], days: int = 30, user=Depends(current_user)):
    _admin(user)
    if JOBS.get(asset, {}).get("status") in ("queued", "running"):
        return {"status": JOBS[asset]["status"], "asset": asset}
    JOBS[asset] = {"status": "queued", "ts": now_ms()}
    asyncio.create_task(_run(user["id"], asset, max(7, min(days, 90))))
    return {"status": "queued", "asset": asset}


@r.get("/status")
async def status(user=Depends(current_user)):
    return {"jobs": JOBS}


@r.get("/latest")
async def latest(user=Depends(current_user)):
    out = {}
    for a in ("BTC", "ETH", "SOL"):
        doc = await db.promotions.find_one({"strategy": "luchii003", "asset": a, "status": {"$in": ["PROMOTE", "REJECT"]}}, {"_id": 0}, sort=[("ts", -1)])
        if doc:
            ok, why = await registry.is_promoted("luchii003", a)
            rep = doc.pop("report", {}) or {}
            out[a] = {**doc, "live_allowed": ok, "live_reason": why, "backtest": rep.get("backtest"), "monte_carlo": rep.get("monte_carlo"), "stress": rep.get("stress"),
                      "benchmark": rep.get("benchmark"), "execution": rep.get("execution"), "health": rep.get("health"), "checks": rep.get("promotion", {}).get("checks"),
                      "equity_curve": rep.get("equity_curve"), "drawdown_curve": rep.get("drawdown_curve"), "replay_ok": rep.get("replay_ok"), "events": rep.get("events"),
                      "candles": rep.get("candles"), "days": rep.get("days"), "runtime_s": rep.get("runtime_s")}
        else:
            out[a] = {"status": "NONE", "live_allowed": False, "live_reason": "no promotion run yet"}
    return {"strategy": "luchii003", "assets": out, "history": await registry.history("luchii003", 20)}


class RollbackIn(BaseModel):
    version: str = "manual"
    reasons: list[str] = ["manual rollback"]
    eventId: Optional[str] = None


@r.post("/rollback")
async def rollback(body: RollbackIn, user=Depends(current_user)):
    _admin(user)
    return await registry.rollback("luchii003", body.version, body.reasons, body.eventId or str(uuid.uuid4()), user["id"])


@r.get("/live-risk")
async def live_risk(user=Depends(current_user)):
    """Runtime risk card for this user's bot: drawdown, exposure, health, rollback policy evaluation."""
    bot = await db.bots.find_one({"user_id": user["id"]}, {"_id": 0}) or {}
    eq = float(bot.get("live_equity") or 0)
    start = float(bot.get("start_equity") or eq or 0)
    dd = max(0.0, (start - eq) / start * 100) if start > 0 else 0.0
    exposure = 0.0
    from engine import pos_of, mkt
    for a in bot.get("assets") or []:
        p = pos_of(bot, a)
        exposure += p["position_qty"] * (mkt(a).get("price") or 0)
    lev = exposure / eq if eq > 0 else 0.0
    gates_cfg = load_gates()
    rb = RollbackMonitor(gates_cfg.rollback).evaluate({"drawdown": dd})
    return {"equity": eq, "start_equity": start, "drawdown_pct": round(dd, 3), "gross_exposure": round(exposure, 2), "leverage": round(lev, 3),
            "open_positions": sum(1 for a in bot.get("assets") or [] if pos_of(bot, a)["position_qty"] > 0), "kill_switch": bool(bot.get("kill_switch")),
            "loss_streak": bot.get("loss_streak", 0), "health": health_score(dd, lev), "rollback_triggers": rb, "status": bot.get("status")}
