"""Live play loop: read the board, plan a batch, drag each piece into place
and check the board afterwards. Started and stopped from the dashboard.
"""
import time
import traceback
from pathlib import Path

import capture
import drag_controller
import hotkey
import pause_control
import piece_calibration
import read_state
import solver
import telemetry

POLL_INTERVAL = 0.5
MATCH_POLL_INTERVAL = 0.05  # runs after every placement, so poll faster
DEBOUNCE_READS = 2
BOARD_MATCH_THRESHOLD = 0.97
STABLE_READS_REQUIRED = 2


def _publish_view(img, board_bbox, board, detailed, orders=None, placed=None,
                  with_tray=True):
    """Send the board (and usually the tray) to the dashboard.

    `board` decides which cells are filled and `img` is only used for colors.
    Reading occupancy from a random frame showed "ghost blocks" whenever it
    caught an animation or the game's placement preview.
    """
    try:
        telemetry.publish_board(read_state.read_board_colors(img, board_bbox, board))
        if not with_tray:
            return
        tray = []
        for i, slot in enumerate(detailed):
            if slot is None:
                tray.append(None)
                continue
            tray.append({
                "cells": [[int(v) for v in row] for row in slot["shape"]],
                "color": read_state.piece_color(img, slot),
                "order": (orders or {}).get(i),
                "placed": bool(placed and i in placed),
            })
        telemetry.publish_tray(tray)
    except Exception as exc:
        telemetry.log(f"dashboard view update skipped: {exc}", "warn")


def _plan_view(result, tray_colors):
    """The planned moves in the format the dashboard draws."""
    view = []
    for move in result["sequence"]:
        piece = move["piece"]
        ph, pw = piece.shape
        view.append({
            "order": move["order"],
            "slot": move["slot"],
            "row": int(move["row"]),
            "col": int(move["col"]),
            "lines_cleared": int(move["lines_cleared"]),
            "cells": [[move["row"] + r, move["col"] + c]
                      for r in range(ph) for c in range(pw) if piece[r, c]],
            "color": tray_colors.get(move["slot"], read_state.FALLBACK_BLOCK_COLOR),
        })
    return view


def _tray_fingerprint(detailed):
    """Shape and rounded position of each tray piece. Comparing positions as
    well as shapes means we wait out pieces that are still moving."""
    parts = []
    for d in detailed:
        if d is None:
            parts.append(None)
            continue
        x0, y0, x1, y1 = d["bbox"]
        parts.append((d["shape"].shape, d["shape"].tobytes(),
                      round(x0), round(y0), round(x1), round(y1)))
    return tuple(parts)


def read_slot_with_retry(win, slot_idx, board_bbox, attempts=4, delay=0.15):
    """Read one tray slot, retrying a few times. Pieces don't move between
    slots during a batch, so an empty reading is usually just an animation
    that hasn't finished. Returns (img, slot_info or None).
    """
    img = slot_info = None
    for _attempt in range(attempts):
        img = capture.grab_window(win["window_id"])
        slot_info = read_state.read_tray_detailed(img, board_bbox)[slot_idx]
        if slot_info is not None:
            return img, slot_info
        time.sleep(delay)
    return img, slot_info


def read_stable_state(win, required_matches=STABLE_READS_REQUIRED, max_attempts=8):
    """Keep reading until the board and tray look the same on consecutive
    frames, so we never plan from a frame caught mid-animation."""
    prev_fp = None
    matches = 0
    last = None
    for _ in range(max_attempts):
        img = capture.grab_window(win["window_id"])
        board_bbox = read_state.find_board_bbox(img)
        board = read_state.read_board(img, board_bbox)
        detailed = read_state.read_tray_detailed(img, board_bbox)
        fp = (board.tobytes(), _tray_fingerprint(detailed))
        last = (img, board_bbox, board, detailed)
        if fp == prev_fp:
            matches += 1
            if matches >= required_matches:
                return last
        else:
            matches = 0
        prev_fp = fp
        time.sleep(POLL_INTERVAL)
    return last


def wait_for_board_match(win, expected_board, board_bbox,
                         threshold=BOARD_MATCH_THRESHOLD, timeout=2.0):
    """Wait until the screen matches expected_board on a couple of
    consecutive frames, or give up after timeout."""
    consistent = 0
    total_cells = expected_board.size
    start = time.time()
    while time.time() - start < timeout:
        try:
            img = capture.grab_window(win["window_id"])
            board = read_state.read_board(img, board_bbox)
        except Exception:
            consistent = 0
            time.sleep(MATCH_POLL_INTERVAL)
            continue
        if (board == expected_board).sum() / total_cells >= threshold:
            consistent += 1
            if consistent >= DEBOUNCE_READS:
                return True
        else:
            consistent = 0
        time.sleep(MATCH_POLL_INTERVAL)
    return False


def run_batch(win):
    """Plan and play one batch. Returns True if something happened, False if
    the tray was empty, or "stuck" if a piece can't go anywhere.
    """
    img, board_bbox, board, detailed = read_stable_state(win)
    _publish_view(img, board_bbox, board, detailed)

    if any(d is not None and piece_calibration.needs_calibration(d["shape"]) for d in detailed):
        telemetry.set_status("calibrating",
                             "New piece shape: click the highlighted cell in the popup window "
                             "to record its pickup point.")
        telemetry.log("Uncalibrated piece shape, waiting for a click in the calibration window", "warn")
    piece_calibration.ensure_tray_calibrated(win, img, board_bbox)
    telemetry.set_status("running", "Running against iPhone Mirroring.")

    pieces_by_slot = [d["shape"] if d else None for d in detailed]
    pieces_available = sum(p is not None for p in pieces_by_slot)
    if pieces_available == 0:
        return False

    counter = solver.load_combo_counter()
    result = solver.plan(board, pieces_by_slot, start_counter=counter)

    if len(result["sequence"]) < pieces_available:
        # No new pieces come until the tray is empty, so a piece that can't
        # be placed means the game is over. Don't bother placing the rest.
        unplaced = pieces_available - len(result["sequence"])
        telemetry.log(f"Stuck: only {len(result['sequence'])}/{pieces_available} pieces "
                      f"placeable, which would strand the other {unplaced}", "error")
        return "stuck"

    tray_colors = {}
    for i, slot in enumerate(detailed):
        if slot is not None:
            try:
                tray_colors[i] = read_state.piece_color(img, slot)
            except Exception:
                pass
    telemetry.publish_plan(_plan_view(result, tray_colors), counter, result["combo_maintained"])
    orders = {m["slot"]: m["order"] for m in result["sequence"]}
    _publish_view(img, board_bbox, board, detailed, orders=orders)
    if not result["combo_maintained"]:
        telemetry.record_combo_break()
        telemetry.log("Combo cannot be kept this batch, placing all pieces instead", "warn")

    placed_slots = set()
    sim_board = board.copy()
    running_counter = counter
    for move in result["sequence"]:
        # Only pause or stop between moves. Stopping mid-drag would leave the
        # piece hanging over the board.
        if not pause_control.wait_if_paused():
            solver.save_combo_counter(running_counter)
            telemetry.clear_plan()
            return True

        telemetry.set_active_order(move["order"])
        img, slot_info = read_slot_with_retry(win, move["slot"], board_bbox)
        if slot_info is None:
            telemetry.log(f"Step {move['order']}: slot {move['slot']} still empty after "
                          f"retries, skipped", "warn")
            continue

        drag_result = drag_controller.place_piece(
            win, img, board_bbox, slot_info, move["piece"], move["row"], move["col"])

        if not drag_result["committed"]:
            telemetry.log(f"Step {move['order']}: drag to ({move['row']},{move['col']}) "
                          f"aborted, re-planning from a fresh read", "error")
            # The rest of the plan assumed this move happened, so replan.
            solver.save_combo_counter(running_counter)
            telemetry.clear_plan()
            time.sleep(0.5)
            return True

        expected_board, _lines = solver.place(sim_board, move["piece"], move["row"], move["col"])
        if wait_for_board_match(win, expected_board, board_bbox):
            sim_board = expected_board
        else:
            sim_board = read_state.read_board(capture.grab_window(win["window_id"]), board_bbox)
        running_counter = 0 if move["lines_cleared"] else running_counter + 1

        placed_slots.add(move["slot"])
        batch_done = len(placed_slots) == len(result["sequence"])
        telemetry.record_placement(move["lines_cleared"], running_counter)
        telemetry.log(f"Step {move['order']}: slot {move['slot']} to "
                      f"({move['row']},{move['col']})" +
                      (f", cleared {move['lines_cleared']} line"
                       f"{'s' if move['lines_cleared'] != 1 else ''}" if move["lines_cleared"] else ""),
                      "good" if move["lines_cleared"] else "info")
        try:
            # The tray isn't re-read here. Once the last piece is placed a new
            # tray gets dealt, and the next batch reads that properly.
            _publish_view(capture.grab_window(win["window_id"]), board_bbox, sim_board, detailed,
                          orders=orders, placed=placed_slots, with_tray=not batch_done)
        except Exception:
            pass

    # Save the counter we actually tracked, not the solver's prediction,
    # which is wrong if a step got skipped.
    solver.save_combo_counter(running_counter)
    telemetry.clear_plan()
    time.sleep(1.2)
    return True


def run(win):
    """Play until stopped or the game ends."""
    stuck_streak = 0
    hotkey.start()
    telemetry.set_status("running", "Running against iPhone Mirroring.")
    while True:
        try:
            if not pause_control.wait_if_paused():
                telemetry.log("Stopping at user request")
                return
            found_work = run_batch(win)
        except Exception as e:
            # Usually a one-off bad frame, so log where it happened and carry on.
            where = traceback.extract_tb(e.__traceback__)[-1]
            origin = f"{Path(where.filename).name}:{where.lineno} in {where.name}"
            telemetry.log(f"{type(e).__name__} at {origin}: {e}", "error")
            found_work = False

        if found_work == "stuck":
            stuck_streak += 1
            # Stuck twice in a row, so it's a real game over and not a misread.
            if stuck_streak >= 2:
                telemetry.record_game_over()
                telemetry.end_run("stuck", "Game over: a piece had no legal placement anywhere.")
                return
        else:
            stuck_streak = 0

        if pause_control.should_stop():
            telemetry.log("Stopping at user request")
            return
        if found_work == "stuck" or not found_work:
            time.sleep(1.5)
