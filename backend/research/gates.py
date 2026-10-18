from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

GATES_PATH = Path(__file__).resolve().parent.parent / "risk_gates.yaml"


class GateConfigError(Exception):
    pass


class BacktestConfig(BaseModel):
    minimum_sharpe: float = Field(ge=0)
    minimum_sortino: float = Field(ge=0)
    minimum_profit_factor: float = Field(ge=0)
    minimum_win_rate: float = Field(ge=0, le=1)
    minimum_expectancy: float
    minimum_alpha: float
    minimum_trades: int = Field(ge=1)


class RiskConfig(BaseModel):
    maximum_drawdown: float = Field(ge=0, le=100)
    maximum_daily_drawdown: float = Field(ge=0, le=100)
    maximum_leverage: float = Field(gt=0)
    maximum_position_weight: float = Field(gt=0, le=100)


class StressConfig(BaseModel):
    minimum_survival_rate: float = Field(ge=0, le=1)
    btc_crash_10_max_loss: float = Field(ge=0, le=100)
    btc_crash_20_max_loss: float = Field(ge=0, le=100)
    btc_crash_40_max_loss: float = Field(ge=0, le=100)


class MonteCarloConfig(BaseModel):
    iterations: int = Field(ge=100)
    minimum_median_return: float
    maximum_ruin_probability: float = Field(ge=0, le=1)
    minimum_survival_rate: float = Field(ge=0, le=1)


class ExecutionConfig(BaseModel):
    maximum_slippage_bps: float = Field(ge=0)
    minimum_fill_ratio: float = Field(ge=0, le=1)


class RollbackConfig(BaseModel):
    maximum_drawdown: float = Field(ge=0, le=100)
    minimum_sharpe: float
    minimum_survival_rate: float = Field(ge=0, le=1)


class GateConfig(BaseModel):
    version: int
    backtest: BacktestConfig
    risk: RiskConfig
    stress: StressConfig
    monte_carlo: MonteCarloConfig
    execution: ExecutionConfig
    rollback: RollbackConfig


def load_gates(path: Path | str = GATES_PATH) -> GateConfig:
    try:
        with open(path) as fh:
            return GateConfig(**yaml.safe_load(fh))
    except ValidationError as e:
        raise GateConfigError(str(e)) from e
    except (KeyError, TypeError) as e:
        raise GateConfigError(f"Malformed gates: {e}") from e


@dataclass
class PromotionDecision:
    strategy: str
    status: str
    reasons: list[str] = field(default_factory=list)
    score: float = 100.0
    checks: list[dict] = field(default_factory=list)


class PromotionEngine:
    """Every check is recorded (pass/fail); any failure → REJECT."""

    def __init__(self, gates: GateConfig):
        self.g = gates

    def evaluate(self, report: dict) -> PromotionDecision:
        bt, mc, st, ex, bench = report["backtest"], report["monte_carlo"], report["stress"], report.get("execution", {}), report.get("benchmark", {})
        g = self.g
        best_alpha = max([v["alpha"] for v in bench.get("benchmarks", {}).values() if v.get("alpha") is not None] or [0.0])
        by = {s["scenario"]: s for s in st}
        pf = bt["profit_factor"] if bt["profit_factor"] is not None else float("inf")
        so = bt["sortino_ratio"] if bt["sortino_ratio"] is not None else float("inf")
        checks = [
            ("trades", bt["trades"], ">=", g.backtest.minimum_trades, 10),
            ("sharpe", bt["sharpe_ratio"], ">=", g.backtest.minimum_sharpe, 15),
            ("sortino", so, ">=", g.backtest.minimum_sortino, 10),
            ("profit_factor", pf, ">=", g.backtest.minimum_profit_factor, 15),
            ("win_rate", bt["win_rate"], ">=", g.backtest.minimum_win_rate, 5),
            ("expectancy", bt["expectancy"], ">", g.backtest.minimum_expectancy, 10),
            ("alpha_vs_best_benchmark", best_alpha, ">=", g.backtest.minimum_alpha, 10),
            ("max_drawdown", bt["max_drawdown"], "<=", g.risk.maximum_drawdown, 25),
            ("mc_survival", mc["survival_rate"], ">=", g.monte_carlo.minimum_survival_rate, 20),
            ("mc_ruin", mc["ruin_probability"], "<=", g.monte_carlo.maximum_ruin_probability, 20),
            ("mc_median_return", mc["median_return"], ">=", g.monte_carlo.minimum_median_return, 10),
            ("btc_crash_10", by.get("btc_crash_10", {}).get("loss_pct", 0), "<=", g.stress.btc_crash_10_max_loss, 10),
            ("btc_crash_20", by.get("btc_crash_20", {}).get("loss_pct", 0), "<=", g.stress.btc_crash_20_max_loss, 10),
            ("btc_crash_40", by.get("btc_crash_40", {}).get("loss_pct", 0), "<=", g.stress.btc_crash_40_max_loss, 15),
            ("stress_survival", sum(1 for s in st if s["survived"]) / len(st) if st else 0, ">=", g.stress.minimum_survival_rate, 15),
        ]
        if ex:
            checks += [("slippage_bps", ex.get("slippage_bps", 0), "<=", g.execution.maximum_slippage_bps, 5),
                       ("fill_ratio", ex.get("fill_ratio", 1), ">=", g.execution.minimum_fill_ratio, 5)]
        reasons, score, rows = [], 100.0, []
        for name, val, op, thr, penalty in checks:
            ok = {">=": val >= thr, ">": val > thr, "<=": val <= thr}[op]
            rows.append({"check": name, "value": None if isinstance(val, float) and val == float("inf") else round(float(val), 4), "op": op, "threshold": thr, "passed": ok})
            if not ok:
                reasons.append(f"{name} {op} {thr} failed (got {val if val != float('inf') else '∞'})")
                score -= penalty
        return PromotionDecision(report["strategy"], "PROMOTE" if not reasons else "REJECT", reasons, max(0.0, score), rows)


class RollbackMonitor:
    def __init__(self, policy: RollbackConfig):
        self.p = policy

    def evaluate(self, live: dict) -> list[str]:
        why = []
        if live.get("drawdown", 0) > self.p.maximum_drawdown:
            why.append(f"live drawdown {live['drawdown']:.1f}% > {self.p.maximum_drawdown}%")
        if live.get("sharpe") is not None and live["sharpe"] < self.p.minimum_sharpe:
            why.append(f"live sharpe {live['sharpe']:.2f} < {self.p.minimum_sharpe}")
        if live.get("survival_rate") is not None and live["survival_rate"] < self.p.minimum_survival_rate:
            why.append(f"survival {live['survival_rate']:.2f} < {self.p.minimum_survival_rate}")
        return why
