import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4


class DuplicateEventError(Exception):
    pass


class SequenceGapError(Exception):
    pass


class SequenceConflictError(Exception):
    pass


class UnknownEventTypeError(Exception):
    pass


@dataclass(frozen=True)
class Event:
    event_id: str
    sequence: int
    event_type: str
    timestamp: datetime
    payload: dict[str, Any] = field(default_factory=dict)


class EventStore:
    def __init__(self):
        self._events: list[Event] = []
        self._ids: set[str] = set()
        self._sequence = 0
        self._snapshots: dict[int, "PortfolioSnapshot"] = {}

    def next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def emit(self, event_type: str, payload: dict) -> Event:
        ev = Event(str(uuid4()), self.next_sequence(), event_type, datetime.now(timezone.utc), payload)
        self.append(ev)
        return ev

    def append(self, event: Event):
        if event.event_id in self._ids:
            raise DuplicateEventError(event.event_id)
        self._ids.add(event.event_id)
        self._events.append(event)
        self._sequence = max(self._sequence, event.sequence)

    def all(self) -> list[Event]:
        return self._events.copy()

    def count(self) -> int:
        return len(self._events)

    def by_type(self, event_type: str) -> list[Event]:
        return [e for e in self._events if e.event_type == event_type]

    def clear(self):
        self._events.clear(); self._ids.clear(); self._sequence = 0

    def save_snapshot(self, snap: "PortfolioSnapshot"):
        self._snapshots[snap.sequence] = snap

    def latest_snapshot(self):
        return self._snapshots[max(self._snapshots)] if self._snapshots else None


def replay(store: EventStore):
    yield from store.all()


def validate_sequence(events: list[Event]):
    if not events:
        return
    expected = events[0].sequence
    for e in events:
        if e.sequence != expected:
            raise SequenceGapError(f"Expected {expected}, got {e.sequence}")
        expected += 1


def validate_uniqueness(events: list[Event]):
    seen = set()
    for e in events:
        if e.sequence in seen:
            raise SequenceConflictError(e.sequence)
        seen.add(e.sequence)


# ---------- snapshots: versioned, HMAC-signed with key ids ----------
CURRENT_VERSION = 2
SUPPORTED_VERSIONS = {1, 2}
SNAPSHOT_KEYS = {"snapshot-key-v1": os.environ.get("SNAPSHOT_KEY_V1", "luchii-snapshot-v1"),
                 "snapshot-key-v2": os.environ.get("SNAPSHOT_KEY_V2", "luchii-snapshot-v2")}
CURRENT_KEY_ID = "snapshot-key-v2"
ALLOWED_POSITION_KEYS = {"quantity", "average_price", "market_price", "side", "realized_pnl"}


@dataclass
class PortfolioSnapshot:
    sequence: int
    cash: Decimal
    realized_pnl: Decimal
    positions: dict
    version: int = CURRENT_VERSION
    key_id: str = CURRENT_KEY_ID
    signature: str = ""
    created_at: str = ""


def snapshot_payload(s: PortfolioSnapshot) -> dict:
    return {"version": s.version, "sequence": s.sequence, "cash": str(s.cash), "realized_pnl": str(s.realized_pnl), "positions": s.positions, "key_id": s.key_id}


def sign_snapshot(s: PortfolioSnapshot) -> str:
    if s.key_id not in SNAPSHOT_KEYS:
        raise ValueError("Unknown key")
    data = json.dumps(snapshot_payload(s), sort_keys=True, default=str).encode()
    return hmac.new(SNAPSHOT_KEYS[s.key_id].encode(), data, hashlib.sha256).hexdigest()


def validate_snapshot(s: PortfolioSnapshot, last_event_sequence: int | None = None):
    if s.version is None or s.version not in SUPPORTED_VERSIONS:
        raise ValueError("Unsupported version")
    if not isinstance(s.sequence, int) or s.sequence <= 0:
        raise ValueError("Invalid sequence")
    if s.cash is None or s.realized_pnl is None:
        raise ValueError("Corrupt snapshot")
    if last_event_sequence is not None and s.sequence > last_event_sequence:
        raise ValueError("Sequence regression")
    for sym, p in (s.positions or {}).items():
        if set(p) - ALLOWED_POSITION_KEYS:
            raise ValueError(f"Unknown position keys for {sym}")
        if Decimal(str(p.get("quantity", "0"))) <= 0:
            raise ValueError(f"Invalid quantity for {sym}")
    if s.key_id not in SNAPSHOT_KEYS:
        raise ValueError("Unknown key")
    if not hmac.compare_digest(sign_snapshot(s), s.signature or ""):
        raise ValueError("Invalid signature")


# ---------- schema migrations ----------
MIGRATIONS, ROLLBACKS = {}, {}


def migration(from_version):
    def wrap(fn):
        MIGRATIONS[from_version] = fn
        return fn
    return wrap


def rollback(version):
    def wrap(fn):
        ROLLBACKS[version] = fn
        return fn
    return wrap


@migration(1)
def _v1_to_v2(d: dict) -> dict:
    d.setdefault("realized_pnl", "0"); d["version"] = 2
    return d


@rollback(2)
def _v2_to_v1(d: dict) -> dict:
    d.pop("realized_pnl", None); d["version"] = 1
    return d


def upgrade_snapshot(d: dict) -> dict:
    v = d.get("version")
    if v not in SUPPORTED_VERSIONS:
        raise ValueError("Unsupported version")
    while d["version"] < CURRENT_VERSION:
        d = MIGRATIONS[d["version"]](d)
    return d


def rollback_snapshot(d: dict) -> dict:
    v = d.get("version")
    if v not in SUPPORTED_VERSIONS or v == 1:
        return d if v == 1 else _raise("Unsupported version")
    return ROLLBACKS[v](d)


def _raise(msg):
    raise ValueError(msg)
