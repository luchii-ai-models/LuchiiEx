import threading
import math

RULES = {
    "entry": {"rsi_min": 55, "trend_pct_min": 0.08, "volume_ratio_min": 1.5, "adx_min": 25, "score_min": 80},
    "risk": {"atr_stop_mult": 1.5, "bull": 0.01, "bear": 0.0075, "score90": 0.01, "score80": 0.01, "score70": 0.005},
    "tiers": [(1.0, 0.25), (2.0, 0.25)],
    "trail": {"break_even": 0.5, "profit_lock": 1.0, "lock_mult": 1.0025, "atr3_at": 2.0, "atr2_at": 5.0, "runner_atr": 2.5},
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


def vwap(candles: list, n: int = 1440):
    win = candles[-n:]
    pv = sum(((c["h"] + c["l"] + c["c"]) / 3) * c["v"] for c in win)
    v = sum(c["v"] for c in win)
    return pv / v if v else None


def resample(candles: list, step: int) -> list:
    """1m → step-minute closes (last partial bucket dropped)."""
    full = len(candles) // step * step
    return [candles[i + step - 1]["c"] for i in range(0, full, step)]


def mtf_trend(candles: list) -> dict:
    out = {}
    for tf, step in (("1m", 1), ("5m", 5), ("15m", 15)):
        closes = resample(candles, step) if step > 1 else [c["c"] for c in candles]
        e9, e21 = ema(closes, 9), ema(closes, 21)
        out[tf] = None if nan(e9, e21) else e9 > e21
    return out


GUARD = {"adx_min": 25, "vol_min": 1.5, "conf_min": 0.80, "score_min": 80, "rsi_min": 55}


def production_guard(i: dict, bi: dict, asset: str, score: float | None = None) -> list[str]:
    """Pure market-state checks shared by live engine and backtest runner. Returns failed-check messages."""
    if nan(i.get("adx"), i.get("rsi"), i.get("volume_ratio"), i.get("ema9"), i.get("ema21"), i.get("vwap"), i.get("atr"), i.get("atr_ma"), bi.get("ema200"), bi.get("rsi"), bi.get("trend_pct")):
        return ["indicators warming up"]
    score = score if score is not None else entry_score(i)[0]
    conf = confidence(i, bi)
    mtf = i.get("mtf") or {}
    g = GUARD
    checks = [(i.get("regime") != "CHOP", "CHOP regime"), (i["adx"] >= g["adx_min"], f"ADX {i['adx']:.0f} < {g['adx_min']}"),
              (i["ema9"] > i["ema21"], "EMA9 ≤ EMA21"), (i["price"] > i["vwap"], "price below VWAP"),
              (g["rsi_min"] < i["rsi"] < 70, f"RSI {i['rsi']:.0f} outside {g['rsi_min']}-70"),
              (i["volume_ratio"] >= g["vol_min"], f"volume {i['volume_ratio']:.2f}x < {g['vol_min']}"), (i["atr"] > i["atr_ma"], "ATR not expanding"),
              (all(mtf.get(tf) for tf in ("1m", "5m", "15m")), "5m/15m trend not aligned"),
              (score >= g["score_min"], f"score {score} < {g['score_min']}"), (conf >= g["conf_min"], f"confidence {conf * 100:.0f}% < {g['conf_min'] * 100:.0f}%")]
    if asset != "BTC":
        checks += [(bi["price"] > bi["ema200"], "BTC below EMA200"), (bi["rsi"] > 45, f"BTC RSI {bi['rsi']:.0f} ≤ 45"), (bi["trend_pct"] > 0, "BTC trend negative")]
    return [msg for ok, msg in checks if not ok]


def indicators(candles: list, price: float, rsi: float) -> dict:
    closes = [c["c"] for c in candles]
    vols = [c["v"] for c in candles]
    e9, e21, e20, e50, e200 = ema(closes, 9), ema(closes, 21), ema(closes, 20), ema(closes, 50), ema(closes, 200)
    a, a_ma, ax = atr(candles), atr_series(candles), adx(candles)
    vma = sma(vols[:-1], 20)
    out = {"price": price, "rsi": rsi, "sma5": sma(closes, 5), "sma13": sma(closes, 13), "ema9": e9, "ema21": e21, "ema20": e20, "ema50": e50, "ema200": e200,
           "atr": a, "atr_ma": a_ma, "adx": ax, "vwap": vwap(candles), "mtf": mtf_trend(candles),
           "trend_pct": ((e20 - e50) / e50 * 100) if e20 and e50 else None,
           "volume_ratio": (vols[-2] / vma) if vma and len(vols) >= 2 else None}
    out["structure"] = structure(candles, price)
    out["regime"] = regime(out, out["structure"])
    out["bull_trend"] = bool(e20 and e50 and e200 and price > e200 and e20 > e50)
    return out


def confidence(i: dict, btc: dict) -> float:
    """Weighted conviction 0..1: trend .30 · momentum .20 · volume .20 · BTC .15 · ADX .15."""
    if nan(i.get("ema9"), i.get("ema21"), i.get("rsi"), i.get("volume_ratio"), i.get("adx")):
        return 0.0
    return round((0.30 if i["ema9"] > i["ema21"] else 0) + (0.20 if i["rsi"] > 60 else 0) + (0.20 if i["volume_ratio"] > 1.5 else 0)
                 + (0.15 if btc.get("bull_trend") else 0) + (0.15 if i["adx"] > 30 else 0), 2)


def entry_score(i: dict) -> tuple:
    checks = [("EMA9>21", (i.get("ema9") or 0) > (i.get("ema21") or 0)), ("EMA20>50", i["ema20"] > i["ema50"]), (f"RSI>{rules()['entry']['rsi_min']}", i["rsi"] > rules()["entry"]["rsi_min"]),
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


def update_stop(entry, highest, pnl_pct, atr_v, existing, runner: bool = False):
    t = rules()["trail"]
    stop = existing
    if pnl_pct >= t["break_even"]:
        stop = max(stop, entry)
    if pnl_pct >= t["profit_lock"]:
        stop = max(stop, entry * t["lock_mult"])
    if atr_v:
        if runner:
            stop = max(stop, highest - atr_v * t["runner_atr"])
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
        tiers = int(pos.get("tiers_done") or 0)
        n_tiers = len(rules()["tiers"])
        stop = update_stop(entry, highest, pnl, atr_v, existing, runner=tiers >= n_tiers)
        fee = float(pos.get("fee_pct") or 0)
        r_pct = (atr_v * rules()["risk"]["atr_stop_mult"] / entry * 100) if atr_v else 0
        if stop and price <= stop:
            why = "STOP" if stop < entry else "BREAK-EVEN STOP" if stop == entry else f"TRAILING STOP ({rules()['trail']['runner_atr']}×ATR runner)" if tiers >= n_tiers else "TRAILING STOP"
            return {**base, "action": "SELL", "stop": stop, "reason": f"[{asset}] {why} hit ${stop:,.2f} · {pnl:+.2f}% · peak ${highest:,.2f}"}
        if tiers < n_tiers and r_pct and pnl >= rules()["tiers"][tiers][0] * r_pct + fee:
            frac = rules()["tiers"][tiers][1] / (1 - 0.25 * tiers)
            return {**base, "action": "SELL", "fraction": round(frac, 4), "tier": tiers + 1, "stop": stop,
                    "reason": f"[{asset}] +{rules()['tiers'][tiers][0]:.0f}R hit ({pnl:+.2f}% · 1R = {r_pct:.2f}%) · scale out 25% · stop → ${stop:,.2f}"}
        nxt = f"+{rules()['tiers'][tiers][0]:.0f}R next" if tiers < n_tiers else f"runner · {rules()['trail']['runner_atr']}×ATR trail"
        return {**base, "action": "HOLD", "stop": stop, "reason": f"[{asset}] in position {pnl:+.2f}% · stop ${stop:,.2f} · {nxt} · regime {i.get('regime')}"}
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
