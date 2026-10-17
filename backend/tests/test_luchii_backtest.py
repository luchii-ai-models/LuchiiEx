import math

import pytest

import strategy
from luchii_backtest import (deployment_gate, execute_long_stop, execute_long_tp, max_drawdown, monte_carlo_summary, optimization_score,
                             parameter_sets, profit_factor, run_backtest, run_monte_carlo, walk_forward)


def test_position_size():
    assert strategy.position_size(10000, 0.01, 100, 98) == 50


def test_never_overbuy():
    assert strategy.position_size(1000, 1.0, 100, 99) <= 10


def test_zero_stop():
    assert strategy.position_size(10000, 0.01, 100, 100) == 0


def test_gap_below_stop():
    assert execute_long_stop(100, 95, 94) == (95, "gap_stop")


def test_normal_stop():
    assert execute_long_stop(100, 101, 99) == (100, "stop")


def test_no_stop():
    assert execute_long_stop(100, 101, 100.5) == (None, None)


def test_gap_through_tp():
    assert execute_long_tp(105, 108, 110) == (108, "gap_tp")


def test_normal_tp():
    assert execute_long_tp(105, 101, 106) == (105, "tp")


def test_break_even_trigger():
    assert strategy.update_stop(100, 101, 0.60, 1, 98) == 100


def test_profit_lock():
    assert strategy.update_stop(100, 102, 1.25, 1, 100) >= 100.25


def test_atr_trail_stages():
    assert strategy.update_stop(100, 110, 2.5, 1, 100) == 107
    assert strategy.update_stop(100, 110, 5.5, 1, 100) == 108


def test_pf_exact():
    assert profit_factor([100, 100, -100]) == 2


def test_pf_inf_and_zero_and_empty():
    assert profit_factor([100, 50]) == float("inf")
    assert profit_factor([-50, -40]) == 0
    assert profit_factor([]) == 0.0


def test_max_drawdown():
    assert math.isclose(max_drawdown([100, 110, 99, 120]), 10.0)


def test_regimes():
    assert strategy.classify_regime(200, 150, 30) in ("TREND_BULL", "BREAKOUT")
    assert strategy.classify_regime(100, 150, 30) == "TREND_BEAR"
    assert strategy.classify_regime(151, 150, 15) == "CHOP"
    assert strategy.classify_regime(150, 150, 30) == "CHOP"


def test_mc_reproducible_and_shape():
    a = run_monte_carlo([1, 2, 3], simulations=5, seed=42)
    b = run_monte_carlo([1, 2, 3], simulations=5, seed=42)
    assert a == b and len(a) == 5 and len(a[0]) == 4
    s = monte_carlo_summary(run_monte_carlo([100, -20, 50], simulations=100))
    assert {"median", "best", "worst", "p05", "p95"} <= set(s)


def test_parameter_sets():
    assert len(list(parameter_sets({"a": [1, 2], "b": [3, 4, 5]}))) == 6


def test_optimization_score_penalises_drawdown():
    good = {"profit_factor": 2.0, "sharpe": 1.5, "trades": 50, "max_drawdown": 5}
    bad = {**good, "max_drawdown": 25}
    assert optimization_score(good) > optimization_score(bad)


def _synthetic(n=900, seed=1):
    import random
    rng = random.Random(seed)
    p, out = 100.0, []
    for k in range(n):
        drift = 0.0006 if (k // 150) % 2 == 0 else -0.0003
        p *= 1 + drift + rng.gauss(0, 0.0015)
        hi, lo = p * (1 + abs(rng.gauss(0, 0.001))), p * (1 - abs(rng.gauss(0, 0.001)))
        out.append({"t": k, "o": p, "h": hi, "l": lo, "c": p, "v": 100 + rng.random() * 200 + (300 if k % 40 == 0 else 0)})
    return out


def test_backtest_runs_and_accounts_cash():
    c = _synthetic()
    r = run_backtest(c, {"score_min": 60, "adx_min": 15, "volume_min": 1.0, "trend_min": 0.01})
    assert r["equity"][0] == 10000 and all(e > 0 for e in r["equity"])
    assert r["trades"] >= 0 and 0 <= r["win_rate"] <= 100 and r["max_drawdown"] >= 0


def test_walk_forward_and_gate():
    c = _synthetic(1500)
    wf = walk_forward(c, train_bars=600, test_bars=300, step=300, grid={"rsi_min": [55], "atr_stop": [2.5], "trend_min": [0.01], "volume_min": [1.0]})
    assert len(wf) == 3 and all("stability" in r and "overfit" in r for r in wf)
    g = deployment_gate(wf)
    assert "pass" in g and set(g["checks"]) == {"oos_pf>=1.5", "sharpe>=1.25", "trades>=50", "dd<=12", "stability 0.7-1.3"}
    assert deployment_gate([])["pass"] is False


@pytest.mark.parametrize("vals", [[None], [float("nan")]])
def test_nan_rejection(vals):
    assert strategy.nan(*vals)
