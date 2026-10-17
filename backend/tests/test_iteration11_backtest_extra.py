# Iteration 11: extra luchii_backtest checks (run_backtest keys/equity, walk_forward, monte carlo, deployment_gate)
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from luchii_backtest import deployment_gate, monte_carlo_summary, run_backtest, run_monte_carlo, walk_forward  # noqa: E402


def synth(n=900, seed=1):
    rng, p, out = random.Random(seed), 100.0, []
    for k in range(n):
        drift = 0.0015 if (k // 150) % 2 == 0 else -0.001
        o = p
        p = max(1.0, p * (1 + drift + rng.gauss(0, 0.002)))
        out.append({"t": k * 60000, "o": o, "h": max(o, p) * 1.001, "l": min(o, p) * 0.999, "c": p, "v": 10 + rng.random() * 20})
    return out


def test_run_backtest_keys_and_equity():
    r = run_backtest(synth())
    for k in ("trades", "profit_factor", "win_rate", "sharpe", "max_drawdown", "equity", "regime_pf"):
        assert k in r
    assert min(r["equity"]) >= 0 and set(r["regime_pf"]) == {"BULL", "BEAR", "CHOP"}
    assert 0 <= r["win_rate"] <= 100 and r["max_drawdown"] >= 0


def test_walk_forward_windows():
    ws = walk_forward(synth(1200), train_bars=500, test_bars=300, step=300, grid={"rsi_min": [52, 55], "atr_stop": [2.5]})
    assert len(ws) >= 1
    for w in ws:
        assert "stability" in w and "overfit" in w and "equity" not in w and isinstance(w["overfit"], bool)


def test_monte_carlo_and_gate():
    pnls = [5, -3, 2, -1, 7, -4, 3]
    a, b = run_monte_carlo(pnls, 200, seed=7), run_monte_carlo(pnls, 200, seed=7)
    assert a == b and len(a) == 200 and all(math.isclose(c[-1], 10009) for c in a)
    s = monte_carlo_summary(a)
    assert s["worst"] <= s["p05"] <= s["median"] <= s["p95"] <= s["best"]
    assert deployment_gate([])["pass"] is False
