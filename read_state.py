"""Reads the 8x8 board and the three tray pieces from a screenshot of the
iPhone Mirroring window.

The board is found by its shape (a big dark square) rather than its color,
since game skins change every color on screen. Cells are then classified by
sampling the middle of each one. Tray pieces are found in the first band of
colored rows below the board and snapped onto a grid to recover their shape.
"""
import numpy as np
import cv2

TRAY_UNIT_RATIO = 31.0 / 69.0  # tray cell size / board cell size, measured
BOARD_SIZE = 8
MAX_PIECE_DIM = 5  # no piece is bigger than 5 cells in either direction


MIN_BOARD_SQUARENESS = 0.85   # min(w,h) / max(w,h)
MIN_BOARD_WIDTH_FRAC = 0.30   # of the captured window width
MAX_BOARD_WIDTH_FRAC = 0.98
DARK_FLOOR = 12               # anything darker is letterboxing, not board


def find_board_bbox(img):
    """Find the board panel. Returns (x, y, w, h).

    We try a range of brightness cutoffs and keep the dark region that looks
    most like a board (large and square). One of the cutoffs will separate
    the board from the background whatever colors the skin uses, and the
    squareness check rejects the whole-screen blob you get when the board
    and background are close in brightness.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    value = hsv[:, :, 2]
    img_w = img.shape[1]
    min_w = img_w * MIN_BOARD_WIDTH_FRAC
    max_w = img_w * MAX_BOARD_WIDTH_FRAC
    kernel = np.ones((15, 15), np.uint8)

    best = None  # (score, bbox)
    for cutoff in range(30, 165, 5):
        mask = ((value > DARK_FLOOR) & (value < cutoff)).astype(np.uint8) * 255
        if not mask.any():
            continue
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        n, _labels, stats, _cent = cv2.connectedComponentsWithStats(closed, connectivity=8)
        for i in range(1, n):
            x, y, w, h, area = (int(v) for v in stats[i])
            if w < min_w or w > max_w:
                continue
            squareness = min(w, h) / max(w, h)
            if squareness < MIN_BOARD_SQUARENESS:
                continue
            # Don't require the region to be solid. Filled cells are bright,
            # so a half-full board is only about half dark, but the empty
            # cells stay connected through the grid lines.
            #
            # Higher cutoffs pick up a bit of the surrounding background and
            # make the box too big, so weight heavily towards square.
            score = area * squareness ** 8
            if best is None or score > best[0]:
                best = (score, (x, y, w, h))

    if best is not None:
        return best[1]

    # Nothing square turned up, so fall back to the largest dark blob.
    mask = ((value > 25) & (value < 85)).astype(np.uint8) * 255
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(closed, connectivity=8)
    if n <= 1:
        raise RuntimeError("Could not find board region (no dark panel detected)")
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, _area = stats[idx]
    return int(x), int(y), int(w), int(h)


BG_HUE = 109           # hue of the default blue backgrounds (OpenCV hue, 0-179)
BG_HUE_TOLERANCE = 30


def vivid_mask(img):
    """True where a pixel looks like part of a block rather than background.

    Most blocks are simply bright, but the green block (V~135) is darker than
    the default tray background (V~146), so brightness alone can't catch it.
    Pixels that are clearly off the background hue and at least dimly lit
    count too.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV).astype(int)
    hue, val = hsv[:, :, 0], hsv[:, :, 2]
    hue_dist = np.minimum(np.abs(hue - BG_HUE), 180 - np.abs(hue - BG_HUE))
    return (val > 185) | ((hue_dist > BG_HUE_TOLERANCE) & (val > 100))


def read_board(img, bbox):
    x, y, w, h = bbox
    vivid = vivid_mask(img)
    cell_w = w / BOARD_SIZE
    cell_h = h / BOARD_SIZE

    board = np.zeros((BOARD_SIZE, BOARD_SIZE), dtype=bool)
    for row in range(BOARD_SIZE):
        for col in range(BOARD_SIZE):
            cx0 = int(x + col * cell_w + cell_w * 0.3)
            cx1 = int(x + col * cell_w + cell_w * 0.7)
            cy0 = int(y + row * cell_h + cell_h * 0.3)
            cy1 = int(y + row * cell_h + cell_h * 0.7)
            patch = vivid[cy0:cy1, cx0:cx1]
            board[row, col] = patch.mean() > 0.5
    return board


def _row_bands(row_has_vivid, gap_limit):
    """Runs of vivid rows as (start, end) pairs. Gaps shorter than gap_limit
    don't end a run, since pieces have small gaps between their cells.
    """
    bands = []
    n = len(row_has_vivid)
    i = 0
    while i < n:
        if not row_has_vivid[i]:
            i += 1
            continue
        start = i
        end = i
        while i < n:
            if row_has_vivid[i]:
                end = i
                i += 1
            else:
                gap_start = i
                while i < n and not row_has_vivid[i] and i - gap_start < gap_limit:
                    i += 1
                if i - gap_start >= gap_limit:
                    break
        bands.append((start, end + 1))
    return bands


TRAY_DARK = 40              # darker than this is letterboxing, never a piece
TRAY_BG_DISTANCE = 10       # Lab distance from the background that counts as a piece


def tray_mask(img, board_bbox):
    """True where a pixel below the board looks like part of a tray piece.

    The background is taken as the median color below the board, since
    pieces only cover a small part of that area, and anything far enough
    from it counts. That works whatever color the skin uses, including red
    pieces on a red background. Regions too big to be a piece (like a phone
    bezel in a slightly different shade) are then removed.
    """
    bx, by, bw, bh = board_bbox
    board_bottom = by + bh
    mask = np.zeros(img.shape[:2], dtype=bool)
    region = img[board_bottom:]
    if region.size == 0:
        return mask
    lab = cv2.cvtColor(np.ascontiguousarray(region), cv2.COLOR_RGB2LAB).astype(np.float32)
    lit = region.max(axis=2) > TRAY_DARK
    sample = lab[::4, ::4][lit[::4, ::4]]
    if sample.size == 0:
        return mask
    background = np.median(sample, axis=0)
    fg = lit & (np.linalg.norm(lab - background, axis=2) > TRAY_BG_DISTANCE)

    unit = (bw / BOARD_SIZE + bh / BOARD_SIZE) / 2 * TRAY_UNIT_RATIO
    max_size = (MAX_PIECE_DIM + 1) * unit
    n, labels, stats, _cent = cv2.connectedComponentsWithStats(fg.astype(np.uint8), connectivity=8)
    too_big = (stats[:, cv2.CC_STAT_WIDTH] > max_size) | (stats[:, cv2.CC_STAT_HEIGHT] > max_size)
    too_big[0] = True  # label 0 is the background
    mask[board_bottom:] = ~too_big[labels]
    return mask


def find_tray_band(mask, board_bottom, min_height=0):
    """Rows (y0, y1) containing the tray pieces: the first band of tray_mask
    pixels below the board that's at least min_height tall. The height check
    skips thin decoration under the board, like the glowing dots on the grey
    skin.
    """
    row_has_vivid = mask[board_bottom:, :].sum(axis=1) > 3

    for start, end in _row_bands(row_has_vivid, gap_limit=40):
        if end - start >= min_height:
            return board_bottom + start, board_bottom + end
    return None


def read_tray_detailed(img, board_bbox):
    """Read the three tray slots. Each is None or
    {"shape": bool array, "bbox": (x0, y0, x1, y1)}.
    """
    bx, by, bw, bh = board_bbox
    board_bottom = by + bh
    board_cell_size = (bw / BOARD_SIZE + bh / BOARD_SIZE) / 2
    unit = board_cell_size * TRAY_UNIT_RATIO
    vivid = tray_mask(img, board_bbox)
    band = find_tray_band(vivid, board_bottom, min_height=unit * 0.8)
    if band is None:
        return [None, None, None]
    y0, y1 = band

    vivid_u8 = vivid.astype(np.uint8) * 255
    band_mask = np.zeros_like(vivid_u8)
    band_mask[y0:y1, :] = vivid_u8[y0:y1, :]

    n, labels, stats, centroids = cv2.connectedComponentsWithStats(band_mask, connectivity=8)
    blobs = [stats[i] for i in range(1, n) if stats[i][cv2.CC_STAT_AREA] > 150]
    if not blobs:
        return [None, None, None]

    # Assign blobs to slots by position (thirds of the width). Grouping by
    # distance between blobs used to merge two pieces that sat close together.
    slot_width = img.shape[1] / 3
    groups = [[], [], []]
    for s in blobs:
        blob_cx = s[0] + s[2] / 2.0
        slot_idx = min(2, max(0, int(blob_cx // slot_width)))
        groups[slot_idx].append(s)

    pieces = [None, None, None]
    for slot_idx, group in enumerate(groups):
        if not group:
            continue
        xs = [s[0] for s in group] + [s[0] + s[2] for s in group]
        ys = [s[1] for s in group] + [s[1] + s[3] for s in group]
        gx0, gx1 = min(xs), max(xs)
        gy0, gy1 = min(ys), max(ys)

        cols = max(1, round((gx1 - gx0) / unit))
        rows = max(1, round((gy1 - gy0) / unit))
        if cols > MAX_PIECE_DIM or rows > MAX_PIECE_DIM:
            # Too big to be one piece, probably pieces caught mid-animation.
            # Skip it and let the caller retry once things settle.
            continue
        cell_w = (gx1 - gx0) / cols
        cell_h = (gy1 - gy0) / rows
        # Sample each grid cell directly. A piece's cells can merge into one
        # blob, so blob centroids aren't reliable.
        shape = np.zeros((rows, cols), dtype=bool)
        for row in range(rows):
            for col in range(cols):
                cx0 = int(gx0 + col * cell_w + cell_w * 0.3)
                cx1 = int(gx0 + col * cell_w + cell_w * 0.7)
                cy0 = int(gy0 + row * cell_h + cell_h * 0.3)
                cy1 = int(gy0 + row * cell_h + cell_h * 0.7)
                patch = vivid[cy0:cy1, cx0:cx1]
                shape[row, col] = patch.size > 0 and patch.mean() > 0.5
        if not shape.any():
            # Happens when a piece is caught mid-animation. An empty shape
            # would crash the solver, so report the slot as empty instead.
            continue
        pieces[slot_idx] = {"shape": shape, "bbox": (gx0, gy0, gx1, gy1)}
    return pieces


FALLBACK_BLOCK_COLOR = "#3ec6f0"
BLOCK_MIN_BRIGHTNESS = 100  # darker than this is background, not a block


def _hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*(int(max(0, min(255, v))) for v in rgb))


def read_board_colors(img, bbox, board=None):
    """Color of each filled cell as "#rrggbb" (None for empty), for the
    dashboard. Only cosmetic.
    """
    x, y, w, h = bbox
    if board is None:
        board = read_board(img, bbox)
    cell_w, cell_h = w / BOARD_SIZE, h / BOARD_SIZE
    out = []
    for row in range(BOARD_SIZE):
        line = []
        for col in range(BOARD_SIZE):
            if not board[row, col]:
                line.append(None)
                continue
            cx0 = int(x + col * cell_w + cell_w * 0.3)
            cx1 = int(x + col * cell_w + cell_w * 0.7)
            cy0 = int(y + row * cell_h + cell_h * 0.3)
            cy1 = int(y + row * cell_h + cell_h * 0.7)
            patch = img[cy0:cy1, cx0:cx1]
            if patch.size == 0:
                line.append(FALLBACK_BLOCK_COLOR)
                continue
            mean = patch.reshape(-1, 3).mean(axis=0)
            # `board` can say filled while this frame still shows background
            # (e.g. mid-animation), so don't paint a block the background color.
            line.append(_hex(mean) if mean.max() > BLOCK_MIN_BRIGHTNESS
                        else FALLBACK_BLOCK_COLOR)
        out.append(line)
    return out


def piece_color(img, slot_info):
    """Average color of a tray piece, ignoring the background between cells."""
    x0, y0, x1, y1 = (int(v) for v in slot_info["bbox"])
    crop = img[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return FALLBACK_BLOCK_COLOR
    mask = vivid_mask(crop)
    pixels = crop[mask]
    if pixels.size == 0:
        return FALLBACK_BLOCK_COLOR
    return _hex(pixels.mean(axis=0))


def board_to_ascii(board):
    lines = []
    for row in board:
        lines.append("".join("#" if v else "." for v in row))
    return "\n".join(lines)


def piece_to_ascii(shape):
    if shape is None:
        return "(empty slot)"
    return "\n".join("".join("#" if v else "." for v in row) for row in shape)
