"""PayPal Checkout (Orders v2) → ledger. Needs PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET / PAYPAL_ENV (sandbox|live) in backend/.env."""
import base64
import os

import httpx

import ledger

BASE = {"sandbox": "https://api-m.sandbox.paypal.com", "live": "https://api-m.paypal.com"}


def configured() -> bool:
    return bool(os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET"))


def _base() -> str:
    return BASE[os.environ.get("PAYPAL_ENV", "sandbox")]


async def _token(c: httpx.AsyncClient) -> str:
    auth = base64.b64encode(f"{os.environ['PAYPAL_CLIENT_ID']}:{os.environ['PAYPAL_CLIENT_SECRET']}".encode()).decode()
    r = await c.post(f"{_base()}/v1/oauth2/token", data={"grant_type": "client_credentials"}, headers={"Authorization": f"Basic {auth}"})
    r.raise_for_status()
    return r.json()["access_token"]


async def create_order(uid: str, amount: float, return_url: str, cancel_url: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        tok = await _token(c)
        r = await c.post(f"{_base()}/v2/checkout/orders", headers={"Authorization": f"Bearer {tok}", "PayPal-Request-Id": f"{uid}:{amount}:{return_url[-8:]}"},
                         json={"intent": "CAPTURE", "purchase_units": [{"custom_id": uid, "amount": {"currency_code": "USD", "value": f"{amount:.2f}"}}],
                               "application_context": {"brand_name": "Luchii Ex", "user_action": "PAY_NOW", "return_url": return_url, "cancel_url": cancel_url}})
        r.raise_for_status()
        d = r.json()
        approve = next((l["href"] for l in d.get("links", []) if l["rel"] == "approve"), None)
        return {"orderId": d["id"], "approveUrl": approve, "status": d["status"]}


async def capture(uid: str, order_id: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        tok = await _token(c)
        r = await c.post(f"{_base()}/v2/checkout/orders/{order_id}/capture", headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        if r.status_code == 422 and "ORDER_ALREADY_CAPTURED" in r.text:
            r = await c.get(f"{_base()}/v2/checkout/orders/{order_id}", headers={"Authorization": f"Bearer {tok}"})
        r.raise_for_status()
        d = r.json()
    cap = d["purchase_units"][0]["payments"]["captures"][0]
    if cap["status"] != "COMPLETED":
        return {"status": cap["status"], "orderId": order_id}
    amt = cap["amount"]["value"]
    import funding
    out = await funding.settle_fiat_deposit(uid, "PAYPAL", cap["id"], amt)
    return {"status": "COMPLETED", "orderId": order_id, "amount": float(amt), **out}
