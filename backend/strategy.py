import threading
import math

RULES = {
    "entry": {"rsi_min": 55, "trend_pct_min": 0.08, "volume_ratio_min": 1.5, "adx_min": 25, "score_min": 80},
    "risk": {"atr_stop_mult": 2.5, "bull": 0.015, "bear": 0.0075, "score90": 0.015, "score80": 0.01, "score70": 0.005},
    "tiers": [(1.0, 0.25), (2.5, 0.25), (5.0, 0.25)],
    "trail": {"break_even": 0.5, "profit_lock": 1.0, "lock_mult": 1.0025, "atr3_at": 2.0, "atr2_at": 5.0},
}

_LOCAL = threading.local()


def rules() -> dict:
    """Active rule set: a thread-local override (backtests) or the live RULES."""
    return getattr(_LOCAL, "override", None) or RULES


def set_rules_override(override: dict | None):
    _LOCAL.override = override



def nan(*vals) -> bool:
    for v in vals:
        if v is None:
            return True
        try:
            if math.isnan(v):
                return True
        except TypeError:
            pass
    return False


def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def ema(xs, n):
    if len(xs) < n:
        return None
    k = 2 / (n + 1)
    e = sum(xs[:n]) / n
    for x in xs[n:]:
        e = x * k + e * (1 - k)
    return e


def atr(candles, n=14):
    if len(candles) < n + 1:
        return None
    trs = []
    for p, c in zip(candles[-n - 1:-1], candles[-n:]):
        trs.append(max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])))
    return sum(trs) / n


def atr_series(candles, n=14, m=20):
    if len(candles) < n + m:
        return None
    vals = [atr(candles[: len(candles) - i], n) for i in range(m)]
    return sum(vals) / m


def adx(candles, n=14):
    if len(candles) < 2 * n + 1:
        return None
    pdm, mdm, trs = [], [], []
    for p, c in zip(candles[:-1], candles[1:]):
        up, dn = c["h"] - p["h"], p["l"] - c["l"]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        trs.append(max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])))
    dxs = []
    for i in range(n, len(trs) + 1):
        tr = sum(trs[i - n:i])
        if tr == 0:
            dxs.append(0.0)
            continue
        pdi, mdi = 100 * sum(pdm[i - n:i]) / tr, 100 * sum(mdm[i - n:i]) / tr
        den = pdi + mdi
        dxs.append(0.0 if den == 0 else abs(pdi - mdi) / den * 100)
    return sum(dxs[-n:]) / n if len(dxs) >= n else None


REGIMES = ("TREND_BULL", "TREND_BEAR", "BREAKOUT", "CHOP", "MEAN_REVERSION", "PANIC")
NO_TRADE_REGIMES = ("CHOP", "MEAN_REVERSION")


def classify_regime(ema50, ema200, adx_v, close=None):
    """Legacy coarse classifier (EMA50/200 + ADX only)."""
    if nan(ema50, ema200, adx_v):
        return "CHOP"
    if adx_v < 20 or (close and abs(ema50 - ema200) / close < 0.0008):
        return "CHOP"
    if ema50 > ema200 and adx_v > 25:
        return "TREND_BULL"
    if ema50 < ema200 and adx_v > 25:
        return "TREND_BEAR"
    return "CHOP"


def structure(candles: list, price: float, lookback: int = 1440):
    """'HIGH' if price breaks the lookback high, 'LOW' if it breaks the low, else None."""
    win = candles[-lookback - 1:-1]
    if len(win) < 30 or not price:
        return None
    if price >= max(c["h"] for c in win):
        return "HIGH"
    if price <= min(c["l"] for c in win):
        return "LOW"
    return None


def regime(i: dict, struct=None) -> str:
    """Market state machine. Order: PANIC > BREAKOUT > CHOP > TREND > MEAN_REVERSION > CHOP."""
    adx_v, rsi, e20, e50, e200, a, a_ma, vr = (i.get(k) for k in ("adx", "rsi", "ema20", "ema50", "ema200", "atr", "atr_ma", "volume_ratio"))
    if nan(adx_v, rsi, e20, e50, e200):
        return "CHOP"
    expanding = not nan(a, a_ma) and a > a_ma
    if expanding and a > a_ma * 3:
        return "PANIC"
    if expanding and not nan(vr) and vr > 1.5 and struct:
        return "BREAKOUT"
    if adx_v < 20:
        return "CHOP"
    if adx_v >= 25 and e20 > e50 > e200 and rsi > 55:
        return "TREND_BULL"
    if adx_v >= 25 and e20 < e50 < e200 and rsi < 45:
        return "TREND_BEAR"
    if adx_v < 25 and (rsi > 75 or rsi < 25):
        return "MEAN_REVERSION"
    return "CHOP"


def indicators(candles: list, price: float, rsi: float) -> dict:
    closes = [c["c"] for c in candles]
    vols = [c["v"] for c in candles]
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
    a, a_ma, ax = atr(candles), atr_series(candles), adx(candles)
    vma = sma(vols[:-1], 20)
    out = {"price": price, "rsi": rsi, "sma5": sma(closes, 5), "sma13": sma(closes, 13), "ema20": e20, "ema50": e50, "ema200": e200,
           "atr": a, "atr_ma": a_ma, "adx": ax,
           "trend_pct": ((e20 - e50) / e50 * 100) if e20 and e50 else None,
           "volume_ratio": (vols[-2] / vma) if vma and len(vols) >= 2 else None}
    out["structure"] = structure(candles, price)
    out["regime"] = regime(out, out["structure"])
    out["bull_trend"] = bool(e20 and e50 and e200 and price > e200 and e20 > e50)
    return out


def entry_score(i: dict) -> tuple:
    checks = [("SMA5>13", i["sma5"] > i["sma13"]), ("EMA20>50", i["ema20"] > i["ema50"]), (f"RSI>{rules()['entry']['rsi_min']}", i["rsi"] > rules()["entry"]["rsi_min"]),
              (f"VOL>{rules()['entry']['volume_ratio_min']}x", i["volume_ratio"] > rules()["entry"]["volume_ratio_min"]),
              (f"TREND>{rules()['entry']['trend_pct_min']}%", i["trend_pct"] > rules()["entry"]["trend_pct_min"])]
    score = sum(20 for _, ok in checks if ok)
    return score, [name for name, ok in checks if not ok]


def risk_pct(score: int, regime: str) -> float:
    r = rules()["risk"]
    base = r["score90"] if score >= 90 else r["score80"] if score >= 80 else r["score70"] if score >= 70 else 0.0
    if regime == "TREND_BEAR":
        return min(base, r["bear"])
    return base * 0.5 if regime == "PANIC" else base


def position_size(cash, risk, entry, stop):
    dist = abs(entry - stop)
    if entry <= 0 or dist <= 0 or cash <= 0:
        return 0.0
    return min(cash * risk / dist, cash / entry)


def update_stop(entry, highest, pnl_pct, atr_v, existing):
    t = rules()["trail"]
    stop = existing
    if pnl_pct >= t["break_even"]:
        stop = max(stop, entry)
    if pnl_pct >= t["profit_lock"]:
        stop = max(stop, entry * t["lock_mult"])
    if atr_v:
        if pnl_pct >= t["atr3_at"]:
            stop = max(stop, highest - atr_v * 3)
        if pnl_pct >= t["atr2_at"]:
            stop = max(stop, highest - atr_v * 2)
    return stop


def evaluate(asset: str, i: dict, btc: dict, pos: dict, weights: dict | None = None) -> dict:
    """Returns {action, fraction?, stop?, risk?, score, regime, reason, metrics}."""
    base = {"score": 0, "regime": i.get("regime", "CHOP"), "metrics": i}
    price, qty = i["price"], pos["position_qty"]
    warm = nan(i["sma5"], i["sma13"], i["ema20"], i["ema50"], i["ema200"], i["rsi"], i["atr"], i["atr_ma"], i["adx"], i["trend_pct"], i["volume_ratio"])
    if not warm:
        base["score"], missing = entry_score(i)
    if qty > 0 and pos["entry_price"] and price:
        entry, highest = pos["entry_price"], max(pos["peak_price"] or 0, pos["entry_price"], price)
        pnl = (price - entry) / entry * 100
        atr_v = pos.get("entry_atr") or (None if nan(i.get("atr")) else i["atr"])
        existing = pos.get("stop_price") or (entry - atr_v * rules()["risk"]["atr_stop_mult"] if atr_v else 0)
        stop = update_stop(entry, highest, pnl, atr_v, existing)
        tiers = int(pos.get("tiers_done") or 0)
        fee = float(pos.get("fee_pct") or 0)
        if stop and price <= stop:
            why = "STOP" if stop < entry else "BREAK-EVEN STOP" if stop == entry else "TRAILING STOP"
            return {**base, "action": "SELL", "stop": stop, "reason": f"[{asset}] {why} hit ${stop:,.2f} · {pnl:+.2f}% · peak ${highest:,.2f}"}
        if tiers < len(rules()["tiers"]) and pnl >= rules()["tiers"][tiers][0] + fee:
            frac = rules()["tiers"][tiers][1] / (1 - 0.25 * tiers)
            return {**base, "action": "SELL", "fraction": round(frac, 4), "tier": tiers + 1, "stop": stop,
                    "reason": f"[{asset}] TP{tiers + 1} +{rules()['tiers'][tiers][0]}%{f' +{fee:.2f}% fees' if fee else ''} hit ({pnl:+.2f}%) · selling 25% of original · stop → ${stop:,.2f}"}
        return {**base, "action": "HOLD", "stop": stop, "reason": f"[{asset}] in position {pnl:+.2f}% · stop ${stop:,.2f} · TP{tiers + 1} next · regime {i.get('regime')}"}
    if warm:
        return {**base, "action": "HOLD", "reason": f"[{asset}] warming up indicators (need 200 candles)"}
    score = base["score"]
    reg = i["regime"]
    if reg in NO_TRADE_REGIMES:
        return {**base, "action": "HOLD", "reason": f"[{asset}] {reg} regime (ADX {i['adx']:.0f} · RSI {i['rsi']:.0f}) · no new trades · score {score}/100"}
    if reg == "BREAKOUT" and i.get("structure") == "LOW":
        return {**base, "action": "HOLD", "reason": f"[{asset}] BREAKDOWN (new low · vol {i['volume_ratio']:.1f}x) · no longs · score {score}/100"}
    if asset != "BTC" and not btc.get("bull_trend"):
        return {**base, "action": "HOLD", "reason": f"[{asset}] BTC master filter · BTC below EMA200/EMA20<50 · no alt longs · score {score}/100"}
    if asset != "BTC" and (reg == "TREND_BEAR" or btc.get("regime") == "TREND_BEAR"):
        return {**base, "action": "HOLD", "reason": f"[{asset}] TREND_BEAR regime ({'BTC' if btc.get('regime') == 'TREND_BEAR' else asset}) · no alt longs · score {score}/100"}
    gates = []
    if i["adx"] <= rules()["entry"]["adx_min"]:
        gates.append(f"ADX {i['adx']:.0f}≤25")
    if i["atr"] <= i["atr_ma"]:
        gates.append("ATR not expanding")
    if score < rules()["entry"]["score_min"] or gates:
        return {**base, "action": "HOLD", "reason": f"[{asset}] score {score}/100 · {reg} · missing {', '.join(missing + gates) or '-'}"}
    import agents
    v = agents.vote(i, weights)
    base["agents"] = v
    if not v["allowed"]:
        return {**base, "action": "HOLD", "reason": f"[{asset}] agent vote · {v['reason']} · score {score}/100"}
    risk = risk_pct(score, reg)
    stop = price - i["atr"] * rules()["risk"]["atr_stop_mult"]
    return {**base, "action": "BUY", "risk": risk, "stop": stop, "entry_atr": i["atr"], "winner_agent": v["winner"],
            "reason": f"[{asset}] BUY · score {score}/100 · {reg} · trend {i['trend_pct']:+.2f}% · vol {i['volume_ratio']:.1f}x · ADX {i['adx']:.0f} · risk {risk * 100:.2g}% · stop ${stop:,.2f}"}
