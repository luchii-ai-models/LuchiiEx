# Aggressive oversold dip entry: RSI < 25 + first green candle -> BUY with tight stop; DIP STOP exit; LUCHII unaffected.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
import engine  # noqa: E402


def falling(n=40, start=2500.0, bounce=True):
    cs = [{"t": k, "o": start - k, "h": start - k, "l": start - k, "c": start - k, "v": 1} for k in range(n)]
    if bounce:
        cs[-1]["c"] = cs[-2]["c"] + 1.5  # first green candle
    return cs


def bot(**kw):
    return {**engine.DEFAULT_BOT, "user_id": "u", "strategy": "aggressive", "aggressive": True, "take_profit_pct": 0.6, "stop_loss_pct": 0.4, **kw}


def test_dip_buy_on_first_green_candle():
    cs = falling()
    engine.SNAPSHOT["markets"] = {"ETH": {"price": cs[-1]["c"], "candles": cs, "rsi": 18.8}}
    d = engine.signal(bot(), "ETH")
    assert d["action"] == "BUY" and d.get("dip") and "OVERSOLD DIP" in d["reason"]
    assert abs(d["stop"] - cs[-1]["c"] * (1 - 0.0025)) < 1e-6


def test_no_dip_buy_while_still_falling_or_rsi_high():
    cs = falling(bounce=False)
    engine.SNAPSHOT["markets"] = {"ETH": {"price": cs[-1]["c"], "candles": cs, "rsi": 18.8}}
    assert engine.signal(bot(), "ETH")["action"] == "HOLD"
    cs = falling()
    engine.SNAPSHOT["markets"] = {"ETH": {"price": cs[-1]["c"], "candles": cs, "rsi": 30}}
    assert engine.signal(bot(), "ETH")["action"] == "HOLD"


def test_dip_disabled_or_cooldown():
    import time
    cs = falling()
    engine.SNAPSHOT["markets"] = {"ETH": {"price": cs[-1]["c"], "candles": cs, "rsi": 15}}
    assert engine.signal(bot(dip_entries=False), "ETH")["action"] == "HOLD"
    assert engine.signal(bot(last_stop={"ETH": {"ts": time.time(), "price": 9999}}), "ETH")["action"] == "HOLD"


def test_dip_stop_exit_and_luchii_untouched():
    cs = falling()
    b = bot(alt_positions={"ETH": {"qty": 0.01, "entry": 2462.0, "peak": 2462.0, "tp_taken": False, "stop": 2455.85}})
    engine.SNAPSHOT["markets"] = {"ETH": {"price": 2455.0, "candles": cs, "rsi": 20}}
    d = engine.signal(b, "ETH")
    assert d["action"] == "SELL" and "DIP STOP" in d["reason"]
    engine.SNAPSHOT["markets"] = {"ETH": {"price": 2458.0, "candles": cs, "rsi": 20}}
    assert engine.signal(b, "ETH")["action"] == "HOLD"
    # LUCHII_003 path does not take dip entries
    engine.SNAPSHOT["markets"]["ETH"]["price"] = cs[-1]["c"]
    engine.SNAPSHOT["markets"]["BTC"] = engine.SNAPSHOT["markets"]["ETH"]
    assert engine.decide({**bot(), "strategy": "luchii003"}, "ETH")["action"] == "HOLD"


def test_quiet_market_filter_blocks_unreachable_target():
    flat = [{"t": k, "o": 2400, "h": 2400.3, "l": 2399.7, "c": 2400.0 + (0.2 if k % 2 else 0), "v": 1} for k in range(40)]
    flat[-1]["c"] = 2401.0  # SMA5 > SMA13 cross
    engine.SNAPSHOT["markets"] = {"ETH": {"price": 2401.0, "candles": flat, "rsi": 55}}
    d = engine.signal(bot(), "ETH")
    assert d["action"] == "HOLD" and "too quiet" in d["reason"]
    # volatile market: ATR ~0.3% -> reachable 1.8% > 0.82%
    vol = [{"t": k, "o": 2400, "h": 2410, "l": 2390, "c": 2400.0 + (k % 3) * 3, "v": 1} for k in range(40)]
    vol[-1]["c"] = 2415.0
    engine.SNAPSHOT["markets"] = {"ETH": {"price": 2415.0, "candles": vol, "rsi": 55}}
    assert "too quiet" not in engine.signal(bot(), "ETH")["reason"]
