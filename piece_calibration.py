"""Per-shape pickup points.

When you hold a piece the game lifts and scales it, so where a cell ends up
on screen can't be worked out from the tray position alone. The first time
the bot sees a new shape it holds the piece down, takes a screenshot, and
asks you to click the center of one reference cell. That offset is saved and
reused for that shape in every slot.

The reference cell is the filled cell nearest the piece's center, because an
edge cell can end up outside the captured window.
"""
import time
import json
import subprocess
from pathlib import Path
import numpy as np
import cv2

import capture
import input_sim
import read_state
import solver
import telemetry

CALIBRATION_FILE = Path(__file__).parent / "piece_calibration.json"
DISPLAY_SCALE = 3  # upscale the prompt so it's easier to click accurately

ALERT_SOUND = "/System/Library/Sounds/Sosumi.aiff"


def _play_alert_sound():
    try:
        subprocess.run(["afplay", ALERT_SOUND], capture_output=True, timeout=3)
    except Exception:
        pass


def shape_key(piece):
    """String key for a trimmed piece, e.g. "110/011"."""
    rows = ["".join("1" if v else "0" for v in row) for row in piece]
    return "/".join(rows)


def middle_anchor_cell(piece):
    """The filled cell closest to the piece's center, used as the reference
    point for both pickup and placement."""
    ph, pw = piece.shape
    filled = [(rr, cc) for rr in range(ph) for cc in range(pw) if piece[rr, cc]]
    mean_r = sum(rr for rr, cc in filled) / len(filled)
    mean_c = sum(cc for rr, cc in filled) / len(filled)
    return min(filled, key=lambda rc: (rc[0] - mean_r) ** 2 + (rc[1] - mean_c) ** 2)


def _load():
    if CALIBRATION_FILE.exists():
        return json.loads(CALIBRATION_FILE.read_text())
    return {}


def _save(data):
    CALIBRATION_FILE.write_text(json.dumps(data, indent=2))


MAX_DISPLAY_DIM = 1000  # keep the popup on screen
LEGEND_CELL_PX = 36


def _render_shape_legend(piece, anchor_r, anchor_c):
    """Small diagram of the piece with the cell to click shown in red."""
    ph, pw = piece.shape
    img = np.full((ph * LEGEND_CELL_PX, pw * LEGEND_CELL_PX, 3), (235, 235, 235), dtype=np.uint8)
    for rr in range(ph):
        for cc in range(pw):
            x0, y0 = cc * LEGEND_CELL_PX, rr * LEGEND_CELL_PX
            x1, y1 = x0 + LEGEND_CELL_PX, y0 + LEGEND_CELL_PX
            if piece[rr, cc]:
                is_anchor = (rr, cc) == (anchor_r, anchor_c)
                color = (230, 60, 60) if is_anchor else (60, 140, 220)
                cv2.rectangle(img, (x0, y0), (x1, y1), color, -1)
            cv2.rectangle(img, (x0, y0), (x1, y1), (120, 120, 120), 1)
    cx = int(anchor_c * LEGEND_CELL_PX + LEGEND_CELL_PX / 2)
    cy = int(anchor_r * LEGEND_CELL_PX + LEGEND_CELL_PX / 2)
    cv2.drawMarker(img, (cx, cy), (255, 255, 255), markerType=cv2.MARKER_CROSS, markerSize=16, thickness=2)
    return img


def _compose_calibration_image(legend, crop):
    """Stack the instructions, the legend and the screenshot. Returns the
    image and the y where the screenshot starts, so clicks above it can be
    ignored.
    """
    lh, lw = legend.shape[:2]
    ch, cw = crop.shape[:2]
    w = max(lw, cw)

    banner_h = 30
    banner = np.full((banner_h, w, 3), (255, 240, 200), dtype=np.uint8)
    cv2.putText(banner, "Click the CENTER of the highlighted (red) cell in the photo below",
                (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 1, cv2.LINE_AA)

    legend_padded = np.full((lh, w, 3), (245, 245, 245), dtype=np.uint8)
    legend_padded[:, :lw] = legend

    divider_h = 4
    divider = np.zeros((divider_h, w, 3), dtype=np.uint8)

    crop_padded = np.zeros((ch, w, 3), dtype=np.uint8)
    crop_padded[:, :cw] = crop

    combined = np.vstack([banner, legend_padded, divider, crop_padded])
    photo_y0 = banner_h + lh + divider_h
    return combined, photo_y0


def _prompt_click_on_image(img_rgb, title, min_y=0):
    """Show the image and wait for a click at or below min_y. Returns the
    click in the image's own pixel coordinates.
    """
    h, w = img_rgb.shape[:2]
    scale = min(DISPLAY_SCALE, MAX_DISPLAY_DIM / max(h, w))
    big = cv2.resize(img_rgb, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_NEAREST)
    bgr = cv2.cvtColor(big, cv2.COLOR_RGB2BGR)

    clicked = {}

    def on_click(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and y / scale >= min_y:
            clicked["pt"] = (x / scale, y / scale)

    cv2.namedWindow(title)
    cv2.setMouseCallback(title, on_click)
    cv2.imshow(title, bgr)
    while "pt" not in clicked:
        cv2.waitKey(20)
    cv2.destroyWindow(title)
    cv2.waitKey(1)  # macOS needs this for the window to actually close
    return clicked["pt"]


def _record_pickup_point(win, slot_info, piece, board_bbox):
    """Hold the piece, screenshot it and ask for a click on its anchor cell.
    Returns the offset from the slot's bbox top-left, in image pixels.

    The screenshot runs from the top of the board down, because a held
    piece lifts up towards the board.
    """
    anchor_r, anchor_c = middle_anchor_cell(piece)
    gx0, gy0, gx1, gy1 = slot_info["bbox"]
    grab_px = ((gx0 + gx1) / 2.0, (gy0 + gy1) / 2.0)

    img_before = capture.grab_window(win["window_id"])
    grab_screen = capture.pixel_to_screen(win, img_before.shape, *grab_px)

    input_sim.focus_iphone_mirroring()
    gsx, gsy = grab_screen
    input_sim._post(input_sim.Quartz.kCGEventMouseMoved, gsx, gsy)
    time.sleep(0.1)
    input_sim._post(input_sim.Quartz.kCGEventLeftMouseDown, gsx, gsy)
    time.sleep(0.3)

    img_now = capture.grab_window(win["window_id"])

    by = board_bbox[1]
    h_margin = 150
    mx0 = max(0, int(min(gx0, board_bbox[0]) - h_margin))
    mx1 = min(img_now.shape[1], int(max(gx1, board_bbox[0] + board_bbox[2]) + h_margin))
    my0 = max(0, int(by - 40))
    my1 = img_now.shape[0]
    crop = img_now[my0:my1, mx0:mx1]

    legend = _render_shape_legend(piece, anchor_r, anchor_c)
    combined, photo_y0 = _compose_calibration_image(legend, crop)

    ph, pw = piece.shape
    click_x, click_y = _prompt_click_on_image(
        combined, f"Click the CENTER of the highlighted cell ({ph}x{pw} piece)", min_y=photo_y0)

    input_sim._post(input_sim.Quartz.kCGEventLeftMouseUp, gsx, gsy)

    photo_click_x, photo_click_y = click_x, click_y - photo_y0
    click_full_x, click_full_y = mx0 + photo_click_x, my0 + photo_click_y
    return click_full_x - gx0, click_full_y - gy0


def needs_calibration(shape):
    """True if this shape has no saved pickup point yet."""
    return shape_key(solver.trim_piece(shape)) not in _load()


def ensure_tray_calibrated(win, img, board_bbox):
    """Record a pickup point for any piece in the tray that doesn't have one."""
    detailed = read_state.read_tray_detailed(img, board_bbox)
    data = _load()
    changed = False
    for slot_info in detailed:
        if slot_info is None:
            continue
        piece = solver.trim_piece(slot_info["shape"])
        key = shape_key(piece)
        if key in data:
            continue
        _play_alert_sound()
        offset = _record_pickup_point(win, slot_info, piece, board_bbox)
        data[key] = list(offset)
        changed = True
        telemetry.log(f"Calibrated a new {piece.shape[0]}x{piece.shape[1]} piece shape "
                      f"(pickup offset {offset[0]:.0f}, {offset[1]:.0f})", "good")
    if changed:
        _save(data)


def get_pickup_offset(piece):
    """Saved (x, y) offset from the slot's bbox top-left to the anchor cell."""
    key = shape_key(piece)
    data = _load()
    if key not in data:
        raise KeyError(f"No pickup calibration recorded for shape {key!r}; "
                        f"call piece_calibration.ensure_tray_calibrated first")
    x, y = data[key]
    return x, y
