"""Finds and screenshots the iPhone Mirroring window. The capture is of the
window itself, so it still works if other windows are covering it.
"""
import numpy as np
import Quartz

WINDOW_NAME_HINT = "iPhone Mirroring"


def list_windows():
    """Info for all on-screen windows."""
    options = Quartz.kCGWindowListOptionOnScreenOnly
    return Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)


def find_window(name_hint=WINDOW_NAME_HINT):
    """Find a window by owner or title. Returns {left, top, width, height,
    window_id} or None."""
    for info in list_windows():
        owner = info.get("kCGWindowOwnerName", "") or ""
        title = info.get("kCGWindowName", "") or ""
        if name_hint.lower() in owner.lower() or name_hint.lower() in title.lower():
            bounds = info.get("kCGWindowBounds")
            if not bounds:
                continue
            return {
                "left": int(bounds["X"]),
                "top": int(bounds["Y"]),
                "width": int(bounds["Width"]),
                "height": int(bounds["Height"]),
                "window_id": int(info["kCGWindowNumber"]),
            }
    return None


def pixel_to_screen(win, img_shape, px, py):
    """Convert image pixels to screen points for mouse events."""
    scale_x = img_shape[1] / win["width"]
    scale_y = img_shape[0] / win["height"]
    return win["left"] + px / scale_x, win["top"] + py / scale_y


def grab_window(window_id):
    """Screenshot a window by ID. Returns an RGB array (H, W, 3)."""
    image_option = (
        Quartz.kCGWindowImageBoundsIgnoreFraming
        | Quartz.kCGWindowImageBestResolution
    )
    cg_image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        image_option,
    )
    if cg_image is None:
        raise RuntimeError(f"Failed to capture window {window_id}")

    width = Quartz.CGImageGetWidth(cg_image)
    height = Quartz.CGImageGetHeight(cg_image)
    bytes_per_row = Quartz.CGImageGetBytesPerRow(cg_image)
    # A window that's closing or minimizing can return an empty frame. Fail
    # here with a clear message rather than inside OpenCV later.
    if width <= 0 or height <= 0 or bytes_per_row < width * 4:
        raise RuntimeError(
            f"Window {window_id} returned an unusable frame "
            f"({width}x{height}, {bytes_per_row} bytes/row)")

    data_provider = Quartz.CGImageGetDataProvider(cg_image)
    data = Quartz.CGDataProviderCopyData(data_provider)
    buf = np.frombuffer(data, dtype=np.uint8)
    expected = height * bytes_per_row
    if buf.size < expected:
        raise RuntimeError(
            f"Window {window_id} returned a short frame "
            f"({buf.size} bytes, expected {expected})")
    arr = buf[:expected].reshape((height, bytes_per_row // 4, 4))[:, :width, :]
    # frombuffer doesn't copy, but the channel reorder below does, so the
    # result doesn't depend on `data` staying alive.
    return arr[:, :, [2, 1, 0]]  # BGRA -> RGB
