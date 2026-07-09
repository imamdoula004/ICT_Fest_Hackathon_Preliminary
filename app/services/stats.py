"""Live per-room booking statistics.

Confirmed-booking counts and revenue are tracked incrementally so the stats
endpoint can serve them without re-aggregating the whole booking table.
"""
import time

_stats: dict[int, dict] = {}


def _aggregate_pause() -> None:
    time.sleep(0.1)


import threading

_lock = threading.Lock()
_room_locks: dict[int, threading.Lock] = {}


def _get_room_lock(room_id: int) -> threading.Lock:
    with _lock:
        if room_id not in _room_locks:
            _room_locks[room_id] = threading.Lock()
        return _room_locks[room_id]


def record_create(room_id: int, price_cents: int) -> None:
    room_lock = _get_room_lock(room_id)
    with room_lock:
        current = _stats.get(room_id, {"count": 0, "revenue": 0})
        count, revenue = current["count"], current["revenue"]
        _aggregate_pause()
        _stats[room_id] = {"count": count + 1, "revenue": revenue + price_cents}


def record_cancel(room_id: int, price_cents: int) -> None:
    room_lock = _get_room_lock(room_id)
    with room_lock:
        current = _stats.get(room_id, {"count": 0, "revenue": 0})
        count, revenue = current["count"], current["revenue"]
        _aggregate_pause()
        _stats[room_id] = {"count": max(0, count - 1), "revenue": revenue - price_cents}


def get(room_id: int) -> dict:
    room_lock = _get_room_lock(room_id)
    with room_lock:
        return _stats.get(room_id, {"count": 0, "revenue": 0})
