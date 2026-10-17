# Fee-aware targets: TP triggers only after the round-trip fee is covered; live P&L is net of fees.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
import engine  # noqa: E402
import strategy  # noqa: E402


def test_fee_math():
    bot = {"take_profit_pct": 0.6, "fee_rate": 0.001}
    assert engine.fee_round_trip_pct(bot) == 0.22
    assert engine.net_tp_pct(bot) == 0.82
    assert engine.net_tp_pct({"take_profit_pct": 0}) == 0.0
    assert engine.fee_round_trip_pct({}) == 0.22  # default 0.10% taker
    assert engine.fee_round_trip_pct({"fee_rate": 0.0008}) == 0.18
    # 1 ETH 2400 -> 2419.68 (+0.82%): gross 19.68, fees (2400+2419.68)*0.001 = 4.82 -> net > 0
    assert engine.net_pnl(bot, 2400.0, 2419.68, 1.0) == 14.8603
    # a "win" of +0.1% gross is a net loss once fees are counted
    assert engine.net_pnl(bot, 2400.0, 2402.4, 1.0) < 0


def test_aggressive_tp_waits_for_fee_cover():
    candles = [{"t": k, "o": 2400, "h": 2400, "l": 2400, "c": 2400.0, "v": 1} for k in range(40)]
    bot = {**engine.DEFAULT_BOT, "user_id": "u", "strategy": "aggressive", "take_profit_pct": 0.6, "fee_rate": 0.001, "partial_tp": False,
           "alt_positions": {"ETH": {"qty": 0.01, "entry": 2400.0, "peak": 2400.0, "tp_taken": False}}}
    engine.SNAPSHOT["markets"] = {"ETH": {"price": 2400 * 1.0065, "candles": candles, "rsi": 55}}
    assert engine.signal(bot, "ETH")["action"] == "HOLD"  # +0.65% gross would be a net loss
    engine.SNAPSHOT["markets"]["ETH"]["price"] = 2400 * 1.0083
    d = engine.signal(bot, "ETH")
    assert d["action"] == "SELL" and "fees" in d["reason"] and "+0.82% net" in d["reason"]


def test_tp_price_includes_fee():
    assert abs(engine.tp_price({"take_profit_pct": 0.6, "fee_rate": 0.001}, {"tp_taken": False}, 2400.0) - 2400 * 1.0082) < 1e-6


def test_luchii_tier_includes_fee():
    i = {"price": 2424.0, "rsi": 60, "sma5": 2420, "sma13": 2410, "ema20": 2410, "ema50": 2400, "ema200": 2300, "atr": 5.0, "atr_ma": 5.0, "adx": 30,
         "trend_pct": 0.4, "volume_ratio": 1.5, "structure": None, "regime": "TREND_BULL", "bull_trend": True}
    pos = {"position_qty": 1.0, "entry_price": 2400.0, "peak_price": 2424.0, "tp_taken": False, "stop_price": 2380.0, "tiers_done": 0, "entry_atr": 5.0}
    assert strategy.evaluate("ETH", i, i, pos)["action"] == "SELL"  # +1.0% tier without fee
    assert strategy.evaluate("ETH", i, i, {**pos, "fee_pct": 0.22})["action"] == "HOLD"  # needs +1.22%
    i2 = {**i, "price": 2400 * 1.0125}
    assert strategy.evaluate("ETH", i2, i2, {**pos, "fee_pct": 0.22})["action"] == "SELL"
