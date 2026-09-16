"""Escape pauses and resumes the bot from anywhere.

iPhone Mirroring has focus while the bot plays, so a normal shortcut
wouldn't see the key. This uses a CGEventTap, which needs Accessibility
permission.
"""
import threading

import Quartz

import pause_control
import telemetry

ESCAPE_KEYCODE = 53

# Look these up once at import. pyobjc's lazy lookup isn't thread-safe and
# can raise KeyError inside the event callback, which kills the listener.
_get_field = Quartz.CGEventGetIntegerValueField
_KEYCODE_FIELD = Quartz.kCGKeyboardEventKeycode

_lock = threading.Lock()
_thread = None


def _tap_callback(proxy, event_type, event, refcon):
    if event_type == Quartz.kCGEventKeyDown:
        if _get_field(event, _KEYCODE_FIELD) == ESCAPE_KEYCODE:
            pause_control.toggle()
    return event


def _run_listener():
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown),
        _tap_callback,
        None,
    )
    if tap is None:
        telemetry.log("Couldn't install the Escape pause hotkey. Grant Accessibility "
                      "permission in System Settings to enable it", "warn")
        return

    run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), run_loop_source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    Quartz.CFRunLoopRun()


def start():
    """Start the listener thread if it isn't running already. Every live run
    calls this, and two listeners would toggle pause twice per keypress.
    """
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        _thread = threading.Thread(target=_run_listener, daemon=True)
        _thread.start()
        return _thread
