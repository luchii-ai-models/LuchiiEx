"""Run: python -m luchii_backtest.run BTC [bars]  → prints backtest, walk-forward, Monte Carlo and deployment gate."""
import json
import sys

from luchii_backtest import deployment_gate, fetch_candles, monte_carlo_summary, run_backtest, run_monte_carlo, walk_forward

asset = sys.argv[1] if len(sys.argv) > 1 else "BTC"
bars = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
candles = fetch_candles(asset, bars)
btc = candles if asset == "BTC" else fetch_candles("BTC", bars)
bt = run_backtest(candles, btc_candles=btc)
eq = bt.pop("equity")
print(f"== {asset} backtest on {len(candles)} 1m bars ==")
print(json.dumps({k: v for k, v in bt.items() if k != "_cost"}, default=str, indent=1))
wf = walk_forward(candles, grid={"rsi_min": [55], "atr_stop": [2.5, 3.0], "trend_min": [0.08], "volume_min": [1.4]}, btc_candles=btc)
print("== walk-forward ==")
for r in wf:
    print({k: r[k] for k in ("window", "trades", "profit_factor", "sharpe", "max_drawdown", "train_pf", "stability", "overfit")})
print("== deployment gate ==", json.dumps(deployment_gate(wf), default=str))
trades = [e2 - e1 for e1, e2 in zip(eq, eq[1:]) if e2 != e1]
print("== monte carlo ==", monte_carlo_summary(run_monte_carlo(trades, 500)))
