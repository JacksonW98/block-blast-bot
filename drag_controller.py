"""Drags a piece from the tray onto the board.

The move is pure geometry: a calibrated pickup point plus the measured drag
gain. The caller checks the board afterwards to confirm the piece landed.
"""
import time

import Quartz

import capture
import input_sim
import piece_calibration
import read_state

SUBSTEPS = 10
STEP_DELAY = 0.02


def place_piece(win, img, board_bbox, slot_info, piece, r, c):
    """Drag a piece so its top-left lands on board cell (r, c).

    The shape has to be calibrated already.
    """
    anchor_r, anchor_c = piece_calibration.middle_anchor_cell(piece)

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

    input_sim._post(Quartz.kCGEventLeftMouseUp, target_x, target_y)
    # Give the drop animation a moment before the next screenshot.
    time.sleep(0.15)
