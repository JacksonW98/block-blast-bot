"""Demo mode: the real solver playing the simulated game, with a pause
between moves so you can watch it."""
import time

import pause_control
import simulator
import solver
import telemetry

GAME_OVER_PAUSE = 2.0  # seconds to leave a game over on screen


def _plan_view(sequence, tray_colors):
    """The planned moves in the format the dashboard draws."""
    view = []
    for move in sequence:
        piece = move["piece"]
        ph, pw = piece.shape
        cells = [[move["row"] + r, move["col"] + c]
                 for r in range(ph) for c in range(pw) if piece[r, c]]
        view.append({
            "order": move["order"],
            "slot": move["slot"],
            "row": int(move["row"]),
            "col": int(move["col"]),
            "lines_cleared": int(move["lines_cleared"]),
            "cells": cells,
            "color": tray_colors[move["slot"]],
        })
    return view


def _sleep_interruptibly(seconds):
    """Sleep, but wake up quickly if Stop is pressed. Returns False if it was."""
    end = time.time() + seconds
    while time.time() < end:
        if pause_control.should_stop():
            return False
        time.sleep(min(0.05, max(0.0, end - time.time())))
    return True


def _publish_state(game, orders=None, placed=None):
    telemetry.publish_board(game.board_colors())
    telemetry.publish_tray(game.tray_view(orders=orders, placed=placed))


def run(seed=None):
    game = simulator.Game(seed=seed)
    counter = 0

    telemetry.set_status("running", "Demo running against the simulated board.")
    telemetry.log("Demo mode started: real solver, simulated board")
    _publish_state(game)

    while not pause_control.should_stop():
        if not pause_control.wait_if_paused():
            break

        pieces_by_slot = list(game.tray)
        available = sum(p is not None for p in pieces_by_slot)
        if available == 0:
            game.deal()
            continue

        result = solver.plan(game.board, pieces_by_slot, start_counter=counter)
        sequence = result["sequence"]
        tray_colors = [simulator.PALETTE[i] for i in game.tray_colors]
        orders = {m["slot"]: m["order"] for m in sequence}

        if len(sequence) < available:
            # A piece with nowhere to go means game over.
            telemetry.clear_plan()
            _publish_state(game, orders={}, placed=set())
            telemetry.record_game_over()
            telemetry.set_status("stuck", "Game over: a piece had no legal placement anywhere.")
            telemetry.log(f"Game over: only {len(sequence)}/{available} pieces placeable", "error")
            if not _sleep_interruptibly(GAME_OVER_PAUSE):
                break
            game = simulator.Game()
            counter = 0
            solver.save_combo_counter(counter)
            telemetry.set_status("running", "New game started.")
            telemetry.log("Starting a fresh board")
            _publish_state(game)
            continue

        telemetry.publish_plan(_plan_view(sequence, tray_colors), counter, result["combo_maintained"])
        # Update the tray now so its step numbers show up with the plan.
        _publish_state(game, orders=orders, placed=set())
        if result["combo_broken"]:
            telemetry.record_combo_break()
            telemetry.log("Combo cannot be kept this batch, placing all pieces instead", "warn")

        placed = set()
        for move in sequence:
            if not pause_control.wait_if_paused():
                return
            # Pause before placing, standing in for the time a real drag takes.
            telemetry.set_active_order(move["order"])
            if not _sleep_interruptibly(telemetry.speed()):
                return
            lines = game.place(move["slot"], move["row"], move["col"])
            counter = solver.next_counter(counter, lines)
            placed.add(move["slot"])
            solver.save_combo_counter(counter)
            telemetry.record_placement(lines, counter)
            # After the last piece a new tray is dealt, so don't label the new
            # pieces with this batch's step numbers.
            batch_done = len(placed) == len(sequence)
            _publish_state(game,
                           orders={} if batch_done else orders,
                           placed=set() if batch_done else placed)

            note = f", cleared {lines} line{'s' if lines != 1 else ''}" if lines else ""
            telemetry.log(f"Step {move['order']}: slot {move['slot']} to "
                          f"({move['row']},{move['col']}){note}",
                          "good" if lines else "info")

        telemetry.clear_plan()

    telemetry.log("Demo stopped")
