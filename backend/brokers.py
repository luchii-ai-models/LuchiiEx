import time

import httpx

PROV = "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai/users/current/accounts"


def client_url(region: str) -> str:
    return f"https://mt-client-api-v1.{region or 'new-york'}.agiliumtrade.ai/users/current/accounts"


def _err(r: httpx.Response) -> str:
    try:
        j = r.json()
        return j.get("message") or str(j)
    except Exception:
        return r.text[:200]


async def provision(token: str, login: str, password: str, server: str, platform: str) -> dict:
    h = {"auth-token": token}
    body = {"login": login, "password": password, "name": f"luchii-{login}", "server": server,
            "platform": platform, "magic": 0, "type": "cloud-g2"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(PROV, json=body, headers=h)
        if r.status_code >= 300:
            raise RuntimeError(f"MetaApi: {_err(r)}")
        acc_id = r.json()["id"]
        info = (await c.get(f"{PROV}/{acc_id}", headers=h)).json()
        await c.post(f"{PROV}/{acc_id}/deploy", headers=h)
    return {"account_id": acc_id, "region": info.get("region", "new-york")}


async def account_info(mt: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{client_url(mt.get('region'))}/{mt['account_id']}/account-information", headers={"auth-token": mt["token"]})
    if r.status_code >= 300:
        raise RuntimeError(f"MetaApi: {_err(r)}")
    return r.json()


async def trade(mt: dict, side: str) -> dict:
    t0 = time.time()
    action = "ORDER_TYPE_BUY" if side == "BUY" else "ORDER_TYPE_SELL"
    async with httpx.AsyncClient(timeout=20) as c:
        for sym in [s.strip() for s in mt["symbols"].split(",") if s.strip()]:
            r = await c.post(f"{client_url(mt.get('region'))}/{mt['account_id']}/trade", headers={"auth-token": mt["token"]},
                             json={"actionType": action, "symbol": sym, "volume": float(mt["volume"])})
            if r.status_code >= 300:
                raise RuntimeError(f"MetaApi {sym}: {_err(r)}")
    return {"ok": True, "last_ms": int((time.time() - t0) * 1000), "ts": int(time.time() * 1000)}
