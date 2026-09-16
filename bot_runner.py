"""Runs the bot on a background thread when the dashboard asks.

The live modules are only imported when a live run starts. They need macOS
and Quartz, and this way demo mode works anywhere.
"""
import threading
import traceback

import pause_control
import telemetry

_thread = None
_lock = threading.Lock()


def _run_demo():
    import demo_play
    demo_play.run()


def _run_live():
    import capture
    import auto_play

    win = capture.find_window()
    if win is None:
        telemetry.set_status(
            "error",
            "No iPhone Mirroring window found. Open iPhone Mirroring with Block "
            "Blast on screen, then press Start again.")
        telemetry.log("iPhone Mirroring window not found", "error")
        return
    telemetry.log(f"Attached to iPhone Mirroring ({win['width']}x{win['height']})")
    auto_play.run(win)


def start(mode):
    """Start a "demo" or "live" run, unless one is already going."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False, "A run is already in progress."

        pause_control.clear_stop()
        telemetry.start_run(mode)

        def target():
            try:
                if mode == "live":
                    _run_live()
                else:
                    _run_demo()
            except Exception as exc:
                telemetry.log(f"{type(exc).__name__}: {exc}", "error")
                telemetry.end_run("error", f"Run stopped on an error: {exc}")
                traceback.print_exc()
            else:
                if telemetry.snapshot()["status"] not in ("error", "stuck"):
                    telemetry.end_run("stopped", "Run finished.")
            finally:
                pause_control.clear_stop()

        _thread = threading.Thread(target=target, name=f"bot-{mode}", daemon=True)
        _thread.start()
        return True, f"{mode} run started"


def stop(timeout=6.0):
    """Stop the run and wait for it to finish. If it hasn't finished in time
    we keep hold of the thread, so Start can't launch a second run while
    the first is still moving the mouse.
    """
    global _thread
    pause_control.request_stop()
    with _lock:
        t = _thread
    if t is not None:
        t.join(timeout)
        if t.is_alive():
            telemetry.log("Still winding down, finishing the move in flight", "warn")
            return False, "still stopping"
    with _lock:
        if _thread is t:
            _thread = None
    telemetry.end_run("stopped", "Stopped.")
    telemetry.log("Stopped by user", "warn")
    return True, "stopped"
