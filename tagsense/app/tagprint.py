"""Printable tag16h5 tags: PNG for paper, SVG (in mm) for cutting or 3D printing.

The detector needs a light area around the tag's black border (the "quiet
zone"). It is added by default; leave it out only if the tag will sit on a
light surface, e.g. a white bin lid.
"""
from __future__ import annotations

import cv2
import cv2.aruco as aruco
import numpy as np

CELLS = 6            # tag16h5: 4x4 data cells + 1-cell black border each side


def tag_grid(tag_id: int) -> np.ndarray:
    """6x6 array, True = black cell."""
    if not 0 <= tag_id <= 29:
        raise ValueError("tag16h5 ids are 0-29")
    img = aruco.generateImageMarker(
        aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_16h5), tag_id, CELLS)
    return img == 0


def tag_png(tag_id: int, cell_px: int = 100, label: bool = True, quiet: bool = True) -> bytes:
    """PNG of the tag, with an optional one-cell white quiet zone and caption."""
    cell_px = max(10, min(int(cell_px), 400))
    grid = tag_grid(tag_id)
    img = np.where(np.kron(grid, np.ones((cell_px, cell_px), bool)), 0, 255).astype(np.uint8)
    if quiet:
        img = cv2.copyMakeBorder(img, cell_px, cell_px, cell_px, cell_px,
                                 cv2.BORDER_CONSTANT, value=255)
    if label:
        caption = np.full((max(40, cell_px // 2), img.shape[1]), 255, np.uint8)
        scale = caption.shape[0] / 60
        text = f"TagSense  tag16h5  id {tag_id}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        cv2.putText(caption, text, ((img.shape[1] - tw) // 2, (caption.shape[0] + th) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, 150, 1, cv2.LINE_AA)
        caption[0, ::12] = 180       # dashed cut line under the tag
        img = np.vstack([img, caption])
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def black_rects(grid: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Merge black cells into disjoint rectangles (x, y, w, h) in cell units."""
    rects, open_runs = [], {}          # (x, w) -> index in rects, for runs continuing down
    for y, row in enumerate(grid):
        runs, x = [], 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]:
                    x += 1
                runs.append((start, x - start))
            else:
                x += 1
        next_open = {}
        for run in runs:
            if run in open_runs:
                i = open_runs[run]
                rx, ry, rw, rh = rects[i]
                rects[i] = (rx, ry, rw, rh + 1)
            else:
                i = len(rects)
                rects.append((run[0], y, run[1], 1))
            next_open[run] = i
        open_runs = next_open
    return rects


def tag_svg(tag_id: int, size_mm: float = 100.0, quiet: bool = True) -> bytes:
    """SVG in millimetres. `size_mm` is the black square's edge length.

    Two non-overlapping shapes: #black (the tag) and #white (the inner white
    cells, plus the quiet zone if enabled), so a slicer can give each its own
    filament, or a cutter can use just the black layer.
    """
    size_mm = max(5.0, min(float(size_mm), 1000.0))
    cell = size_mm / CELLS
    margin = 1 if quiet else 0
    total = (CELLS + 2 * margin) * cell
    rects = black_rects(tag_grid(tag_id))

    def r(v: float) -> str:
        return f"{v:.3f}".rstrip("0").rstrip(".")

    def rect_path(x, y, w, h) -> str:
        return f"M{r(x)} {r(y)}h{r(w)}v{r(h)}h{r(-w)}z"

    black = "".join(rect_path((x + margin) * cell, (y + margin) * cell, w * cell, h * cell)
                    for x, y, w, h in rects)
    white = rect_path(0, 0, total, total) + black          # evenodd: black cut out
    t = r(total)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{t}mm" height="{t}mm" '
            f'viewBox="0 0 {t} {t}">\n'
            f'  <title>TagSense tag16h5 id {tag_id}, {r(size_mm)} mm'
            f'{" with quiet zone" if quiet else ""}</title>\n'
            f'  <path id="white" fill="#ffffff" fill-rule="evenodd" d="{white}"/>\n'
            f'  <path id="black" fill="#000000" d="{black}"/>\n'
            f'</svg>\n').encode()
