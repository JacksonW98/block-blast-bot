"""Synthetic mouse events for dragging pieces.

Needs Accessibility permission, otherwise the events are silently ignored.
Drags are sent as lots of small steps with short delays, since the game
doesn't follow one big jump properly.
"""
import math
import subprocess
import time

import AppKit
import Quartz


def focus_iphone_mirroring():
    """Bring iPhone Mirroring to the front. Drags don't register unless
    it's focused. Skipped if it's already frontmost."""
    frontmost = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if frontmost is not None and frontmost.localizedName() == "iPhone Mirroring":
        return
    subprocess.run(
        ["osascript", "-e", 'tell application "iPhone Mirroring" to activate'],
        capture_output=True,
    )
    time.sleep(0.15)


BUTTON = Quartz.kCGMouseButtonLeft

# The piece moves this much further than the cursor. Measured by dragging
# a piece from row 8 to row 1: 22.72pt of cursor movement per row against a
# 34.56pt cell. It's the same across the whole board.
DRAG_GAIN = 1.5212

_source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)


def _post(event_type, x, y):
    ev = Quartz.CGEventCreateMouseEvent(_source, event_type, (x, y), BUTTON)
    Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, 1)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _ease_in_out(t):
    return 0.5 - 0.5 * math.cos(math.pi * t)
