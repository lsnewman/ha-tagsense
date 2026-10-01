import re

import cv2
import numpy as np
import pytest

from app.detector import Detector
from app.tagprint import black_rects, tag_grid, tag_png, tag_svg


def decodes(img_gray, tag):
    bgr = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)
    return Detector(tag).detect(bgr, (0, 0, 1, 1)).found


@pytest.mark.parametrize("tag", [0, 5, 29])
def test_printed_png_decodes(tag):
    img = cv2.imdecode(np.frombuffer(tag_png(tag, cell_px=30), np.uint8), cv2.IMREAD_GRAYSCALE)
    assert decodes(img, tag)


def test_png_without_quiet_zone_decodes_on_light_background():
    img = cv2.imdecode(np.frombuffer(tag_png(5, cell_px=30, label=False, quiet=False), np.uint8),
                       cv2.IMREAD_GRAYSCALE)
    assert img.shape == (180, 180)
    on_lid = cv2.copyMakeBorder(img, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=225)
    assert decodes(on_lid, 5)


@pytest.mark.parametrize("tag", range(30))
def test_black_rects_cover_exactly_the_black_cells(tag):
    grid = tag_grid(tag)
    cover = np.zeros_like(grid, int)
    for x, y, w, h in black_rects(grid):
        cover[y:y + h, x:x + w] += 1
    assert (cover == grid.astype(int)).all()          # exact, no overlaps


def rasterise_svg(svg: str, px_per_mm: float) -> np.ndarray:
    """Fill the #black path's rectangles (all paths are axis-aligned 'M x y h w v h h -w z')."""
    size = float(re.search(r'viewBox="0 0 ([\d.]+)', svg).group(1))
    n = int(round(size * px_per_mm))
    img = np.full((n, n), 255, np.uint8)
    black = re.search(r'id="black"[^>]*d="([^"]+)"', svg).group(1)
    for x, y, w, h in re.findall(r"M([\d.]+) ([\d.]+)h([\d.]+)v([\d.]+)", black):
        x0, y0 = int(round(float(x) * px_per_mm)), int(round(float(y) * px_per_mm))
        img[y0:y0 + int(round(float(h) * px_per_mm)), x0:x0 + int(round(float(w) * px_per_mm))] = 0
    return img


@pytest.mark.parametrize("quiet,total", [(True, 80), (False, 60)])
def test_svg_size_and_decode(quiet, total):
    svg = tag_svg(5, size_mm=60, quiet=quiet).decode()
    assert f'width="{total}mm"' in svg and f'viewBox="0 0 {total} {total}"' in svg
    assert 'id="white"' in svg and 'fill-rule="evenodd"' in svg
    img = rasterise_svg(svg, px_per_mm=3)
    if not quiet:
        img = cv2.copyMakeBorder(img, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
    assert decodes(img, 5)


def test_bad_tag_id():
    with pytest.raises(ValueError):
        tag_png(30)
    with pytest.raises(ValueError):
        tag_svg(-1)
