"""Drags a piece from the tray onto the board.

The move is pure geometry: a calibrated pickup point plus the measured drag
gain. Tracking the piece visually during the drag didn't work, because line
clear animations and pickup sparkles kept confusing it. Instead we check
the target cells once before letting go.
"""
import time

import numpy as np
import Quartz

import capture
import input_sim
import piece_calibration
import read_state

SUBSTEPS = 10
STEP_DELAY = 0.02

# A cell counts as showing something (block, held piece or clear preview)
# when most of its middle is brighter than this.
CONTENT_THRESHOLD = 85


def _cell_has_content(img_now, board_bbox, row, col):
    """True if the cell isn't showing empty background."""
    x, y, w, h = board_bbox
    cell_w, cell_h = w / read_state.BOARD_SIZE, h / read_state.BOARD_SIZE
    cx0 = int(x + col * cell_w + cell_w * 0.3)
    cx1 = int(x + col * cell_w + cell_w * 0.7)
    cy0 = int(y + row * cell_h + cell_h * 0.3)
    cy1 = int(y + row * cell_h + cell_h * 0.7)
    patch = img_now[cy0:cy1, cx0:cx1]
    brightness = np.max(patch, axis=2)
    return (brightness > CONTENT_THRESHOLD).mean() > 0.5


def _clear_preview_confirmed(img_now, board_bbox, expected_clear_rows, expected_clear_cols):
    """Whether the lines this move should clear look full. None if it
    doesn't clear anything."""
    if not expected_clear_rows and not expected_clear_cols:
        return None
    for row in expected_clear_rows:
        if not all(_cell_has_content(img_now, board_bbox, row, cc)
                   for cc in range(read_state.BOARD_SIZE)):
            return False
    for col in expected_clear_cols:
        if not all(_cell_has_content(img_now, board_bbox, rr, col)
                   for rr in range(read_state.BOARD_SIZE)):
            return False
    return True


def _target_cells_confirmed(img_now, board_bbox, target_cells):
    return all(_cell_has_content(img_now, board_bbox, row, col) for row, col in target_cells)


def place_piece(win, img, board_bbox, slot_info, piece, r, c):
    """Drag a piece so its top-left lands on (r, c), letting go only once the
    target cells look right. If they don't, the piece goes back to its slot.

    The shape has to be calibrated already. Returns {"committed": bool}.
    """
    anchor_r, anchor_c = piece_calibration.middle_anchor_cell(piece)
    ph, pw = piece.shape
    filled_cells = [(rr, cc) for rr in range(ph) for cc in range(pw) if piece[rr, cc]]

    gx0, gy0, gx1, gy1 = slot_info["bbox"]
    offset_x, offset_y = piece_calibration.get_pickup_offset(piece)
    # We press in the middle of the slot, which is always on the piece. The
    # calibrated point is only used to work out how far to move, since it
    # can sit outside the slot once the piece lifts.
    grab_px = ((gx0 + gx1) / 2.0, (gy0 + gy1) / 2.0)
    reference_pickup_px = (gx0 + offset_x, gy0 + offset_y)

    bx, by, bw, bh = board_bbox
    cell_w, cell_h = bw / read_state.BOARD_SIZE, bh / read_state.BOARD_SIZE
    reference_target_px = (bx + (c + anchor_c + 0.5) * cell_w,
                           by + (r + anchor_r + 0.5) * cell_h)

    grab_screen = capture.pixel_to_screen(win, img.shape, *grab_px)
    reference_pickup_screen = capture.pixel_to_screen(win, img.shape, *reference_pickup_px)
    reference_target_screen = capture.pixel_to_screen(win, img.shape, *reference_target_px)

    target_cells = {(r + rr, c + cc) for rr, cc in filled_cells}
    test_board = read_state.read_board(img, board_bbox)
    test_board[r:r + ph, c:c + pw] |= piece
    expected_clear_rows = [i for i in range(read_state.BOARD_SIZE) if test_board[i, :].all()]
    expected_clear_cols = [j for j in range(read_state.BOARD_SIZE) if test_board[:, j].all()]

    input_sim.focus_iphone_mirroring()
    sx, sy = grab_screen
    input_sim._post(Quartz.kCGEventMouseMoved, sx, sy)
    time.sleep(0.02)
    input_sim._post(Quartz.kCGEventLeftMouseDown, sx, sy)
    time.sleep(0.08)

    target_x = sx + (reference_target_screen[0] - reference_pickup_screen[0]) / input_sim.DRAG_GAIN
    target_y = sy + (reference_target_screen[1] - reference_pickup_screen[1]) / input_sim.DRAG_GAIN

    # Move in small steps. The game doesn't follow a single jump properly.
    for i in range(1, SUBSTEPS + 1):
        t = input_sim._ease_in_out(i / SUBSTEPS)
        input_sim._post(Quartz.kCGEventLeftMouseDragged,
                        sx + (target_x - sx) * t, sy + (target_y - sy) * t)
        time.sleep(STEP_DELAY)
    time.sleep(0.12)

    placement_confirmed = False
    for _attempt in range(3):
        img_check = capture.grab_window(win["window_id"])
        cells_ok = _target_cells_confirmed(img_check, board_bbox, target_cells)
        clear_ok = _clear_preview_confirmed(img_check, board_bbox,
                                            expected_clear_rows, expected_clear_cols)
        if cells_ok and clear_ok is not False:
            placement_confirmed = True
            break
        time.sleep(0.1)

    if placement_confirmed:
        input_sim._post(Quartz.kCGEventLeftMouseUp, target_x, target_y)
        # Give the drop animation a moment before the next screenshot.
        time.sleep(0.15)
        return {"committed": True}

    # Not confirmed, so carry the piece back and drop it in its slot.
    steps_back = 8
    for i in range(1, steps_back + 1):
        t = input_sim._ease_in_out(i / steps_back)
        input_sim._post(Quartz.kCGEventLeftMouseDragged,
                        target_x + (sx - target_x) * t, target_y + (sy - target_y) * t)
        time.sleep(0.02)
    time.sleep(0.05)
    input_sim._post(Quartz.kCGEventLeftMouseUp, sx, sy)
    return {"committed": False}
