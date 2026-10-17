# Exchange-side protective stop: price selection + okx conditional order params (no live calls).
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
import engine  # noqa: E402
import exchange_service as xs  # noqa: E402


def test_protective_stop_hard_sl_then_trailing_floor():
    bot = {"stop_loss_pct": 0.4, "trailing_stop": True}
    pos = {"stop_price": 0.0, "peak_price": 0.0}
    assert abs(engine.protective_stop(bot, "ETH", pos, 2400.0) - 2400 * 0.996) < 1e-6
    pos = {"stop_price": 0.0, "peak_price": 2430.0}
    assert abs(engine.protective_stop(bot, "ETH", pos, 2400.0) - 2430 * 0.996) < 1e-6
    # strategy ATR stop above the hard SL wins
    assert engine.protective_stop(bot, "ETH", {"stop_price": 2395.0, "peak_price": 0.0}, 2400.0) == 2395.0
    # d["stop"] from the decision is honoured on entry
    assert engine.protective_stop(bot, "ETH", {"stop_price": 0.0, "peak_price": 0.0}, 2400.0, {"stop": 2392.0}) == 2392.0
    assert engine.protective_stop({"stop_loss_pct": 0}, "ETH", {"stop_price": 0.0, "peak_price": 0.0}, 2400.0) == 0


def test_place_stop_okx_params_and_cancel_fallbacks():
    calls = []

    class Ex:
        def amount_to_precision(self, s, q):
            return f"{q:.4f}"

        def price_to_precision(self, s, p):
            return f"{p:.2f}"

        async def create_order(self, symbol, typ, side, amount, price, params):
            calls.append((symbol, typ, side, amount, price, params))
            return {"id": "algo123"}

        async def cancel_order(self, oid, symbol, params):
            calls.append(("cancel", oid, params))
            if "stop" not in params:
                raise RuntimeError("wrong endpoint")

    async def run():
        st = await xs.place_stop(Ex(), "okx", "ETH/USDT", 0.003636, 2402.344)
        assert st == {"id": "algo123", "price": 2402.34, "qty": 0.0036}
        assert calls[0][1] == "conditional" and calls[0][2] == "sell" and calls[0][4] is None
        assert calls[0][5] == {"tdMode": "cash", "slTriggerPx": "2402.34", "slOrdPx": "-1"}
        assert await xs.cancel_stop(Ex(), "okx", "ETH/USDT", "algo123") is True
        assert calls[-1] == ("cancel", "algo123", {"stop": True})

    asyncio.run(run())
