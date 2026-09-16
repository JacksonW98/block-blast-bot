"""Vision tests against drawn frames in two game skins, checking the exact
board and pieces come back.

  grey: board and background only ~15 brightness levels apart, plus a ring
        of glowing dots between the board and the tray
  navy: the default look, including the green block that's darker than
        the background

    python test_vision.py
"""
import sys

import cv2
import numpy as np

import read_state

W, H = 651, 1360
BOARD_X, BOARD_Y, BOARD_PX = 45, 310, 555
CELL = BOARD_PX / 8.0
UNIT = 31.0
TRAY_TOP = 985

BOARD = """
x_______
x__x_x__
_x_x_x__
_x_x_x__
_xxxxx__
_x_xxxxx
xx_xxx__
xx_xxx__
""".strip().splitlines()

PIECES = [
    (["x_", "x_", "xx"], 105),
    (["x__", "xxx"], 275),
    (["__x", "_x_", "x__"], 455),   # diagonal: cells touch only at corners
]


def as_bool(rows):
    return np.array([[ch == "x" for ch in row] for row in rows], dtype=bool)


def _block(img, x, y, w, h, color, highlight=None):
    cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h)), color, -1)
    if highlight:
        cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h * 0.35)), highlight, -1)


def _draw_common(img, board_bg, grid, cell_color, tray_color, highlight=None):
    cv2.rectangle(img, (BOARD_X, BOARD_Y),
                  (BOARD_X + BOARD_PX, BOARD_Y + BOARD_PX), board_bg, -1)
    for i in range(9):
        p = int(round(i * CELL))
        cv2.line(img, (BOARD_X, BOARD_Y + p), (BOARD_X + BOARD_PX, BOARD_Y + p), grid, 1)
        cv2.line(img, (BOARD_X + p, BOARD_Y), (BOARD_X + p, BOARD_Y + BOARD_PX), grid, 1)
    for r, row in enumerate(BOARD):
        for c, ch in enumerate(row):
            if ch == "x":
                _block(img, BOARD_X + c * CELL + 2, BOARD_Y + r * CELL + 2,
                       CELL - 4, CELL - 4, cell_color(r, c), highlight)
    for idx, (cells, x0) in enumerate(PIECES):
        for r, row in enumerate(cells):
            for c, ch in enumerate(row):
                if ch == "x":
                    _block(img, x0 + c * UNIT, TRAY_TOP + r * UNIT,
                           UNIT - 3, UNIT - 3, tray_color(idx), highlight)


def grey_skin_frame():
    img = np.zeros((H, W, 3), np.uint8)
    cv2.rectangle(img, (8, 8), (W - 9, H - 9), (46, 46, 46), -1)      # bezel ~ board shade
    cv2.rectangle(img, (18, 150), (W - 19, H - 120), (58, 58, 58), -1)  # app background
    cv2.putText(img, "4080", (200, 250), cv2.FONT_HERSHEY_SIMPLEX, 2.6, (255, 255, 255), 8)
    _draw_common(img, (43, 43, 43), (36, 36, 36),
                 lambda r, c: (232, 200, 50), lambda i: (232, 200, 50),
                 highlight=(250, 224, 96))
    for i in range(22):                                   # ring of glowing dots
        t = i / 21.0
        for dx, dy in ((t * BOARD_PX, -14), (t * BOARD_PX, BOARD_PX + 14),
                       (-14, t * BOARD_PX), (BOARD_PX + 14, t * BOARD_PX)):
            cv2.circle(img, (int(BOARD_X + dx), int(BOARD_Y + dy)), 5, (80, 240, 90), -1)
    return img


def navy_skin_frame():
    colors = [(64, 200, 245), (245, 150, 40), (168, 85, 247), (60, 135, 36), (244, 63, 94)]
    img = np.zeros((H, W, 3), np.uint8)
    cv2.rectangle(img, (18, 150), (W - 19, H - 120), (58, 96, 146), -1)
    cv2.putText(img, "4080", (200, 250), cv2.FONT_HERSHEY_SIMPLEX, 2.6, (255, 255, 255), 8)
    _draw_common(img, (30, 42, 72), (24, 34, 60),
                 lambda r, c: colors[(r + c) % len(colors)],
                 # piece 1 gets the dim green that is darker than the background
                 lambda i: (60, 135, 36) if i == 1 else colors[i])
    return img


def check(name, img):
    failures = []
    bbox = read_state.find_board_bbox(img)
    bx, by, bw, bh = bbox
    if abs(bx - BOARD_X) > 8 or abs(by - BOARD_Y) > 8 or abs(bw - BOARD_PX) > 12:
        failures.append(f"board bbox {bbox} != ~({BOARD_X},{BOARD_Y},{BOARD_PX},{BOARD_PX})")

    board = read_state.read_board(img, bbox)
    if not np.array_equal(board, as_bool(BOARD)):
        wrong = int((board != as_bool(BOARD)).sum())
        failures.append(f"board misread ({wrong}/64 cells wrong)")

    pieces = read_state.read_tray_detailed(img, bbox)
    for i, (cells, _x) in enumerate(PIECES):
        want = as_bool(cells)
        got = pieces[i]
        if got is None:
            failures.append(f"piece {i} not detected")
        elif not (got["shape"].shape == want.shape and np.array_equal(got["shape"], want)):
            failures.append(f"piece {i} misread as {got['shape'].shape}, want {want.shape}")

    status = "PASS" if not failures else "FAIL"
    print(f"{status}  {name}")
    for f in failures:
        print(f"        {f}")
    return not failures


def main():
    ok = True
    ok &= check("grey skin (board/background 15 levels apart, glow ring under board)",
                grey_skin_frame())
    ok &= check("navy skin (original palette, dim green piece)", navy_skin_frame())
    print("\nall vision tests passed" if ok else "\nVISION TESTS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
