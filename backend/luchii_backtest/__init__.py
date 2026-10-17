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


def run_backtest(candles: list, params: dict = None, cash: float = 10000.0, fee: float = 0.001, slippage: float = 0.0005,
                 btc_candles: list = None) -> dict:
    p = {"score_min": 80, "adx_min": 25, "rsi_min": 55, "trend_min": 0.08, "volume_min": 1.5, "atr_stop": 2.5, **(params or {})}
    old = dict(strategy.RULES["entry"]), strategy.RULES["risk"]["atr_stop_mult"]
    strategy.RULES["entry"].update(score_min=p["score_min"], adx_min=p["adx_min"], rsi_min=p["rsi_min"], trend_pct_min=p["trend_min"], volume_ratio_min=p["volume_min"])
    strategy.RULES["risk"]["atr_stop_mult"] = p["atr_stop"]
    equity, trades, regimes = [cash], [], []
    pos = {"position_qty": 0.0, "entry_price": 0.0, "peak_price": 0.0, "stop_price": 0.0, "tiers_done": 0, "entry_atr": 0.0}
    cost_basis = 0.0
    try:
        for k in range(210, len(candles) - 1):
            window = candles[:k + 1]
            price = window[-1]["c"]
            closes = [c["c"] for c in window]
            i = strategy.indicators(window, price, rsi(closes))
            bi = strategy.indicators(btc_candles[:k + 1], btc_candles[k]["c"], rsi([c["c"] for c in btc_candles[:k + 1]])) if btc_candles else i
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
        strategy.RULES["entry"].update(old[0])
        strategy.RULES["risk"]["atr_stop_mult"] = old[1]
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
