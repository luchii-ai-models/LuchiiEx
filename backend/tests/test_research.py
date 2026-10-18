"""research package unit tests: ledger, events, replay, snapshots, fills, metrics, MC, stress, gates."""
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017"); os.environ.setdefault("DB_NAME", "test")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research.events import (Event, PortfolioSnapshot, SequenceConflictError, SequenceGapError, UnknownEventTypeError,  # noqa: E402
                             rollback_snapshot, sign_snapshot, upgrade_snapshot, validate_sequence, validate_snapshot, validate_uniqueness)
from research.execution import FeeModel, FillEngine, InsufficientLiquidityError, SlippageModel, position_size  # noqa: E402
from research.gates import GateConfigError, PromotionEngine, load_gates  # noqa: E402
from research.metrics import MetricsCalculator, MonteCarloEngine, alpha, beta, information_ratio, max_drawdown, sharpe_ratio, sortino_ratio  # noqa: E402
from research.portfolio import PortfolioLedger, PortfolioProjector  # noqa: E402
from research.stress import fully_invested_ledger, health_score, run_all  # noqa: E402

now = lambda: datetime.now(timezone.utc)  # noqa: E731


@pytest.fixture
def ledger():
    return PortfolioLedger("10000")


def test_buy_sell_pnl_and_fees(ledger):
    ledger.buy("BTC", 1, 100, "1"); assert ledger.cash == Decimal("9899")
    ledger.buy("BTC", 1, 200); assert ledger.positions["BTC"].average_price == Decimal("150")
    ledger.sell("BTC", 2, 170, "2"); assert ledger.realized_pnl == Decimal("38") and "BTC" not in ledger.positions


def test_invalid_inputs(ledger):
    for bad in ((0, 100), (-1, 100), (1, 0)):
        with pytest.raises(ValueError):
            ledger.buy("BTC", *bad)
    with pytest.raises(ValueError):
        ledger.buy("BTC", 1000, 100)


def test_short_equity_valuation(ledger):
    ledger.short_sell("BTC", 1, 100); ledger.mark("BTC", 80)
    assert ledger.cash == Decimal("10100") and ledger.equity == Decimal("10020") and ledger.unrealized_pnl == Decimal("20")
    ledger.cover_short("BTC", 1, 80); assert ledger.realized_pnl == Decimal("20") and ledger.equity == Decimal("10020")
    ledger.short_sell("ETH", 1, 100); ledger.cover_short("ETH", 1, 120); assert ledger.realized_pnl == Decimal("0")


def test_events_order_and_replay(ledger):
    ledger.buy("BTC", 2, 100, "2"); ledger.sell("BTC", 2, 110); ledger.snapshot()
    ev = ledger.events.all()
    assert [e.event_type for e in ev] == ["POSITION_BOUGHT", "POSITION_SOLD", "SNAPSHOT"] and ev[0].payload["qty"] == "2"
    validate_sequence(ev); validate_uniqueness(ev)
    rebuilt = PortfolioProjector("10000").project(ev)
    assert rebuilt.cash == ledger.cash and rebuilt.realized_pnl == ledger.realized_pnl


def test_projector_idempotent_unknown_reset(ledger):
    ledger.buy("BTC", 1, 100)
    p = PortfolioProjector("10000"); evs = ledger.events.all()
    p.project(evs); c = p.ledger.cash; p.project(evs); assert p.ledger.cash == c
    p.apply(evs[0]); assert p.ledger.positions["BTC"].quantity == 1
    with pytest.raises(UnknownEventTypeError):
        p.apply(Event("x", 99, "FUTURE_EVENT", now(), {}))
    p.reset(); assert p.ledger.cash == Decimal("10000") and not p.applied_ids


def test_sequence_validation():
    with pytest.raises(SequenceGapError):
        validate_sequence([Event("1", 1, "A", now()), Event("2", 3, "A", now())])
    with pytest.raises(SequenceGapError):
        validate_sequence([Event("1", 10, "A", now()), Event("2", 9, "A", now())])
    with pytest.raises(SequenceConflictError):
        validate_uniqueness([Event("1", 1, "A", now()), Event("2", 1, "B", now())])


def test_snapshot_sign_tamper_restore_equivalence():
    full = PortfolioLedger("10000")
    for i in range(1, 41):
        full.buy("BTC", 1, 100 + i)
    for i in range(20):
        full.sell("BTC", 1, 150)
    evs = full.events.all()
    head = PortfolioProjector("10000"); head.project(evs[:30]); snap = head.ledger.create_snapshot()
    snap.sequence = 30
    snap.signature = sign_snapshot(snap)
    validate_snapshot(snap, last_event_sequence=60)
    tail = PortfolioProjector("10000"); tail.restore(snap); tail.project(evs[30:])
    assert tail.ledger.cash == full.cash and tail.ledger.realized_pnl == full.realized_pnl and tail.ledger.equity == full.equity
    for mutate in (lambda s: setattr(s, "cash", Decimal("1")), lambda s: setattr(s, "sequence", 999), lambda s: setattr(s, "version", 999),
                   lambda s: setattr(s, "key_id", "snapshot-key-v0"), lambda s: s.positions.__setitem__("BTC", {**s.positions["BTC"], "admin": True})):
        s2 = PortfolioSnapshot(**{**snap.__dict__}); s2.positions = {k: dict(v) for k, v in snap.positions.items()}
        mutate(s2)
        with pytest.raises(ValueError):
            validate_snapshot(s2)
    legacy = PortfolioSnapshot(**{**snap.__dict__, "key_id": "snapshot-key-v1"}); legacy.signature = sign_snapshot(legacy); validate_snapshot(legacy)
    with pytest.raises(ValueError):
        validate_snapshot(PortfolioSnapshot(-1, Decimal(1), Decimal(0), {}))


def test_snapshot_migrations():
    v1 = {"version": 1, "sequence": 100, "cash": "10000"}
    up = upgrade_snapshot(dict(v1)); assert up["version"] == 2 and "realized_pnl" in up
    assert rollback_snapshot(dict(up)) == v1
    with pytest.raises(ValueError):
        upgrade_snapshot({"version": 999})


def test_fill_engine():
    fe = FillEngine(FeeModel(8, 10), allow_partial=False)
    rep = fe.execute_buy(5, [(100, 2), (101, 3), (102, 5)])
    assert rep.filled_quantity == 5 and rep.average_price == Decimal("100.6") and rep.fully_filled and rep.fill_ratio == 1
    with pytest.raises(InsufficientLiquidityError):
        fe.execute_buy(100, [(100, 1)])
    with pytest.raises(ValueError):
        fe.execute_buy(0, [(100, 1)])
    partial = FillEngine().execute_buy(10, [(100, 4)]); assert partial.fill_ratio == Decimal("0.4") and not partial.fully_filled
    assert SlippageModel(5).apply_buy(100) > 100 > SlippageModel(5).apply_sell(100)
    assert position_size(10000, 0.01, 100, 90) == 10


def test_metrics_edge_cases():
    m = MetricsCalculator()
    z = m.calculate([]); assert z.trades == 0 and z.win_rate == 0
    aw = m.calculate([100, 50, 200]); assert aw.win_rate == 1 and aw.average_loss == 0 and aw.profit_factor == float("inf")
    al = m.calculate([-50, -25]); assert al.win_rate == 0 and al.profit_factor == 0
    mx = m.calculate([100, 100, -50]); assert mx.profit_factor == 4
    assert max_drawdown([1000, 1200, 800, 900]) > 0 and sharpe_ratio([0.01]) == 0 and sortino_ratio([0.01, 0.02]) == float("inf")
    assert alpha(20, 15) == 5 and isinstance(beta([0.1, 0.2, 0.3], [0.1, 0.2, 0.4]), float) and isinstance(information_ratio([0.1, 0.2], [0.1, 0.3]), float)


def test_monte_carlo_seed():
    r = [0.01, -0.02, 0.03]
    assert MonteCarloEngine(100, 123).run(r) == MonteCarloEngine(100, 123).run(r) and MonteCarloEngine(50, 1).run(r) != MonteCarloEngine(50, 2).run(r)
    rep = MonteCarloEngine(200, 7).report(r); assert 0 <= rep.survival_rate <= 1 and rep.iterations == 200


def test_stress_and_health():
    led = fully_invested_ledger(10000, {"BTC": 0.5, "ETH": 0.5}, {"BTC": 80000, "ETH": 2400})
    res = {s["scenario"]: s for s in run_all(led)}
    assert 9 < res["btc_crash_10"]["loss_pct"] < 11 and res["btc_crash_40"]["loss_pct"] > 35 and res["btc_crash_10"]["survived"]
    assert health_score(0, 1)["band"] == "SAFE" and health_score(15, 3, 1, 50)["band"] == "CRITICAL"


def test_gates_and_promotion(tmp_path):
    g = load_gates()
    good = {"strategy": "s", "backtest": {"trades": 40, "sharpe_ratio": 1.8, "sortino_ratio": 2.1, "profit_factor": 1.6, "win_rate": 0.55, "expectancy": 1, "max_drawdown": 12},
            "monte_carlo": {"survival_rate": 0.99, "ruin_probability": 0.01, "median_return": 5}, "benchmark": {"benchmarks": {"BTC": {"alpha": 3}}},
            "stress": [{"scenario": "btc_crash_10", "loss_pct": 8, "survived": True}, {"scenario": "btc_crash_20", "loss_pct": 16, "survived": True}, {"scenario": "btc_crash_40", "loss_pct": 32, "survived": True}],
            "execution": {"slippage_bps": 6, "fill_ratio": 0.99}}
    d = PromotionEngine(g).evaluate(good); assert d.status == "PROMOTE" and d.score == 100
    bad = {**good, "backtest": {**good["backtest"], "sharpe_ratio": 0.2, "max_drawdown": 50}}
    d2 = PromotionEngine(g).evaluate(bad); assert d2.status == "REJECT" and len(d2.reasons) == 2 and d2.score == 60
    p = tmp_path / "g.yaml"; p.write_text("version: 1\nbacktest: {}\n")
    with pytest.raises(GateConfigError):
        load_gates(p)
