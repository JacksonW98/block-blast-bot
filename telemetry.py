"""Shared state between the bot and the dashboard.

The bot calls the publish_* functions from its own thread, and the web
server reads snapshot() or waits in wait_for_change(). Everything stored
here is plain JSON-friendly data so the server can send it as is.
"""
import threading
import time
from collections import deque

MAX_LOG = 300

_lock = threading.RLock()
_cond = threading.Condition(_lock)
_version = 0

_state = {
    "mode": "idle",            # idle | live | demo
    # Separate from status: demo mode shows "stuck" between games while
    # the run is still going.
    "running": False,
    "status": "idle",          # idle | starting | running | paused | stopped | stuck | calibrating | error
    "message": "Bot is idle. Pick a mode and press Start.",
    "board": [[None] * 8 for _ in range(8)],
    "tray": [None, None, None],
    "plan": [],
    "active_order": None,
    "combo_counter": 0,
    "combo_maintained": True,
    "batch_index": 0,
    "stats": {
        "games": 0,
        "batches": 0,
        "pieces_placed": 0,
        "lines_cleared": 0,
        "combos_broken": 0,
        "best_streak": 0,
        "elapsed": 0.0,
        "ppm": 0.0,
    },
    "speed": 0.45,
    "started_at": None,
    "ended_at": None,
}

_log = deque(maxlen=MAX_LOG)
_log_seq = 0
_clear_streak = 0


def _bump():
    """Caller must hold the lock."""
    global _version
    _version += 1
    _cond.notify_all()


def _refresh_derived():
    """Update runtime and pieces/min. Caller must hold the lock."""
    started = _state["started_at"]
    if started is None:
        return
    elapsed = (_state["ended_at"] or time.time()) - started
    _state["stats"]["elapsed"] = round(elapsed, 1)
    if elapsed > 0:
        _state["stats"]["ppm"] = round(_state["stats"]["pieces_placed"] / elapsed * 60, 1)


def snapshot():
    with _lock:
        _refresh_derived()
        return {
            "version": _version,
            "log": list(_log),
            **{k: v for k, v in _state.items()},
        }


def version():
    with _lock:
        return _version


def wait_for_change(since, timeout=15.0):
    """Wait for the state to change (or time out) and return a snapshot.
    The timeout doubles as a heartbeat for idle connections."""
    deadline = time.time() + timeout
    with _cond:
        while _version <= since:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            _cond.wait(remaining)
    return snapshot()


# publishing

def log(message, level="info"):
    global _log_seq
    with _lock:
        _log_seq += 1
        _log.append({
            "id": _log_seq,
            "t": time.strftime("%H:%M:%S"),
            "level": level,
            "msg": str(message),
        })
        _bump()


def set_status(status, message=None):
    with _lock:
        _state["status"] = status
        if message is not None:
            _state["message"] = message
        _bump()


def set_speed(seconds):
    with _lock:
        _state["speed"] = max(0.0, float(seconds))
        _bump()


def speed():
    with _lock:
        return _state["speed"]


def start_run(mode):
    """Reset the stats for a new run."""
    global _clear_streak
    with _lock:
        _clear_streak = 0
        _state["mode"] = mode
        _state["running"] = True
        _state["status"] = "starting"
        _state["batch_index"] = 0
        _state["plan"] = []
        _state["active_order"] = None
        _state["started_at"] = time.time()
        _state["ended_at"] = None
        _state["stats"] = {
            "games": 0, "batches": 0, "pieces_placed": 0, "lines_cleared": 0,
            "combos_broken": 0, "best_streak": 0, "elapsed": 0.0, "ppm": 0.0,
        }
        _bump()


def end_run(status="stopped", message=None):
    with _lock:
        if _state["ended_at"] is None:
            _state["ended_at"] = time.time()
        _refresh_derived()
        _state["running"] = False
        _state["status"] = status
        _state["active_order"] = None
        if message is not None:
            _state["message"] = message
        _bump()


def publish_board(board_colors):
    """board_colors: 8x8 list of None or "#rrggbb"."""
    with _lock:
        _state["board"] = [list(row) for row in board_colors]
        _bump()


def publish_tray(tray):
    """tray: three entries, each None or {cells, color, placed, order}."""
    with _lock:
        _state["tray"] = list(tray)
        _bump()


def publish_plan(plan, combo_counter, combo_maintained, batch_index=None):
    with _lock:
        _state["plan"] = list(plan)
        _state["combo_counter"] = int(combo_counter)
        _state["combo_maintained"] = bool(combo_maintained)
        _state["active_order"] = plan[0]["order"] if plan else None
        if batch_index is not None:
            _state["batch_index"] = int(batch_index)
        else:
            _state["batch_index"] += 1
        _state["stats"]["batches"] += 1
        _bump()


def clear_plan():
    """Clear the finished plan so it isn't shown next to the new tray."""
    with _lock:
        _state["plan"] = []
        _state["active_order"] = None
        _bump()


def set_active_order(order):
    with _lock:
        _state["active_order"] = order
        _bump()


def record_placement(lines_cleared, combo_counter):
    """Count a piece that was placed."""
    global _clear_streak
    with _lock:
        _state["stats"]["pieces_placed"] += 1
        _state["stats"]["lines_cleared"] += int(lines_cleared)
        _state["combo_counter"] = int(combo_counter)
        if lines_cleared:
            _clear_streak += 1
            _state["stats"]["best_streak"] = max(_state["stats"]["best_streak"], _clear_streak)
        _refresh_derived()
        _bump()


def record_combo_break():
    global _clear_streak
    with _lock:
        _clear_streak = 0
        _state["stats"]["combos_broken"] += 1
        _bump()


def record_game_over():
    global _clear_streak
    with _lock:
        _clear_streak = 0
        _state["stats"]["games"] += 1
        _bump()
