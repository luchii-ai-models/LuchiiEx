"""BacktestRunner v2: real LUCHII_003 signals → fill engine (fees + slippage + depth) → event-sourced ledger → metrics/MC/stress → promotion."""
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import strategy  # noqa: E402
from luchii_backtest import fetch_candles, indicator_series  # noqa: E402

from .events import validate_sequence, validate_uniqueness  # noqa: E402
from .execution import FeeModel, FillEngine, SlippageModel  # noqa: E402
from .gates import PromotionEngine, load_gates  # noqa: E402
from .metrics import MetricsCalculator, MonteCarloEngine, benchmark_report, drawdown_series  # noqa: E402
from .portfolio import D, PortfolioLedger, PortfolioProjector  # noqa: E402
from .stress import fully_invested_ledger, health_score, run_all  # noqa: E402

STRATEGY_VERSION = "luchii003-guard-v2"


def synthetic_book(price: float, side: str, depth_usd: float = 250_000, levels: int = 5, spread_bps: float = 2.0):
    """Depth ladder around price for fill simulation (replace with recorded books when available)."""
    step = price * spread_bps / 10000
    first = price * (1 + spread_bps / 20000) if side == "ask" else price * (1 - spread_bps / 20000)
    per = depth_usd / levels / price
    return [((first + i * step) if side == "ask" else (first - i * step), per * (1 + i)) for i in range(levels)]


class BacktestRunnerV2:
    def __init__(self, cash=10000.0, fee_bps=10, slippage_bps=5, risk_pct=0.01, max_weight=0.5):
        self.cash0 = cash
        self.fees = FeeModel(taker_fee_bps=fee_bps, maker_fee_bps=max(0, fee_bps - 2))
        self.slip = SlippageModel(slippage_bps)
        self.fill = FillEngine(self.fees)
        self.risk_pct, self.max_weight = risk_pct, max_weight

    def run(self, asset: str, candles: list, btc_candles: list | None) -> dict:
        led = PortfolioLedger(self.cash0)
        ind, bind = indicator_series(candles), indicator_series(btc_candles) if btc_candles else None
        pos = {"position_qty": 0.0, "entry_price": 0.0, "peak_price": 0.0, "stop_price": 0.0, "tiers_done": 0, "entry_atr": 0.0, "fee_pct": 0.1}
        equity, pnls, regimes, slips, ratios, cost, blocked = [self.cash0], [], [], [], [], Decimal(0), 0
        for k in range(1, len(candles) - 1):
            i, nxt = ind[k], candles[k + 1]
            if not i:
                equity.append(float(led.equity)); continue
            bi = (bind[k] if bind and bind[k] else i)
            d = strategy.evaluate(asset, i, bi, pos)
            if d["action"] == "BUY" and pos["position_qty"] <= 0 and strategy.production_guard(i, bi, asset, d.get("score")):
                d = {**d, "action": "HOLD"}
                blocked += 1
            px = nxt["o"]
            if d["action"] == "BUY" and pos["position_qty"] <= 0:
                stop = d.get("stop") or px * (1 - 0.0075)
                usd = min(float(led.equity) * self.max_weight, float(led.equity) * self.risk_pct / max(1e-9, (px - stop) / px), float(led.cash) * 0.985)
                if usd < 50:
                    equity.append(float(led.equity)); continue
                want = D(usd) / D(self.slip.apply_buy(px))
                rep = self.fill.execute_buy(want, synthetic_book(float(self.slip.apply_buy(px)), "ask"), taker=False)
                led.buy(asset, rep.filled_quantity, rep.average_price, rep.total_fees)
                slips.append(float(rep.total_slippage / rep.total_notional * 10000)); ratios.append(float(rep.fill_ratio)); cost += rep.total_fees
                pos.update(position_qty=float(rep.filled_quantity), entry_price=float(rep.average_price), peak_price=float(rep.average_price),
                           stop_price=stop, tiers_done=0, entry_atr=d.get("entry_atr") or i.get("atr") or 0.0)
                regimes.append(d.get("regime", "?"))
            elif d["action"] == "SELL" and pos["position_qty"] > 0:
                frac = d.get("fraction") or 1.0
                qty = D(pos["position_qty"]) * D(frac) if frac < 1 else D(pos["position_qty"])
                qty = min(qty, led.positions[asset].quantity)
                rep = self.fill.execute_sell(qty, synthetic_book(float(self.slip.apply_sell(px)), "bid"), taker=True)
                pnl = led.sell(asset, rep.filled_quantity, rep.average_price, rep.total_fees)
                cost += rep.total_fees
                left = pos["position_qty"] - float(rep.filled_quantity)
                if left * px < 1:
                    pnls.append(float(pnl) + float(pos.get("_partial", 0)))
                    pos.update(position_qty=0.0, entry_price=0.0, peak_price=0.0, stop_price=0.0, tiers_done=0, entry_atr=0.0, _partial=0.0)
                else:
                    pos.update(position_qty=left, tiers_done=pos["tiers_done"] + 1, stop_price=max(pos["stop_price"], d.get("stop") or 0), _partial=pos.get("_partial", 0) + float(pnl))
            else:
                if pos["position_qty"] > 0 and d.get("stop"):
                    pos["stop_price"] = max(pos["stop_price"], d["stop"])
            if pos["position_qty"] > 0:
                pos["peak_price"] = max(pos["peak_price"], nxt["h"])
            led.mark(asset, nxt["c"])
            equity.append(float(led.equity))
        if pos["position_qty"] > 0:
            pnl = led.sell(asset, led.positions[asset].quantity, candles[-1]["c"], self.fees.calculate(D(candles[-1]["c"]) * led.positions[asset].quantity))
            pnls.append(float(pnl) + float(pos.get("_partial", 0))); equity.append(float(led.equity))
        events = led.events.all()
        validate_sequence(events); validate_uniqueness(events)
        rebuilt = PortfolioProjector(self.cash0).project([e for e in events if e.event_type != "SNAPSHOT"])
        replay_ok = rebuilt.cash == led.cash and rebuilt.realized_pnl == led.realized_pnl
        return {"ledger": led, "equity": equity, "pnls": pnls, "regimes": regimes, "events": len(events), "replay_ok": replay_ok, "blocked_entries": blocked,
                "execution": {"slippage_bps": round(sum(slips) / len(slips), 3) if slips else 0.0, "fill_ratio": round(sum(ratios) / len(ratios), 4) if ratios else 1.0,
                              "fees_paid": float(cost)}}


def validation_job(asset: str, days: int = 30, cash: float = 10000.0) -> dict:
    """Blocking: runs in a worker process. Returns the full promotion report."""
    t0 = time.time()
    bars = min(3000 * max(1, days // 2), 43200)
    cs = fetch_candles(asset, bars)
    btc = cs if asset == "BTC" else fetch_candles("BTC", bars)
    bench = {"BTC": [c["c"] for c in btc]}
    if asset != "BTC":
        bench[asset] = [c["c"] for c in cs]
    r = BacktestRunnerV2(cash).run(asset, cs, btc)
    mc_cfg = load_gates().monte_carlo
    met = MetricsCalculator().calculate(r["pnls"], r["equity"])
    rets = [p / cash for p in r["pnls"]]
    mc = MonteCarloEngine(mc_cfg.iterations, 42, 0.5).report(rets)
    prices = {"BTC": btc[-1]["c"], asset: cs[-1]["c"]}
    stress = run_all(fully_invested_ledger(r["equity"][-1], {asset: 0.5, "BTC": 0.5} if asset != "BTC" else {"BTC": 1.0}, prices))
    bm = benchmark_report(r["equity"], bench)
    report = {"strategy": "luchii003", "version": STRATEGY_VERSION, "asset": asset, "days": days, "candles": len(cs), "cash": cash,
              "backtest": met.dict(), "monte_carlo": mc.__dict__, "stress": stress, "benchmark": bm, "execution": r["execution"],
              "events": r["events"], "replay_ok": r["replay_ok"], "blocked_entries": r["blocked_entries"], "regimes": {x: r["regimes"].count(x) for x in set(r["regimes"])},
              "equity_curve": r["equity"][:: max(1, len(r["equity"]) // 300)], "drawdown_curve": drawdown_series(r["equity"])[:: max(1, len(r["equity"]) // 300)],
              "runtime_s": round(time.time() - t0, 1)}
    dec = PromotionEngine(load_gates()).evaluate(report)
    worst = max([s["loss_pct"] for s in stress] or [0])
    report["health"] = health_score(met.max_drawdown, 1.0, None, worst)
    report["promotion"] = {"status": dec.status, "score": dec.score, "reasons": dec.reasons, "checks": dec.checks}
    return report
