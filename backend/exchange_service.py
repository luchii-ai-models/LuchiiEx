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


def build_client(exchange: str, api_key: str, secret: str, password: str = ""):
    cfg = {"apiKey": api_key, "secret": secret, "enableRateLimit": True, "timeout": 15000}
    if password:
        cfg["password"] = password
    return getattr(ccxt, exchange)(cfg)


def client_from_doc(doc: dict):
    return build_client(doc["exchange"], decrypt(doc["api_key"]), decrypt(doc["secret"]), decrypt(doc.get("password", "")))


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
