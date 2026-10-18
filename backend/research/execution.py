from dataclasses import dataclass
from decimal import Decimal

from .portfolio import D


class FillEngineError(Exception):
    pass


class InsufficientLiquidityError(FillEngineError):
    pass


@dataclass
class ExecutionReport:
    requested_quantity: Decimal
    filled_quantity: Decimal
    average_price: Decimal
    best_price: Decimal
    worst_price: Decimal
    total_notional: Decimal
    total_fees: Decimal
    total_slippage: Decimal
    fill_ratio: Decimal
    execution_latency_ms: int
    fully_filled: bool


class FeeModel:
    def __init__(self, maker_fee_bps=8, taker_fee_bps=10):
        self.maker, self.taker = D(maker_fee_bps), D(taker_fee_bps)

    def calculate(self, notional, taker=True) -> Decimal:
        return D(notional) * (self.taker if taker else self.maker) / 10000


class SlippageModel:
    def __init__(self, slippage_bps=5):
        self.bps = D(slippage_bps)

    def apply_buy(self, price) -> Decimal:
        return D(price) * (1 + self.bps / 10000)

    def apply_sell(self, price) -> Decimal:
        return D(price) * (1 - self.bps / 10000)


def validate_quantity(q):
    if D(q) <= 0:
        raise ValueError("Quantity must be positive")


def validate_orderbook(side):
    if not side:
        raise ValueError("Orderbook side empty")
    for price, qty in side:
        if D(price) <= 0 or D(qty) <= 0:
            raise ValueError("Invalid orderbook level")


def spread_pct(bids, asks) -> Decimal:
    bid, ask = D(bids[0][0]), D(asks[0][0])
    return (ask - bid) / bid * 100


def book_imbalance(bids, asks) -> Decimal:
    b, a = sum(D(q) for _, q in bids), sum(D(q) for _, q in asks)
    return b / a if a else D(0)


class FillEngine:
    """Walks book depth; partial fills; deterministic (latency is a fixed model parameter)."""

    def __init__(self, fee_model: FeeModel | None = None, latency_ms: int = 250, allow_partial: bool = True):
        self.fees = fee_model or FeeModel()
        self.latency_ms = latency_ms
        self.allow_partial = allow_partial

    def _walk(self, quantity, levels):
        validate_quantity(quantity); validate_orderbook(levels)
        remaining, fills = D(quantity), []
        available = sum(D(q) for _, q in levels)
        if available < remaining and not self.allow_partial:
            raise InsufficientLiquidityError(f"Requested {remaining}, available {available}")
        for price, qty in levels:
            if remaining <= 0:
                break
            take = min(remaining, D(qty))
            fills.append((D(price), take)); remaining -= take
        return fills

    def execute_buy(self, quantity, asks, taker=True) -> ExecutionReport:
        return self._report(D(quantity), self._walk(quantity, asks), D(asks[0][0]), taker)

    def execute_sell(self, quantity, bids, taker=True) -> ExecutionReport:
        return self._report(D(quantity), self._walk(quantity, bids), D(bids[0][0]), taker)

    def _report(self, requested, fills, ref_price, taker) -> ExecutionReport:
        qty = sum(q for _, q in fills)
        if qty == 0:
            raise InsufficientLiquidityError("No liquidity")
        notional = sum(p * q for p, q in fills)
        avg = notional / qty
        prices = [p for p, _ in fills]
        return ExecutionReport(requested, qty, avg, min(prices), max(prices), notional, self.fees.calculate(notional, taker),
                               abs(avg - ref_price) * qty, qty / requested, self.latency_ms, qty == requested)


def fill_ratio(requested, fills) -> Decimal:
    return sum(D(q) for _, q in fills) / D(requested)


def position_size(equity, risk_pct, entry_price, stop_price) -> Decimal:
    dist = abs(D(entry_price) - D(stop_price))
    if dist <= 0:
        return D(0)
    return D(equity) * D(risk_pct) / dist
