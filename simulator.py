"""A simple Block Blast game for demo mode, so the solver can be shown off
without a phone. It deals pieces and applies moves with the same rules as
the real game.
"""
import random
import numpy as np

import solver

BOARD_SIZE = solver.BOARD_SIZE

PALETTE = [
    "#3ec6f0",  # cyan
    "#f5a524",  # orange
    "#a855f7",  # purple
    "#4ade80",  # green
    "#f43f5e",  # red
    "#facc15",  # yellow
    "#60a5fa",  # blue
    "#fb7185",  # pink
]


def _shape(*rows):
    return np.array([[c == "#" for c in row] for row in rows], dtype=bool)


# (shape, weight)
PIECES = [
    (_shape("#"), 3),
    (_shape("##"), 4),
    (_shape("#", "#"), 4),
    (_shape("###"), 4),
    (_shape("#", "#", "#"), 4),
    (_shape("####"), 3),
    (_shape("#", "#", "#", "#"), 3),
    (_shape("#####"), 1),
    (_shape("#", "#", "#", "#", "#"), 1),
    (_shape("##", "##"), 4),
    (_shape("###", "###", "###"), 1),
    (_shape("###", "###"), 2),
    (_shape("##", "##", "##"), 2),
    # 3-cell corners (all four rotations)
    (_shape("#.", "##"), 3),
    (_shape("##", "#."), 3),
    (_shape("##", ".#"), 3),
    (_shape(".#", "##"), 3),
    # 5-cell corners (all four rotations)
    (_shape("#..", "#..", "###"), 2),
    (_shape("###", "#..", "#.."), 2),
    (_shape("###", "..#", "..#"), 2),
    (_shape("..#", "..#", "###"), 2),
    # S / Z
    (_shape(".##", "##."), 1),
    (_shape("##.", ".##"), 1),
    (_shape("#.", "##", ".#"), 1),
    (_shape(".#", "##", "#."), 1),
    # T
    (_shape("###", ".#."), 2),
    (_shape(".#.", "###"), 2),
]

_SHAPES = [p for p, _w in PIECES]
_WEIGHTS = [w for _p, w in PIECES]


class Game:
    """8x8 board plus a three-piece tray. Like the real game, a new tray is
    only dealt once all three pieces are placed."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.board = np.zeros((BOARD_SIZE, BOARD_SIZE), dtype=bool)
        # Color index per cell (-1 when empty), only used by the dashboard.
        self.colors = np.full((BOARD_SIZE, BOARD_SIZE), -1, dtype=int)
        self.tray = [None, None, None]
        self.tray_colors = [0, 0, 0]
        self.deal()

    def deal(self):
        self.tray = [self.rng.choices(_SHAPES, weights=_WEIGHTS, k=1)[0] for _ in range(3)]
        self.tray_colors = [self.rng.randrange(len(PALETTE)) for _ in range(3)]

    @property
    def tray_empty(self):
        return all(p is None for p in self.tray)

    def place(self, slot, row, col):
        """Place a piece, clear full lines and return how many cleared.
        Raises on an illegal move, which would mean a solver bug."""
        piece = self.tray[slot]
        if piece is None:
            raise ValueError(f"slot {slot} is empty")
        ph, pw = piece.shape
        if row < 0 or col < 0 or row + ph > BOARD_SIZE or col + pw > BOARD_SIZE:
            raise ValueError(f"placement ({row},{col}) is off the board")
        if np.any(self.board[row:row + ph, col:col + pw] & piece):
            raise ValueError(f"placement ({row},{col}) overlaps filled cells")

        color = self.tray_colors[slot]
        for r in range(ph):
            for c in range(pw):
                if piece[r, c]:
                    self.board[row + r, col + c] = True
                    self.colors[row + r, col + c] = color

        full_rows = [i for i in range(BOARD_SIZE) if self.board[i, :].all()]
        full_cols = [j for j in range(BOARD_SIZE) if self.board[:, j].all()]
        for i in full_rows:
            self.board[i, :] = False
            self.colors[i, :] = -1
        for j in full_cols:
            self.board[:, j] = False
            self.colors[:, j] = -1

        self.tray[slot] = None
        if self.tray_empty:
            self.deal()
        return len(full_rows) + len(full_cols)

    def board_colors(self):
        """8x8 list of None or "#rrggbb" for the dashboard."""
        return [[PALETTE[self.colors[r, c]] if self.board[r, c] else None
                 for c in range(BOARD_SIZE)] for r in range(BOARD_SIZE)]

    def tray_view(self, orders=None, placed=None):
        """The tray in the format the dashboard draws."""
        out = []
        for i, piece in enumerate(self.tray):
            if piece is None:
                out.append(None)
                continue
            out.append({
                "cells": [[int(v) for v in row] for row in piece],
                "color": PALETTE[self.tray_colors[i]],
                "order": (orders or {}).get(i),
                "placed": bool(placed and i in placed),
            })
        return out
