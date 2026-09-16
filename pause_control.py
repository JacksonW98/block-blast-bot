"""Pause and stop flags, set by the Escape hotkey and the dashboard and
checked by the play loop between moves."""
import threading
import time

import telemetry

_paused = threading.Event()
_stop = threading.Event()


# Escape works even when nothing is running, so only update the status
# during a run.
_ACTIVE = ("running", "paused", "starting", "calibrating")


def _announce(paused):
    active = telemetry.snapshot()["status"] in _ACTIVE
    if paused:
        if active:
            telemetry.set_status("paused", "Paused. Press Escape or Resume to continue.")
            telemetry.log("Paused", "warn")
    else:
        if active:
            telemetry.set_status("running", "Running.")
            telemetry.log("Resumed")


def toggle():
    if _paused.is_set():
        _paused.clear()
        _announce(False)
    else:
        _paused.set()
        _announce(True)


def set_paused(value):
    value = bool(value)
    if value == _paused.is_set():
        return
    toggle()


def request_stop():
    """Ask the loop to stop at the next move. Also unpauses, otherwise a
    paused run would never notice."""
    _stop.set()
    _paused.clear()


def clear_stop():
    _stop.clear()
    _paused.clear()


def should_stop():
    return _stop.is_set()


def wait_if_paused(poll_interval=0.1):
    """Block while paused. Returns False if a stop has been requested."""
    while _paused.is_set() and not _stop.is_set():
        time.sleep(poll_interval)
    return not _stop.is_set()
