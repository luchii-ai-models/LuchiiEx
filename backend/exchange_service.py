import asyncio
import os

import ccxt.async_support as ccxt
from cryptography.fernet import Fernet

EXCHANGES = ["binance", "binanceus", "coinbase", "kraken", "okx", "bybit", "kucoin", "bitget", "gateio",
             "mexc", "bitfinex", "bitstamp", "gemini", "htx", "cryptocom"]
USD_QUOTE = {"coinbase", "kraken", "bitstamp", "gemini", "bitfinex", "binanceus"}


def _f() -> Fernet:
    return Fernet(os.environ["ENCRYPTION_KEY"].encode())


def encrypt(s: str) -> str:
    return _f().encrypt(s.encode()).decode() if s else ""


def decrypt(s: str) -> str:
    return _f().decrypt(s.encode()).decode() if s else ""


def symbol_for(exchange: str) -> str:
    return "BTC/USD" if exchange in USD_QUOTE else "BTC/USDT"


OKX_HOSTS = ["www.okx.com", "us.okx.com", "eea.okx.com", "my.okx.com", "app.okx.com"]
OKX_ERRORS = {
    "50119": "OKX doesn't recognise this API key on any region (global, US, EEA). Copy the key again from the account that created it, make sure it wasn't deleted, and use a live-trading key (not a Demo Trading key).",
    "50101": "This is an OKX Demo Trading key. Create the key under live trading instead.",
    "50105": "OKX passphrase is wrong. Enter the passphrase you set when you created this API key.",
    "50113": "OKX rejected the signature. The API secret is wrong; copy it again.",
    "50110": "Your OKX key only allows certain IPs. Edit the key on OKX and turn off Trusted IP access.",
    "50111": "OKX API key is invalid. Copy it again.",
    "51008": "Not enough balance on OKX for this order.",
}
ACCOUNTS = {"okx": ("funding", "trading"), "binance": ("funding", "spot"), "bybit": ("funding", "unified"),
            "kucoin": ("main", "trade"), "bitget": ("funding", "spot"), "gateio": ("funding", "spot"), "mexc": ("funding", "spot")}


def build_client(exchange: str, api_key: str, secret: str, password: str = "", hostname: str = None):
    cfg = {"apiKey": api_key, "secret": secret, "enableRateLimit": True, "timeout": 15000}
    if password:
        cfg["password"] = password
    if hostname:
        cfg["hostname"] = hostname
    return getattr(ccxt, exchange)(cfg)


def client_from_doc(doc: dict):
    return build_client(doc["exchange"], decrypt(doc["api_key"]), decrypt(doc["secret"]), decrypt(doc.get("password", "")), doc.get("hostname"))


def friendly(e: Exception) -> str:
    s = str(e)
    flat = s.replace(" ", "").lower()
    for code, msg in OKX_ERRORS.items():
        if f'code":"{code}"' in flat:
            return f"{msg} (OKX {code})"
    return f"{type(e).__name__}: {s[:220]}"


async def verify(exchange: str, api_key: str, secret: str, password: str):
    hosts = OKX_HOSTS if exchange == "okx" else [None]
    last = None
    for h in hosts:
        ex = build_client(exchange, api_key, secret, password, h)
        try:
            bal = await ex.fetch_balance()
            perms = await permissions(ex, exchange)
            return bal, h, perms
        except Exception as e:
            last = e
            if '"50119"' not in str(e).replace(" ", "") and '"50111"' not in str(e).replace(" ", ""):
                break
        finally:
            await ex.close()
    raise last


async def permissions(ex, exchange: str):
    try:
        if exchange == "okx":
            perm = (await ex.privateGetAccountConfig())["data"][0].get("perm", "")
            p = set(perm.split(","))
            return {"read": True, "trade": "trade" in p, "withdraw": "withdraw" in p, "transfer": True}
        if exchange in ("binance", "binanceus"):
            r = await ex.sapiGetAccountApiRestrictions()
            return {"read": True, "trade": bool(r.get("enableSpotAndMarginTrading")), "withdraw": bool(r.get("enableWithdrawals")),
                    "transfer": bool(r.get("permitsUniversalTransfer") or r.get("enableInternalTransfer"))}
    except Exception:
        return None
    return None


async def all_balances(ex, exchange: str) -> dict:
    out = {"trading": nonzero_balances(await ex.fetch_balance())}
    acc = ACCOUNTS.get(exchange)
    if acc:
        try:
            out["funding"] = nonzero_balances(await ex.fetch_balance({"type": acc[0]}))
        except Exception:
            pass
    return out


QUOTES = ("USDT", "USD", "USDC")


async def resolve_symbol(ex, exchange: str, base: str = "BTC") -> str:
    await ex.load_markets()
    avail = [q for q in QUOTES if f"{base}/{q}" in ex.markets]
    if not avail:
        if base != "BTC":
            raise ValueError(f"{base} is not tradable on this exchange")
        return symbol_for(exchange)
    try:
        bal = await all_balances(ex, exchange)
        best = max(avail, key=lambda q: sum(float(b.get(q) or 0) for b in bal.values()))
        if any(float(b.get(best) or 0) for b in bal.values()):
            return f"{base}/{best}"
    except Exception:
        pass
    return f"{base}/{avail[0]}"


async def ensure_funds(ex, exchange: str, currency: str, need: float) -> float:
    acc = ACCOUNTS.get(exchange)
    if not acc or need <= 0:
        return 0.0
    free = float(((await ex.fetch_balance()).get(currency) or {}).get("free") or 0)
    if free >= need:
        return 0.0
    funding = float(((await ex.fetch_balance({"type": acc[0]})).get(currency) or {}).get("free") or 0)
    if funding <= 0:
        return 0.0
    amount = float(ex.currency_to_precision(currency, funding)) if hasattr(ex, "currency_to_precision") else funding
    await ex.transfer(currency, amount, acc[0], acc[1])
    return amount


def asset_prices(price: float) -> dict:
    from market import SNAPSHOT
    m = SNAPSHOT.get("markets") or {}
    return {"BTC": price or 0, "ETH": (m.get("ETH") or {}).get("price") or 0, "SOL": (m.get("SOL") or {}).get("price") or 0}


def total_usd(balances: dict, price: float) -> float:
    px = asset_prices(price)
    tot = 0.0
    for b in balances.values():
        tot += sum(float(b.get(c) or 0) for c in ("USD", "USDT", "USDC")) + sum(float(b.get(a) or 0) * p for a, p in px.items())
    return round(tot, 2)


def nonzero_balances(bal: dict) -> dict:
    return {k: v for k, v in (bal.get("total") or {}).items() if v}


def equity_usd(bal: dict, price: float) -> float:
    t = bal.get("total") or {}
    px = asset_prices(price)
    usd = sum(float(t.get(c) or 0) for c in ("USD", "USDT", "USDC"))
    return round(usd + sum(float(t.get(a) or 0) * p for a, p in px.items()), 2)


async def market_order(ex, symbol: str, side: str, usd: float, qty: float, price: float) -> dict:
    if side == "BUY":
        amount = float(ex.amount_to_precision(symbol, usd / price))
        o = await ex.create_order(symbol, "market", "buy", amount, price)
    else:
        amount = float(ex.amount_to_precision(symbol, qty))
        o = await ex.create_order(symbol, "market", "sell", amount)
    if not o.get("average") and o.get("id"):
        try:
            await asyncio.sleep(1)
            o = {**o, **{k: v for k, v in (await ex.fetch_order(o["id"], symbol)).items() if v is not None}}
        except Exception:
            pass
    return o


async def place_stop(ex, exchange: str, symbol: str, qty: float, stop_price: float) -> dict:
    """Exchange-side stop-market sell so the stop fires even if this server is offline. Returns {id, price, qty}."""
    amount = float(ex.amount_to_precision(symbol, qty))
    px = float(ex.price_to_precision(symbol, stop_price))
    if exchange == "okx":
        o = await ex.create_order(symbol, "conditional", "sell", amount, None, {"tdMode": "cash", "slTriggerPx": str(px), "slOrdPx": "-1"})
    else:
        o = await ex.create_order(symbol, "market", "sell", amount, None, {"stopLossPrice": px})
    return {"id": o.get("id"), "price": px, "qty": amount}


async def cancel_stop(ex, exchange: str, symbol: str, stop_id: str) -> bool:
    for params in ({"stop": True}, {"trigger": True}, {}):
        try:
            await ex.cancel_order(stop_id, symbol, params)
            return True
        except Exception as e:  # noqa: BLE001
            last = e
    msg = str(last).lower()
    return "not exist" in msg or "not found" in msg or "51400" in msg or "51401" in msg


async def stop_status(ex, exchange: str, symbol: str, stop_id: str) -> dict:
    """{'state': 'open'|'filled'|'canceled'|'unknown', 'price': avg fill or None, 'qty': filled}."""
    for params in ({"stop": True}, {"trigger": True}):
        try:
            o = await ex.fetch_order(stop_id, symbol, params)
            st = (o.get("status") or "").lower()
            info = o.get("info") or {}
            raw = (info.get("state") or "").lower()
            if st == "closed" or raw in ("effective", "filled"):
                return {"state": "filled", "price": o.get("average") or o.get("price"), "qty": o.get("filled") or o.get("amount")}
            if st == "canceled" or raw in ("canceled", "order_failed"):
                return {"state": "canceled", "price": None, "qty": 0}
            return {"state": "open", "price": None, "qty": 0}
        except Exception:  # noqa: BLE001
            continue
    return {"state": "unknown", "price": None, "qty": 0}


async def place_oco(ex, exchange: str, symbol: str, qty: float, sl_px: float, tp_px: float) -> dict:
    """One-cancels-other: limit take-profit at tp_px + stop-market at sl_px, both exchange-side."""
    amount = float(ex.amount_to_precision(symbol, qty))
    sl, tp = float(ex.price_to_precision(symbol, sl_px)), float(ex.price_to_precision(symbol, tp_px))
    if exchange != "okx":
        raise RuntimeError(f"exchange-side OCO not supported on {exchange}")
    o = await ex.create_order(symbol, "oco", "sell", amount, None,
                              {"tdMode": "cash", "tpTriggerPx": str(tp), "tpOrdPx": str(tp), "slTriggerPx": str(sl), "slOrdPx": "-1"})
    return {"id": o.get("id"), "price": sl, "tp": tp, "qty": amount, "kind": "oco"}
