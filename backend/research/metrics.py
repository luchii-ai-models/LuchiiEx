import math
import random
from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class TradingMetrics:
    total_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    expectancy: float
    average_win: float
    average_loss: float
    trades: int

    def dict(self):
        return {k: (None if isinstance(v, float) and math.isinf(v) else v) for k, v in asdict(self).items()}


def safe_average(values):
    return sum(values) / len(values) if values else 0.0


def sharpe_ratio(returns, risk_free_rate=0.0, periods_per_year=252) -> float:
    if len(returns) < 2:
        return 0.0
    ex = np.array(returns, dtype=float) - risk_free_rate / periods_per_year
    vol = np.std(ex, ddof=1)
    return 0.0 if vol == 0 else float(np.mean(ex) / vol * math.sqrt(periods_per_year))


def sortino_ratio(returns, risk_free_rate=0.0, periods_per_year=252) -> float:
    if len(returns) < 2:
        return 0.0
    ex = np.array(returns, dtype=float) - risk_free_rate / periods_per_year
    down = ex[ex < 0]
    if len(down) == 0:
        return float("inf")
    dd = np.std(down, ddof=1) if len(down) > 1 else abs(float(down[0]))
    return 0.0 if dd == 0 else float(np.mean(ex) / dd * math.sqrt(periods_per_year))


def max_drawdown(equity) -> float:
    peak, worst = -math.inf, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            worst = max(worst, (peak - e) / peak * 100)
    return round(worst, 4)


def drawdown_series(equity) -> list:
    out, peak = [], -math.inf
    for e in equity:
        peak = max(peak, e)
        out.append(round((e - peak) / peak * 100, 4) if peak > 0 else 0.0)
    return out


class MetricsCalculator:
    def calculate(self, pnls: list, equity: list | None = None, returns: list | None = None) -> TradingMetrics:
        if not pnls:
            return TradingMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gp, gl = sum(wins), -sum(losses)
        pf = float("inf") if gl == 0 else gp / gl
        wr = len(wins) / len(pnls)
        aw, al = safe_average(wins), abs(safe_average(losses))
        rets = returns if returns is not None else ([equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))] if equity and len(equity) > 1 else [p / 10000 for p in pnls])
        tr = (equity[-1] / equity[0] - 1) * 100 if equity and len(equity) > 1 and equity[0] else sum(pnls)
        return TradingMetrics(round(tr, 4), round(sharpe_ratio(rets), 4), round(sortino_ratio(rets), 4) if not math.isinf(sortino_ratio(rets)) else float("inf"),
                              max_drawdown(equity) if equity else 0.0, round(wr, 4), round(pf, 4) if not math.isinf(pf) else pf,
                              round(wr * aw - (1 - wr) * al, 6), round(aw, 6), round(al, 6), len(pnls))


# ---------- benchmark-relative ----------
def alpha(strategy_return, benchmark_return) -> float:
    return strategy_return - benchmark_return


def beta(strategy_returns, benchmark_returns) -> float:
    if len(strategy_returns) < 2 or len(strategy_returns) != len(benchmark_returns):
        return 0.0
    var = np.var(benchmark_returns)
    return 0.0 if var == 0 else float(np.cov(strategy_returns, benchmark_returns)[0][1] / var)


def information_ratio(strategy_returns, benchmark_returns) -> float:
    if len(strategy_returns) < 2 or len(strategy_returns) != len(benchmark_returns):
        return 0.0
    ex = np.array(strategy_returns) - np.array(benchmark_returns)
    te = np.std(ex)
    return 0.0 if te == 0 else float(np.mean(ex) / te)


def benchmark_report(strategy_equity: list, bench_closes: dict[str, list]) -> dict:
    """bench_closes: {'BTC': [...], 'ETH': [...]} aligned to strategy_equity length."""
    n = len(strategy_equity)
    s_ret = [strategy_equity[i] / strategy_equity[i - 1] - 1 for i in range(1, n)] if n > 1 else []
    s_tot = (strategy_equity[-1] / strategy_equity[0] - 1) * 100 if n > 1 and strategy_equity[0] else 0.0
    out = {"strategy_return": round(s_tot, 4), "benchmarks": {}}
    for name, closes in bench_closes.items():
        c = closes[-n:] if len(closes) >= n else closes
        if len(c) < 2:
            continue
        b_ret = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
        m = min(len(s_ret), len(b_ret))
        b_tot = (c[-1] / c[0] - 1) * 100
        out["benchmarks"][name] = {"return": round(b_tot, 4), "alpha": round(alpha(s_tot, b_tot), 4),
                                   "beta": round(beta(s_ret[-m:], b_ret[-m:]), 4), "information_ratio": round(information_ratio(s_ret[-m:], b_ret[-m:]), 4)}
    if len(bench_closes) > 1:
        tots = [v["return"] for v in out["benchmarks"].values()]
        ew = sum(tots) / len(tots)
        out["benchmarks"]["EQUAL_WEIGHT"] = {"return": round(ew, 4), "alpha": round(s_tot - ew, 4), "beta": None, "information_ratio": None}
    return out


# ---------- Monte Carlo (seeded, bootstrap of per-trade returns) ----------
@dataclass
class MonteCarloResult:
    iterations: int
    median_return: float
    worst_return: float
    best_return: float
    p5: float
    p95: float
    ruin_probability: float
    survival_rate: float
    max_drawdown_p95: float


class MonteCarloEngine:
    def __init__(self, iterations=1000, seed=42, ruin_threshold=0.5):
        self.iterations, self.seed, self.ruin = iterations, seed, ruin_threshold

    def run(self, trade_returns: list) -> list:
        rng = random.Random(self.seed)
        out = []
        for _ in range(self.iterations):
            eq = 1.0
            for r in rng.choices(trade_returns, k=len(trade_returns)):
                eq *= 1 + r
            out.append(eq)
        return out

    def run_curves(self, trade_returns: list) -> list[list[float]]:
        rng = random.Random(self.seed)
        curves = []
        for _ in range(self.iterations):
            eq, c = 1.0, [1.0]
            for r in rng.choices(trade_returns, k=len(trade_returns)):
                eq *= 1 + r; c.append(eq)
            curves.append(c)
        return curves

    def report(self, trade_returns: list) -> MonteCarloResult:
        if not trade_returns:
            return MonteCarloResult(self.iterations, 0, 0, 0, 0, 0, 1.0, 0.0, 0.0)
        curves = self.run_curves(trade_returns)
        finals = sorted(c[-1] for c in curves)
        ruined = sum(1 for c in curves if min(c) <= self.ruin)
        dds = sorted(max_drawdown(c) for c in curves)
        q = lambda p: finals[min(len(finals) - 1, int(p * len(finals)))]  # noqa: E731
        return MonteCarloResult(self.iterations, round((q(0.5) - 1) * 100, 3), round((finals[0] - 1) * 100, 3), round((finals[-1] - 1) * 100, 3),
                                round((q(0.05) - 1) * 100, 3), round((q(0.95) - 1) * 100, 3), round(ruined / len(curves), 4),
                                round(1 - ruined / len(curves), 4), dds[min(len(dds) - 1, int(0.95 * len(dds)))])
