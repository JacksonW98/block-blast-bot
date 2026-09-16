"""Combo-aware move planner.

The combo rule: after a line clear you get three placements to clear
another line, and missing on the third breaks the combo. So the counter
(placements since the last clear) can only be 0, 1 or 2.

For each batch we try every order of the pieces and every position for
each, then rank whole sequences by:
  1. placing all the pieces
  2. not emptying the board completely (resets the counter, but leaves
     the next batch nothing to work with)
  3. lowest counter left at the end
  4. board safety: how much usable, unfragmented space is left
  5. number of nearly complete lines left for the next batch

If the combo-safe search can't place every piece but an unconstrained one
can, we take the unconstrained sequence and accept the broken combo. A
piece left in the tray is stuck there for good, since no new pieces come
until the tray is empty.
"""
import json
from pathlib import Path
import cv2
import numpy as np

BOARD_SIZE = 8
# Highest the counter may reach. At 2 the next placement has to clear.
MAX_STREAK = 2
STATE_FILE = Path(__file__).parent / "combo_state.json"


def load_combo_counter():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())["placements_since_clear"]
    return 0


def save_combo_counter(n):
    STATE_FILE.write_text(json.dumps({"placements_since_clear": n}))


def trim_piece(piece):
    """Crop a piece to its filled cells."""
    if not np.any(piece):
        raise ValueError("cannot trim a piece with no filled cells")
    rows = np.any(piece, axis=1)
    cols = np.any(piece, axis=0)
    r0, r1 = np.where(rows)[0][[0, -1]]
    c0, c1 = np.where(cols)[0][[0, -1]]
    return piece[r0:r1 + 1, c0:c1 + 1]


def valid_placements(board, piece):
    ph, pw = piece.shape
    out = []
    for r in range(BOARD_SIZE - ph + 1):
        for c in range(BOARD_SIZE - pw + 1):
            region = board[r:r + ph, c:c + pw]
            if not np.any(region & piece):
                out.append((r, c))
    return out


def place(board, piece, r, c):
    """Place a piece and clear any full lines. Returns (board, lines cleared)."""
    ph, pw = piece.shape
    new_board = board.copy()
    new_board[r:r + ph, c:c + pw] |= piece
    full_rows = new_board.all(axis=1)
    full_cols = new_board.all(axis=0)
    n_cleared = int(full_rows.sum()) + int(full_cols.sum())
    if n_cleared:
        new_board[full_rows, :] = False
        new_board[:, full_cols] = False
    return new_board, n_cleared


def near_complete_lines(board):
    """Score for lines that are close to clearing: 2 per line missing one
    cell, 1 per line missing two. We maximise this rather than lines cleared
    this batch, because it's what gives the next (unknown) pieces a chance
    to keep the combo going.
    """
    filled = np.concatenate((board.sum(axis=1), board.sum(axis=0)))
    missing = BOARD_SIZE - filled
    return int(2 * np.count_nonzero(missing == 1) + np.count_nonzero(missing == 2))


def board_safety(board):
    """How usable the empty space is. A piece has to fit inside one
    connected empty region, so 20 cells in one block is much safer than
    20 cells scattered in pairs. Each region scores size**1.5, which
    favours big regions, and isolated single cells count against.
    """
    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        (~board).view(np.uint8), connectivity=4)
    if n <= 1:
        return 0.0
    sizes = stats[1:, cv2.CC_STAT_AREA]
    big = sizes[sizes >= 2]
    return float(np.sum(big.astype(np.float64) ** 1.5) - np.count_nonzero(sizes == 1))


def _search(board, pieces, start_counter, enforce_constraint):
    """pieces: list of (slot_index, piece). Returns the best result found at
    any node, including partial sequences, since sometimes not every piece
    fits.
    """
    best = None
    # Different orders often reach the same position (A then B vs B then A).
    # The ranking only depends on (pieces left, board, counter, full clear),
    # so skipping a state we've already expanded doesn't change the result.
    seen = set()
    placements_cache = {}
    quality_cache = {}

    def placements(board_key, board, piece_idx, piece):
        key = (board_key, piece_idx)
        cached = placements_cache.get(key)
        if cached is None:
            cached = valid_placements(board, piece)
            placements_cache[key] = cached
        return cached

    def quality(board_key, board):
        cached = quality_cache.get(board_key)
        if cached is None:
            cached = (board_safety(board), near_complete_lines(board))
            quality_cache[board_key] = cached
        return cached

    def consider(seq, counter, total_lines, safety, near_complete, full_clear_occurred):
        nonlocal best
        # Safety ranks above near-complete lines: losing the game is worse
        # than losing the combo.
        key = (-len(seq), full_clear_occurred, counter, -safety, -near_complete)
        if best is None or key < best[0]:
            best = (key, list(seq), counter, total_lines, safety, full_clear_occurred)

    def recurse(remaining, board, counter, seq, total_lines, full_clear_occurred):
        board_key = board.tobytes()
        state = (tuple(idx for idx, _p in remaining), board_key, counter, full_clear_occurred)
        if state in seen:
            return
        seen.add(state)

        safety, near_complete = quality(board_key, board)
        consider(seq, counter, total_lines, safety, near_complete, full_clear_occurred)
        if not remaining:
            return

        for i, (slot_idx, piece) in enumerate(remaining):
            must_clear = enforce_constraint and counter >= MAX_STREAK
            for (r, c) in placements(board_key, board, slot_idx, piece):
                new_board, lines = place(board, piece, r, c)
                cleared = lines > 0
                if must_clear and not cleared:
                    continue
                new_counter = 0 if cleared else counter + 1
                if enforce_constraint and new_counter > MAX_STREAK:
                    continue
                seq.append((slot_idx, r, c, piece, lines))
                rest = remaining[:i] + remaining[i + 1:]
                recurse(rest, new_board, new_counter, seq, total_lines + lines,
                        full_clear_occurred or not new_board.any())
                seq.pop()

    recurse(pieces, board, start_counter, [], 0, False)
    return best


def plan(board, pieces_by_slot, start_counter=None):
    """pieces_by_slot: three bool arrays or None for empty slots.

    Returns a dict with sequence (list of moves), combo_maintained,
    leftover_counter, total_lines and full_clear_occurred. Each move is
    {slot, order, row, col, piece, lines_cleared}.
    """
    if start_counter is None:
        start_counter = load_combo_counter()

    pieces = [(i, trim_piece(p)) for i, p in enumerate(pieces_by_slot)
              if p is not None and np.any(p)]
    if not pieces:
        return {"sequence": [], "combo_maintained": True, "leftover_counter": start_counter,
                "total_lines": 0, "full_clear_occurred": False}

    constrained = _search(board, pieces, start_counter, enforce_constraint=True)
    _, seq, leftover, total_lines, _safety, full_clear_occurred = constrained

    # If keeping the combo means stranding a piece, break the combo instead.
    combo_maintained = len(seq) > 0
    if len(seq) < len(pieces):
        unconstrained = _search(board, pieces, start_counter, enforce_constraint=False)
        _, useq, uleftover, utotal_lines, _usafety, ufull_clear = unconstrained
        if len(useq) > len(seq):
            seq, leftover, total_lines, full_clear_occurred = useq, uleftover, utotal_lines, ufull_clear
            combo_maintained = False
    moves = []
    for order, (slot_idx, r, c, piece, lines) in enumerate(seq, start=1):
        moves.append({
            "slot": slot_idx,
            "order": order,
            "row": r,
            "col": c,
            "piece": piece,
            "lines_cleared": lines,
        })

    if not combo_maintained:
        leftover = 0

    return {
        "sequence": moves,
        "combo_maintained": combo_maintained,
        "leftover_counter": leftover,
        "total_lines": total_lines,
        "full_clear_occurred": full_clear_occurred,
    }
