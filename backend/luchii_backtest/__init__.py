"""Luchii backtest: Coinbase history → LUCHII_003 simulation, walk-forward and Monte Carlo."""
import math
import sys
import time
from itertools import product
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import strategy  # noqa: E402

CB = "https://api.exchange.coinbase.com/products"


def fetch_candles(asset: str, bars: int = 3000, granularity: int = 60) -> list:
    out, end = [], int(time.time())
    with httpx.Client(timeout=15, headers={"User-Agent": "luchii-backtest"}) as c:
        while len(out) < bars:
            start = end - 300 * granularity
            r = c.get(f"{CB}/{asset}-USD/candles", params={"granularity": granularity, "start": start, "end": end})
            rows = sorted(r.json(), key=lambda x: x[0]) if isinstance(r.json(), list) else []
            if not rows:
                break
            out = [{"t": x[0] * 1000, "o": x[3], "h": x[2], "l": x[1], "c": x[4], "v": x[5]} for x in rows] + out
            end = start
            time.sleep(0.25)
    return out[-bars:]


def profit_factor(trades):
    if not trades:
        return 0.0
    wins = sum(t for t in trades if t > 0)
    losses = abs(sum(t for t in trades if t < 0))
    return float("inf") if losses == 0 and wins > 0 else (0.0 if losses == 0 else wins / losses)


def sharpe(returns):
    if len(returns) < 2:
        return 0.0
    m = sum(returns) / len(returns)
    sd = math.sqrt(sum((r - m) ** 2 for r in returns) / (len(returns) - 1))
    return 0.0 if sd == 0 else m / sd * math.sqrt(len(returns))


def max_drawdown(equity):
    peak, dd = -math.inf, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            dd = max(dd, (peak - e) / peak * 100)
    return dd


def execute_long_stop(stop_price, open_price, low_price):
    if open_price <= stop_price:
        return open_price, "gap_stop"
    if low_price <= stop_price:
        return stop_price, "stop"
    return None, None


def execute_long_tp(tp_price, open_price, high_price):
    if open_price >= tp_price:
        return open_price, "gap_tp"
    if high_price >= tp_price:
        return tp_price, "tp"
    return None, None


def rsi(closes, n=14):
    if len(closes) <= n:
        return None
    g = l = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        g += max(b - a, 0)
        l += max(a - b, 0)
    return 100.0 if l == 0 else round(100 - 100 / (1 + g / l), 1)


_IND_CACHE: dict = {}
LOOKBACK = 400


def indicator_series(candles: list) -> list:
    """indicators() for every bar, computed once per candle series (params do not affect indicators)."""
    key = (id(candles), len(candles), candles[0]["t"] if candles else 0)
    if key not in _IND_CACHE:
        if len(_IND_CACHE) > 8:
            _IND_CACHE.clear()
        out = [None] * len(candles)
        for k in range(210, len(candles)):
            window = candles[max(0, k - LOOKBACK):k + 1]
            out[k] = strategy.indicators(window, window[-1]["c"], rsi([c["c"] for c in window]))
        _IND_CACHE[key] = out
    return _IND_CACHE[key]


def run_backtest(candles: list, params: dict = None, cash: float = 10000.0, fee: float = 0.001, slippage: float = 0.0005,
                 btc_candles: list = None) -> dict:
    p = {"score_min": 80, "adx_min": 25, "rsi_min": 55, "trend_min": 0.08, "volume_min": 1.5, "atr_stop": 2.5, **(params or {})}
    override = {**strategy.RULES, "entry": {**strategy.RULES["entry"], "score_min": p["score_min"], "adx_min": p["adx_min"], "rsi_min": p["rsi_min"],
                                           "trend_pct_min": p["trend_min"], "volume_ratio_min": p["volume_min"]},
                "risk": {**strategy.RULES["risk"], "atr_stop_mult": p["atr_stop"]}}
    strategy.set_rules_override(override)
    equity, trades, regimes = [cash], [], []
    pos = {"position_qty": 0.0, "entry_price": 0.0, "peak_price": 0.0, "stop_price": 0.0, "tiers_done": 0, "entry_atr": 0.0}
    cost_basis = 0.0
    ind = indicator_series(candles)
    bind = indicator_series(btc_candles) if btc_candles else None
    try:
        for k in range(210, len(candles) - 1):
            i = ind[k]
            bi = (bind[k] if bind and k < len(bind) and bind[k] else i) if btc_candles else i
            d = strategy.evaluate("X", i, bi, pos)
            nxt = candles[k + 1]
            if pos["position_qty"] > 0:
                pos["peak_price"] = max(pos["peak_price"], nxt["h"])
                fill, why = execute_long_stop(pos["stop_price"], nxt["o"], nxt["l"]) if pos["stop_price"] else (None, None)
                if fill is None and d["action"] == "SELL":
                    fill, why = nxt["o"], ("tp" if d.get("fraction") else "exit")
                if fill is not None:
                    frac = (d.get("fraction") or 1.0) if why == "tp" else 1.0
                    qty = pos["position_qty"] * frac
                    fill *= (1 - slippage)
                    entry_fee = pos["entry_price"] * qty * fee
                    pnl = (fill - pos["entry_price"]) * qty - fill * qty * fee - entry_fee
                    cash += fill * qty - fill * qty * fee
                    trades.append(pnl)
                    regimes.append(i["regime"])
                    pos["position_qty"] -= qty
                    pos["tiers_done"] = d.get("tier", pos["tiers_done"]) if why == "tp" else pos["tiers_done"]
                    if d.get("stop"):
                        pos["stop_price"] = d["stop"]
                    if pos["position_qty"] <= 1e-12:
                        pos.update(position_qty=0.0, entry_price=0.0, peak_price=0.0, stop_price=0.0, tiers_done=0, entry_atr=0.0)
                elif d.get("stop"):
                    pos["stop_price"] = d["stop"]
            elif d["action"] == "BUY":
                entry = nxt["o"] * (1 + slippage)
                qty = strategy.position_size(cash, d["risk"], entry, d["stop"])
                if qty > 0:
                    cash -= entry * qty + entry * qty * fee
                    cost_basis = entry * qty
                    pos.update(position_qty=qty, entry_price=entry, peak_price=entry, stop_price=d["stop"], tiers_done=0, entry_atr=d["entry_atr"])
            equity.append(cash + pos["position_qty"] * nxt["c"])
    finally:
        strategy.set_rules_override(None)
    wins = [t for t in trades if t > 0]
    by_regime = {r: profit_factor([t for t, rr in zip(trades, regimes) if rr == r]) for r in strategy.REGIMES}
    return {"trades": len(trades), "net_profit": round(sum(trades), 4), "profit_factor": profit_factor(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0, "sharpe": round(sharpe(trades), 3),
            "max_drawdown": round(max_drawdown(equity), 3), "equity": equity, "trade_pnls": trades, "params": p, "regime_pf": by_regime, "_cost": cost_basis}


def optimization_score(stats: dict) -> float:
    pf = stats["profit_factor"] if stats["profit_factor"] != float("inf") else 10.0
    return pf * max(stats["sharpe"], 0) * math.log(stats["trades"] + 1) / (abs(stats["max_drawdown"]) + 1)


def parameter_sets(grid: dict):
    keys = list(grid)
    for combo in product(*grid.values()):
        yield dict(zip(keys, combo))


PARAM_GRID = {"rsi_min": [52, 55, 58], "atr_stop": [2.0, 2.5, 3.0], "trend_min": [0.05, 0.08, 0.12], "volume_min": [1.2, 1.4, 1.6]}


def walk_forward(candles: list, train_bars=2000, test_bars=500, step=500, grid=None, btc_candles=None) -> list:
    grid = grid or PARAM_GRID
    results, start = [], 0
    while start + train_bars + test_bars <= len(candles):
        train, test = candles[start:start + train_bars], candles[start + train_bars - 210:start + train_bars + test_bars]
        btc_tr = btc_candles[start:start + train_bars] if btc_candles else None
        btc_te = btc_candles[start + train_bars - 210:start + train_bars + test_bars] if btc_candles else None
        best, best_s = None, -math.inf
        for params in parameter_sets(grid):
            s = run_backtest(train, params, btc_candles=btc_tr)
            sc = optimization_score(s)
            if sc > best_s:
                best, best_s, train_pf = params, sc, s["profit_factor"]
        oos = run_backtest(test, best, btc_candles=btc_te)
        oos.pop("equity", None)
        oos.pop("trade_pnls", None)
        oos.update(train_pf=train_pf, window=len(results),
                   stability=(oos["profit_factor"] / train_pf) if train_pf and train_pf != float("inf") and oos["profit_factor"] != float("inf") else None)
        oos["overfit"] = (train_pf != float("inf") and oos["profit_factor"] != float("inf") and train_pf - oos["profit_factor"] > 2) or (train_pf > 3 and oos["profit_factor"] < 1)
        results.append(oos)
        start += step
    return results


def run_monte_carlo(trade_pnls, simulations=1000, starting_equity=10000.0, seed=42):
    import random
    rng = random.Random(seed)
    curves = []
    for _ in range(simulations):
        shuffled = list(trade_pnls)
        rng.shuffle(shuffled)
        eq, curve = starting_equity, [starting_equity]
        for pnl in shuffled:
            eq += pnl
            curve.append(eq)
        curves.append(curve)
    return curves


def monte_carlo_summary(curves):
    finals = sorted(c[-1] for c in curves)
    n = len(finals)
    pct = lambda q: finals[min(n - 1, int(q * (n - 1)))]  # noqa: E731
    return {"best": finals[-1], "worst": finals[0], "median": pct(0.5), "p05": pct(0.05), "p95": pct(0.95),
            "worst_drawdown": round(max(max_drawdown(c) for c in curves), 3)}


def deployment_gate(oos: list) -> dict:
    """Spec gates: OOS PF>=1.5, Sharpe>=1.25, trades>=50, DD<=12, stability 0.7–1.3, regime PFs."""
    if not oos:
        return {"pass": False, "reason": "no walk-forward windows"}
    pf = sum(min(r["profit_factor"], 10) for r in oos) / len(oos)
    sh = sum(r["sharpe"] for r in oos) / len(oos)
    tr = sum(r["trades"] for r in oos)
    dd = max(r["max_drawdown"] for r in oos)
    st = [r["stability"] for r in oos if r["stability"] is not None]
    stab = sum(st) / len(st) if st else None
    checks = {"oos_pf>=1.5": pf >= 1.5, "sharpe>=1.25": sh >= 1.25, "trades>=50": tr >= 50, "dd<=12": dd <= 12,
              "stability 0.7-1.3": stab is not None and 0.7 <= stab <= 1.3}
    return {"pass": all(checks.values()), "checks": checks, "oos_pf": round(pf, 3), "sharpe": round(sh, 3), "trades": tr, "max_drawdown": round(dd, 3), "stability": stab}


DAY = 86400_000


def day_windows(candles: list, train_days=20, validate_days=5, test_days=5, step_days=1):
    """Sliding train/validate/test windows in whole days (candle 't' is ms). Yields (train, validate, test, meta)."""
    if not candles:
        return
    t0 = candles[0]["t"]
    total = train_days + validate_days + test_days
    days = int((candles[-1]["t"] - t0) / DAY) + 1
    for s in range(0, days - total + 1, step_days):
        a, b, c, d = (t0 + k * DAY for k in (s, s + train_days, s + train_days + validate_days, s + total))
        train = [x for x in candles if a <= x["t"] < b]
        val = [x for x in candles if b - 210 * 60_000 <= x["t"] < c]
        test = [x for x in candles if c - 210 * 60_000 <= x["t"] < d]
        yield train, val, test, {"train": (a, b), "validate": (b, c), "test": (c, d)}


def _iso(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def walk_forward_days(candles: list, btc_candles=None, train_days=20, validate_days=5, test_days=5, step_days=1, grid=None) -> dict:
    """Train picks params (PF>=1.10 required), validate must hold PF>=1.0, test is recorded. Returns per-window results + approval."""
    grid = grid or PARAM_GRID
    windows, skipped = [], 0
    for train, val, test, meta in day_windows(candles, train_days, validate_days, test_days, step_days):
        if len(train) < 400 or len(test) < 300:
            continue
        sel = lambda part: [x for x in (btc_candles or []) if part[0]["t"] <= x["t"] <= part[-1]["t"]] or None  # noqa: E731
        b_train, b_val, b_test = sel(train), sel(val), sel(test)
        best, best_s, train_pf = None, -math.inf, 0.0
        for params in parameter_sets(grid):
            s = run_backtest(train, params, btc_candles=b_train)
            if s["trades"] and optimization_score(s) > best_s:
                best, best_s, train_pf = params, optimization_score(s), s["profit_factor"]
        if best is None or train_pf < 1.10:
            skipped += 1
            continue
        v = run_backtest(val, best, btc_candles=b_val)
        if v["trades"] and v["profit_factor"] < 1.0:
            skipped += 1
            continue
        t = run_backtest(test, best, btc_candles=b_test)
        pnls = t["trade_pnls"]
        windows.append({"train": [_iso(meta["train"][0]), _iso(meta["train"][1])], "validate": [_iso(meta["validate"][0]), _iso(meta["validate"][1])],
                        "test": [_iso(meta["test"][0]), _iso(meta["test"][1])], "params": best, "train_pf": None if train_pf == float("inf") else round(train_pf, 3),
                        "trades": t["trades"], "win_rate": t["win_rate"], "profit_factor": None if t["profit_factor"] == float("inf") else round(t["profit_factor"], 3),
                        "expectancy": round(sum(pnls) / len(pnls), 4) if pnls else 0.0, "max_drawdown": t["max_drawdown"], "net_profit": t["net_profit"], "sharpe": t["sharpe"]})
    return {"windows": windows, "skipped": skipped, **approve_walk_forward(windows)}


def approve_walk_forward(windows: list) -> dict:
    """Reject if avg PF < 1.10, max DD > 15%, avg win rate < 35% or expectancy <= 0."""
    traded = [w for w in windows if w["trades"]]
    if not traded:
        return {"deployment": "REJECTED", "reason": "no windows with trades", "avg_pf": 0.0, "avg_win_rate": 0.0, "avg_expectancy": 0.0, "max_drawdown": 0.0}
    pfs = [min(w["profit_factor"], 10) if w["profit_factor"] is not None else 10 for w in traded]
    avg_pf = sum(pfs) / len(pfs)
    med_pf = sorted(pfs)[len(pfs) // 2]
    wr = sum(w["win_rate"] for w in traded) / len(traded)
    ex = sum(w["expectancy"] for w in traded) / len(traded)
    dd = max(w["max_drawdown"] for w in traded)
    checks = {"avg_pf>=1.10": avg_pf >= 1.10, "max_dd<=15": dd <= 15, "win_rate>=35": wr >= 35, "expectancy>0": ex > 0, "windows>=5": len(traded) >= 5}
    return {"deployment": "APPROVED" if all(checks.values()) else "REJECTED", "checks": checks, "avg_pf": round(avg_pf, 3), "median_pf": round(med_pf, 3),
            "avg_win_rate": round(wr, 1), "avg_expectancy": round(ex, 4), "max_drawdown": round(dd, 3), "windows_traded": len(traded)}


def monte_carlo_ruin(trade_pnls: list, simulations=5000, starting_equity=1000.0, ruin_pct=0.5, seed=42) -> dict:
    """Bootstrap (with replacement) real trade returns: probability of ruin, 95% drawdown, worst loss streak, return distribution."""
    import random
    if len(trade_pnls) < 2:
        return {"simulations": 0, "trades": len(trade_pnls), "probability_of_ruin": None, "deployment": "INSUFFICIENT_DATA", "reason": "need at least 2 closed trades"}
    rng = random.Random(seed)
    n, ruin_level = len(trade_pnls), starting_equity * ruin_pct
    finals, dds, streaks, ruins = [], [], [], 0
    for _ in range(simulations):
        eq, peak, dd, streak, worst = starting_equity, starting_equity, 0.0, 0, 0
        for _k in range(n):
            p = trade_pnls[rng.randrange(n)]
            eq += p
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak * 100 if peak > 0 else 100.0)
            streak = streak + 1 if p < 0 else 0
            worst = max(worst, streak)
        finals.append(eq)
        dds.append(dd)
        streaks.append(worst)
        ruins += eq < ruin_level
    finals.sort(); dds.sort(); streaks.sort()
    q = lambda xs, p: xs[min(len(xs) - 1, int(p * (len(xs) - 1)))]  # noqa: E731
    por = ruins / simulations * 100
    dd95 = q(dds, 0.95)
    exp = sum(trade_pnls) / n
    checks = {"probability_of_ruin<5": por < 5, "drawdown_95<20": dd95 < 20, "expectancy>0": exp > 0}
    return {"simulations": simulations, "trades": n, "starting_equity": starting_equity, "probability_of_ruin": round(por, 2), "ruin_level": ruin_level,
            "drawdown_95": round(dd95, 2), "drawdown_median": round(q(dds, 0.5), 2), "worst_loss_streak": streaks[-1], "loss_streak_95": q(streaks, 0.95),
            "equity_p05": round(q(finals, 0.05), 2), "equity_median": round(q(finals, 0.5), 2), "equity_p95": round(q(finals, 0.95), 2), "expectancy": round(exp, 4),
            "capital_required_for_dd95_at_20pct": round(starting_equity * dd95 / 20, 2) if dd95 else None,
            "deployment": "APPROVED" if all(checks.values()) else "REJECTED", "checks": checks}


def validation_job(asset: str, days: int) -> dict:
    """Full fetch + walk-forward + Monte Carlo. Runs in a separate PROCESS so it can never starve the trading loop."""
    bars = days * 1440 + 300
    cs = fetch_candles(asset, bars)
    btc = cs if asset == "BTC" else fetch_candles("BTC", bars)
    days_avail = max(1, int((cs[-1]["t"] - cs[0]["t"]) / DAY))
    tr, va, te = (20, 5, 5) if days_avail >= 30 else (max(2, days_avail * 2 // 3), max(1, days_avail // 6), max(1, days_avail // 6))
    res = walk_forward_days(cs, btc, tr, va, te, step_days=max(1, days_avail // 10))
    res["window_days"] = {"train": tr, "validate": va, "test": te, "available": days_avail}
    full = run_backtest(cs, res["windows"][-1]["params"] if res["windows"] else None, btc_candles=btc)
    mc = monte_carlo_ruin(full["trade_pnls"], starting_equity=10000.0)
    return {"asset": asset, "days": days, "candles": len(cs), "walk_forward": res, "monte_carlo": mc,
            "full_period": {k: v for k, v in full.items() if k not in ("equity", "trade_pnls", "_cost")},
            "deployment": "APPROVED" if res["deployment"] == "APPROVED" and mc["deployment"] == "APPROVED" else "REJECTED"}
