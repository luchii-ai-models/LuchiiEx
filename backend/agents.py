"""Multi-agent voting: five agents score the market 0-100; the weighted winner is the only one allowed to trade."""
import strategy

AGENTS = ("momentum", "trend", "breakout", "volume", "structure")
MIN_FINAL = 75
# Regime permissions: which winning agent may open a long in each regime.
REGIME_ALLOWED = {"TREND_BULL": {"momentum", "trend", "breakout"}, "BREAKOUT": {"breakout"}, "PANIC": {"breakout"},
                  "TREND_BEAR": set(), "CHOP": set(), "MEAN_REVERSION": set()}


def _clip(x):
    return int(max(0, min(100, round(x))))


def momentum(i):
    rsi, tp = i["rsi"], i["trend_pct"]
    return _clip(50 + (rsi - 50) * 1.6 + tp * 100) if rsi < 70 else _clip(40 - (rsi - 70) * 2)


def trend(i):
    e20, e50, e200, p = i["ema20"], i["ema50"], i["ema200"], i["price"]
    s = 20 * (e20 > e50) + 20 * (e50 > e200) + 20 * (p > e20) + min(40, i["adx"])
    return _clip(s)


def breakout(i):
    st = i.get("structure")
    base = 60 if st == "HIGH" else 0 if st == "LOW" else 25
    exp = i["atr"] / i["atr_ma"] if i["atr_ma"] else 1
    return _clip(base + min(25, (exp - 1) * 50) + min(15, (i["volume_ratio"] - 1) * 15))


def volume(i):
    return _clip(min(100, i["volume_ratio"] * 45))


def structure(i):
    p, e200 = i["price"], i["ema200"]
    dist = (p - e200) / e200 * 100 if e200 else 0
    s = 50 + dist * 10 + (20 if i["ema20"] > i["ema50"] else -20) + (15 if i.get("structure") == "HIGH" else -30 if i.get("structure") == "LOW" else 0)
    return _clip(s)


def vote(i: dict, weights: dict | None = None) -> dict:
    """Returns {scores, final, winner, allowed, reason}. weights default to 1.0 per agent."""
    if strategy.nan(i.get("rsi"), i.get("trend_pct"), i.get("ema20"), i.get("ema50"), i.get("ema200"), i.get("adx"), i.get("atr"), i.get("atr_ma"), i.get("volume_ratio")):
        return {"scores": {}, "final": 0, "winner": None, "allowed": False, "reason": "agents warming up"}
    w = {a: float((weights or {}).get(a, 1.0)) for a in AGENTS}
    scores = {"momentum": momentum(i), "trend": trend(i), "breakout": breakout(i), "volume": volume(i), "structure": structure(i)}
    final = round(sum(scores[a] * w[a] for a in AGENTS) / sum(w.values()))
    winner = max(AGENTS, key=lambda a: scores[a] * w[a])
    reg = i.get("regime", "CHOP")
    allowed = final >= MIN_FINAL and winner in REGIME_ALLOWED.get(reg, set())
    why = f"winner {winner} {scores[winner]} · final {final}" + ("" if final >= MIN_FINAL else f" < {MIN_FINAL}") + ("" if winner in REGIME_ALLOWED.get(reg, set()) else f" · {winner} not allowed in {reg}")
    return {"scores": scores, "final": final, "winner": winner, "allowed": allowed, "reason": why}


def weights_from_audit(rows: list, min_trades: int = 10) -> dict:
    """Reinforcement ranking: weight = clamp(PF * expectancy-sign) once an agent has enough closed trades."""
    out = {}
    for a in AGENTS:
        pnls = [r["net_pnl"] for r in rows if r.get("winner_agent") == a and r.get("net_pnl") is not None]
        if len(pnls) < min_trades:
            out[a] = 1.0
            continue
        g, l = sum(p for p in pnls if p > 0), abs(sum(p for p in pnls if p < 0))
        pf = 3.0 if l == 0 else g / l
        out[a] = round(max(0.25, min(2.0, pf)), 2)
    return out
