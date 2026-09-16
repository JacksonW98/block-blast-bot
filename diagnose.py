"""Backs the Diagnose button: grabs one frame and reports what the vision
code makes of it, step by step, with the numbers the thresholds depend on.

Useful when live play reads the wrong board or never places anything, which
is nearly always a detection problem rather than a planning one.
"""
import cv2
import numpy as np
from PIL import Image

import capture
import read_state

FRAME_PNG = "diagnose_frame.png"
ANNOTATED_PNG = "diagnose_annotated.png"

MIN_BLOB_AREA = 150  # same cutoff read_tray_detailed uses


def collect(save_images=True):
    """Run the vision code on a fresh frame and return a report dict. A
    failed step goes in the report instead of raising."""
    out = {"ok": False, "stage": "window", "notes": []}

    win = capture.find_window()
    if win is None:
        out["error"] = "No iPhone Mirroring window found."
        return out
    out["window"] = {"width": win["width"], "height": win["height"],
                     "left": win["left"], "top": win["top"], "id": win["window_id"]}

    out["stage"] = "capture"
    try:
        img = capture.grab_window(win["window_id"])
    except RuntimeError as exc:
        out["error"] = (f"{exc}. On macOS 15+ this usually means the process running the "
                        f"bot has no Screen Recording permission.")
        return out
    h, w = img.shape[:2]
    out["capture"] = {"width": int(w), "height": int(h),
                      "scale": round(w / win["width"], 3)}
    if save_images:
        Image.fromarray(img).save(FRAME_PNG)
        out["frame_png"] = FRAME_PNG

    out["stage"] = "board"
    try:
        bbox = read_state.find_board_bbox(img)
    except RuntimeError as exc:
        out["error"] = str(exc)
        return out
    bx, by, bw, bh = bbox
    cell = (bw / read_state.BOARD_SIZE + bh / read_state.BOARD_SIZE) / 2
    out["board_bbox"] = {"x": bx, "y": by, "w": bw, "h": bh, "cell_px": round(cell, 1)}
    if abs(bw - bh) > 0.15 * max(bw, bh):
        out["notes"].append("Detected board is far from square, so the largest dark blob "
                            "may not be the board.")

    board = read_state.read_board(img, bbox)
    out["board_filled"] = int(board.sum())
    out["board_ascii"] = read_state.board_to_ascii(board)

    out["stage"] = "tray"
    unit = cell * read_state.TRAY_UNIT_RATIO
    out["tray_unit_px"] = round(unit, 1)
    # Same height floor as read_tray_detailed, so we report the same band.
    band = read_state.find_tray_band(img, by + bh, min_height=unit * 0.8)
    if band is None:
        out["error"] = "No tray band found below the board."
        out["notes"].append("Either the tray is genuinely empty, or vivid_mask does not "
                            "recognise this skin's piece colors as 'filled'.")
        return out
    y0, y1 = band
    out["tray_band"] = {"y0": int(y0), "y1": int(y1), "height": int(y1 - y0),
                        "height_in_cells": round((y1 - y0) / cell, 2)}

    vivid = read_state.vivid_mask(img)
    band_mask = np.zeros(vivid.shape, np.uint8)
    band_mask[y0:y1, :] = vivid[y0:y1, :].astype(np.uint8) * 255
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(band_mask, connectivity=8)
    blobs = [(int(stats[i][cv2.CC_STAT_LEFT]), int(stats[i][cv2.CC_STAT_TOP]),
              int(stats[i][cv2.CC_STAT_WIDTH]), int(stats[i][cv2.CC_STAT_HEIGHT]),
              int(stats[i][cv2.CC_STAT_AREA])) for i in range(1, n)]
    blobs.sort(key=lambda b: -b[4])
    kept = [b for b in blobs if b[4] > MIN_BLOB_AREA]
    out["blobs"] = {"total": len(blobs), "kept": len(kept),
                    "min_area_cutoff": MIN_BLOB_AREA,
                    "areas": [b[4] for b in blobs[:12]]}

    if blobs and not kept:
        out["notes"].append(
            f"Every tray blob is under the area>{MIN_BLOB_AREA} cutoff. One piece cell is "
            f"about {unit:.0f}px across (~{unit ** 2:.0f}px area) in this capture, so "
            f"the cutoff is too high here. This alone makes the tray read as empty.")

    # Redo read_tray_detailed's slot maths so we can say why a slot was dropped.
    slot_width = w / 3
    groups = [[], [], []]
    for b in kept:
        cx = b[0] + b[2] / 2.0
        groups[min(2, max(0, int(cx // slot_width)))].append(b)
    slots = []
    for i, group in enumerate(groups):
        if not group:
            slots.append({"slot": i, "result": "empty", "reason": "no blobs in this third"})
            continue
        gx0 = min(b[0] for b in group)
        gx1 = max(b[0] + b[2] for b in group)
        gy0 = min(b[1] for b in group)
        gy1 = max(b[1] + b[3] for b in group)
        cols = max(1, round((gx1 - gx0) / unit))
        rows = max(1, round((gy1 - gy0) / unit))
        entry = {"slot": i, "blobs": len(group),
                 "span_px": [int(gx1 - gx0), int(gy1 - gy0)],
                 "derived_grid": [rows, cols]}
        if cols > read_state.MAX_PIECE_DIM or rows > read_state.MAX_PIECE_DIM:
            entry["result"] = "dropped"
            entry["reason"] = (f"derived {rows}x{cols} exceeds the {read_state.MAX_PIECE_DIM}-cell "
                               f"maximum, so read_tray_detailed discards it. The span "
                               f"({gx1 - gx0}x{gy1 - gy0}px) divided by the unit size "
                               f"({unit:.1f}px) is too large, so TRAY_UNIT_RATIO likely no "
                               f"longer matches how this capture renders the tray.")
            out["notes"].append(f"Slot {i}: {entry['reason']}")
        else:
            entry["result"] = "ok"
        slots.append(entry)
    out["slots"] = slots

    pieces = read_state.read_tray_detailed(img, bbox)
    out["pieces"] = []
    for i, p in enumerate(pieces):
        if p is None:
            out["pieces"].append(None)
        else:
            out["pieces"].append({
                "rows": int(p["shape"].shape[0]), "cols": int(p["shape"].shape[1]),
                "bbox": [int(v) for v in p["bbox"]],
                "ascii": read_state.piece_to_ascii(p["shape"]),
            })
    out["pieces_detected"] = sum(p is not None for p in pieces)
    out["stage"] = "done"
    out["ok"] = out["pieces_detected"] > 0

    if save_images:
        vis = img.copy()
        cw, ch = bw / read_state.BOARD_SIZE, bh / read_state.BOARD_SIZE
        for r in range(read_state.BOARD_SIZE):
            for c in range(read_state.BOARD_SIZE):
                xi, yi = int(bx + c * cw), int(by + r * ch)
                color = (0, 255, 0) if board[r, c] else (90, 90, 255)
                cv2.rectangle(vis, (xi, yi), (int(xi + cw), int(yi + ch)), color, 1)
        cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), (255, 210, 0), 2)
        cv2.rectangle(vis, (0, y0), (w - 1, y1), (255, 0, 255), 2)
        for b in kept:
            cv2.rectangle(vis, (b[0], b[1]), (b[0] + b[2], b[1] + b[3]), (0, 255, 255), 1)
        for p in pieces:
            if p is None:
                continue
            px0, py0, px1, py1 = (int(v) for v in p["bbox"])
            cv2.rectangle(vis, (px0, py0), (px1, py1), (255, 255, 255), 2)
        Image.fromarray(vis).save(ANNOTATED_PNG)
        out["annotated_png"] = ANNOTATED_PNG
    return out
