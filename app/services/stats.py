"""Live per-room booking statistics.

Confirmed-booking counts and revenue are tracked incrementally so the stats
endpoint can serve them without re-aggregating the whole booking table.
"""
import time
import threading

_stats: dict[int, dict] = {}
_lock = threading.Lock()


def _aggregate_pause() -> None:
    time.sleep(0.1)


def record_create(room_id: int, price_cents: int) -> None:
    with _lock:
        current = _stats.get(room_id, {"count": 0, "revenue": 0})
        _aggregate_pause()
        _stats[room_id] = {
            "count": current["count"] + 1,
            "revenue": current["revenue"] + price_cents,
        }


def record_cancel(room_id: int, price_cents: int) -> None:
    with _lock:
        current = _stats.get(room_id, {"count": 0, "revenue": 0})
        _aggregate_pause()
        _stats[room_id] = {
            "count": max(0, current["count"] - 1),
            "revenue": current["revenue"] - price_cents,
        }


def get(room_id: int) -> dict:
    return _stats.get(room_id, {"count": 0, "revenue": 0})
