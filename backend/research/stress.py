from dataclasses import dataclass, field
from decimal import Decimal

from .portfolio import D, PortfolioLedger

CORRELATIONS = {"BTC": {"ETH": 0.85, "SOL": 1.2}}


@dataclass
class StressScenario:
    name: str
    description: str
    moves: dict = field(default_factory=dict)
    spread_multiplier: float = 1.0
    volume_multiplier: float = 1.0


def btc_crash(pct: float) -> StressScenario:
    return StressScenario(f"btc_crash_{int(abs(pct) * 100)}", f"BTC falls {abs(pct) * 100:.0f}%, alts follow by beta",
                          {"BTC": pct, "ETH": pct * CORRELATIONS["BTC"]["ETH"], "SOL": pct * CORRELATIONS["BTC"]["SOL"]})


SCENARIOS = [btc_crash(-0.10), btc_crash(-0.20), btc_crash(-0.40),
             StressScenario("liquidity_shock", "Spreads ×10, slippage ×5, volume ÷4", {"BTC": -0.03, "ETH": -0.04, "SOL": -0.05}, 10.0, 0.25),
             StressScenario("correlated_selloff", "All majors −25% in one session", {"BTC": -0.25, "ETH": -0.28, "SOL": -0.35})]


class ScenarioRunner:
    def run(self, ledger: PortfolioLedger, scenario: StressScenario) -> dict:
        before = ledger.equity
        sim = PortfolioLedger(ledger.cash)
        for sym, p in ledger.positions.items():
            sim.positions[sym] = type(p)(p.symbol, p.quantity, p.average_price, p.market_price, p.side, p.realized_pnl)
        for sym, move in scenario.moves.items():
            if sym in sim.positions:
                px = sim.positions[sym].market_price * (1 + D(move))
                sim.mark(sym, px)
        # exit cost under widened spread: assume liquidation at (spread_mult × 5 bps) + slippage
        exit_cost = sim.gross_exposure * D(scenario.spread_multiplier) * D("0.0005")
        after = sim.equity - exit_cost
        loss_pct = float((before - after) / before * 100) if before > 0 else 0.0
        return {"scenario": scenario.name, "description": scenario.description, "equity_before": float(before), "equity_after": float(after),
                "loss_pct": round(loss_pct, 3), "survived": after > before * Decimal("0.5")}


def run_all(ledger: PortfolioLedger) -> list[dict]:
    r = ScenarioRunner()
    return [r.run(ledger, s) for s in SCENARIOS]


def fully_invested_ledger(equity: float, weights: dict[str, float], prices: dict[str, float]) -> PortfolioLedger:
    """Worst-case book: capital deployed across assets by weight — what the engine could hold at full exposure."""
    led = PortfolioLedger(equity)
    for sym, w in weights.items():
        usd = D(equity) * D(w)
        if usd > 0 and prices.get(sym):
            led.buy(sym, usd / D(prices[sym]), prices[sym])
    return led


def health_score(drawdown_pct: float, leverage: float, margin_ratio: float | None = None, worst_crash_loss_pct: float = 0.0) -> dict:
    h = 100
    if drawdown_pct > 10:
        h -= 20
    if leverage > 2:
        h -= 20
    if margin_ratio is not None and margin_ratio < 2:
        h -= 25
    if worst_crash_loss_pct > 40:
        h -= 25
    elif worst_crash_loss_pct > 25:
        h -= 10
    band = "SAFE" if h >= 95 else "MODERATE" if h >= 70 else "ELEVATED" if h >= 50 else "CRITICAL"
    return {"score": max(0, h), "band": band}
