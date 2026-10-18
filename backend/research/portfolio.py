from dataclasses import dataclass
from decimal import Decimal

from .events import (Event, EventStore, PortfolioSnapshot, UnknownEventTypeError, sign_snapshot, validate_snapshot)

ZERO = Decimal("0")


def D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _validate_quantity(q: Decimal):
    if q <= ZERO:
        raise ValueError("Quantity must be > 0")


def _validate_price(p: Decimal):
    if p <= ZERO:
        raise ValueError("Price must be > 0")


@dataclass
class Position:
    symbol: str
    quantity: Decimal
    average_price: Decimal
    market_price: Decimal
    side: str = "LONG"
    realized_pnl: Decimal = ZERO

    @property
    def unrealized_pnl(self) -> Decimal:
        d = self.market_price - self.average_price
        return (d if self.side == "LONG" else -d) * self.quantity

    def to_dict(self) -> dict:
        return {"quantity": str(self.quantity), "average_price": str(self.average_price), "market_price": str(self.market_price),
                "side": self.side, "realized_pnl": str(self.realized_pnl)}


class PortfolioLedger:
    """Decimal, multi-asset, long/short, event-emitting."""

    def __init__(self, starting_cash, event_store: EventStore | None = None):
        self.starting_cash = D(starting_cash)
        self.cash = D(starting_cash)
        self.realized_pnl = ZERO
        self.fees_paid = ZERO
        self.positions: dict[str, Position] = {}
        self.events = event_store if event_store is not None else EventStore()

    def _emit(self, kind, **payload):
        self.events.emit(kind, {k: (str(v) if isinstance(v, Decimal) else v) for k, v in payload.items()})

    def buy(self, symbol, quantity, price, fee="0"):
        q, p, f = D(quantity), D(price), D(fee)
        _validate_quantity(q); _validate_price(p)
        cost = q * p + f
        if cost > self.cash:
            raise ValueError("Insufficient cash")
        self.cash -= cost
        self.fees_paid += f
        pos = self.positions.get(symbol)
        if pos is None:
            self.positions[symbol] = Position(symbol, q, p, p, "LONG")
        else:
            if pos.side != "LONG":
                raise ValueError("Cannot buy into SHORT")
            tot = pos.quantity + q
            pos.average_price = (pos.average_price * pos.quantity + p * q) / tot
            pos.quantity, pos.market_price = tot, p
        self._emit("POSITION_BOUGHT", symbol=symbol, qty=q, price=p, fee=f)

    def sell(self, symbol, quantity, price, fee="0"):
        q, p, f = D(quantity), D(price), D(fee)
        _validate_quantity(q); _validate_price(p)
        pos = self.positions.get(symbol)
        if pos is None or pos.side != "LONG":
            raise ValueError("Position missing")
        if q > pos.quantity:
            raise ValueError("Invalid quantity")
        pnl = (p - pos.average_price) * q - f
        self.realized_pnl += pnl; pos.realized_pnl += pnl; self.fees_paid += f
        self.cash += q * p - f
        pos.quantity -= q; pos.market_price = p
        if pos.quantity == ZERO:
            del self.positions[symbol]
        self._emit("POSITION_SOLD", symbol=symbol, qty=q, price=p, fee=f, pnl=pnl)
        return pnl

    def short_sell(self, symbol, quantity, price, fee="0"):
        q, p, f = D(quantity), D(price), D(fee)
        _validate_quantity(q); _validate_price(p)
        if symbol in self.positions:
            raise ValueError("Position exists")
        self.cash += q * p - f; self.fees_paid += f
        self.positions[symbol] = Position(symbol, q, p, p, "SHORT")
        self._emit("SHORT_OPENED", symbol=symbol, qty=q, price=p, fee=f)

    def cover_short(self, symbol, quantity, price, fee="0"):
        q, p, f = D(quantity), D(price), D(fee)
        _validate_quantity(q); _validate_price(p)
        pos = self.positions.get(symbol)
        if pos is None or pos.side != "SHORT":
            raise ValueError("Short missing")
        if q > pos.quantity:
            raise ValueError("Invalid quantity")
        pnl = (pos.average_price - p) * q - f
        self.realized_pnl += pnl; pos.realized_pnl += pnl; self.fees_paid += f
        self.cash -= q * p + f
        pos.quantity -= q; pos.market_price = p
        if pos.quantity == ZERO:
            del self.positions[symbol]
        self._emit("SHORT_COVERED", symbol=symbol, qty=q, price=p, fee=f, pnl=pnl)
        return pnl

    def mark(self, symbol, price):
        if symbol in self.positions:
            self.positions[symbol].market_price = D(price)

    @property
    def long_market_value(self) -> Decimal:
        return sum((p.market_price * p.quantity for p in self.positions.values() if p.side == "LONG"), ZERO)

    @property
    def short_liability(self) -> Decimal:
        return sum((p.market_price * p.quantity for p in self.positions.values() if p.side == "SHORT"), ZERO)

    @property
    def market_value(self) -> Decimal:
        return self.long_market_value

    @property
    def unrealized_pnl(self) -> Decimal:
        return sum((p.unrealized_pnl for p in self.positions.values()), ZERO)

    @property
    def equity(self) -> Decimal:
        return self.cash + self.long_market_value - self.short_liability

    @property
    def gross_exposure(self) -> Decimal:
        return self.long_market_value + self.short_liability

    def snapshot(self) -> dict:
        self._emit("SNAPSHOT", equity=self.equity)
        return {"cash": self.cash, "equity": self.equity, "realized_pnl": self.realized_pnl, "unrealized_pnl": self.unrealized_pnl}

    def create_snapshot(self) -> PortfolioSnapshot:
        s = PortfolioSnapshot(self.events._sequence, self.cash, self.realized_pnl, {k: v.to_dict() for k, v in self.positions.items()})
        s.signature = sign_snapshot(s)
        self.events.save_snapshot(s)
        return s


class PortfolioProjector:
    """Idempotent, replay-safe projection of events onto a fresh ledger."""

    def __init__(self, starting_cash="10000"):
        self.starting_cash = D(starting_cash)
        self.reset()

    def reset(self):
        self.applied_ids: set[str] = set()
        self.last_sequence = 0
        self.ledger = PortfolioLedger(self.starting_cash, EventStore())

    def restore(self, snap: PortfolioSnapshot):
        validate_snapshot(snap)
        self.reset()
        self.ledger.cash = D(snap.cash); self.ledger.realized_pnl = D(snap.realized_pnl)
        for sym, p in snap.positions.items():
            self.ledger.positions[sym] = Position(sym, D(p["quantity"]), D(p["average_price"]), D(p.get("market_price", p["average_price"])), p.get("side", "LONG"), D(p.get("realized_pnl", "0")))
        self.last_sequence = snap.sequence

    def apply(self, ev: Event):
        if ev.event_id in self.applied_ids or ev.sequence <= self.last_sequence:
            return
        self.applied_ids.add(ev.event_id)
        p = ev.payload
        match ev.event_type:
            case "POSITION_BOUGHT":
                self.ledger.buy(p["symbol"], p["qty"], p["price"], p.get("fee", "0"))
            case "POSITION_SOLD":
                self.ledger.sell(p["symbol"], p["qty"], p["price"], p.get("fee", "0"))
            case "SHORT_OPENED":
                self.ledger.short_sell(p["symbol"], p["qty"], p["price"], p.get("fee", "0"))
            case "SHORT_COVERED":
                self.ledger.cover_short(p["symbol"], p["qty"], p["price"], p.get("fee", "0"))
            case "SNAPSHOT" | "MARK":
                if ev.event_type == "MARK":
                    self.ledger.mark(p["symbol"], p["price"])
            case _:
                raise UnknownEventTypeError(ev.event_type)
        self.last_sequence = ev.sequence

    def project(self, events: list[Event]):
        for ev in events:
            self.apply(ev)
        return self.ledger
