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
    for code, msg in OKX_ERRORS.items():
        if f'"code":"{code}"' in s.replace(" ", ""):
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


def total_usd(balances: dict, price: float) -> float:
    tot = 0.0
    for b in balances.values():
        tot += sum(float(b.get(c) or 0) for c in ("USD", "USDT", "USDC")) + float(b.get("BTC") or 0) * (price or 0)
    return round(tot, 2)


def nonzero_balances(bal: dict) -> dict:
    return {k: v for k, v in (bal.get("total") or {}).items() if v}


def equity_usd(bal: dict, price: float) -> float:
    t = bal.get("total") or {}
    usd = sum(float(t.get(c) or 0) for c in ("USD", "USDT", "USDC"))
    return round(usd + float(t.get("BTC") or 0) * (price or 0), 2)


async def market_order(ex, symbol: str, side: str, usd: float, qty: float, price: float) -> dict:
    if side == "BUY":
        amount = float(ex.amount_to_precision(symbol, usd / price))
        return await ex.create_order(symbol, "market", "buy", amount, price)
    amount = float(ex.amount_to_precision(symbol, qty))
    return await ex.create_order(symbol, "market", "sell", amount)
