"""Printable tag16h5 images with the white quiet zone the detector needs."""
from __future__ import annotations

import cv2
import cv2.aruco as aruco
import numpy as np

CELLS = 6            # tag16h5: 4x4 data cells + 1-cell black border each side


def tag_png(tag_id: int, cell_px: int = 100, label: bool = True) -> bytes:
    """PNG of the tag with a one-cell white quiet zone, plus an optional caption."""
    if not 0 <= tag_id <= 29:
        raise ValueError("tag16h5 ids are 0-29")
    cell_px = max(10, min(int(cell_px), 400))
    marker = aruco.generateImageMarker(
        aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_16h5), tag_id, CELLS * cell_px)
    img = cv2.copyMakeBorder(marker, cell_px, cell_px, cell_px, cell_px,
                             cv2.BORDER_CONSTANT, value=255)
    if label:
        caption = np.full((max(40, cell_px // 2), img.shape[1]), 255, np.uint8)
        scale = caption.shape[0] / 60
        text = f"TagSense  tag16h5  id {tag_id}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        cv2.putText(caption, text, ((img.shape[1] - tw) // 2, (caption.shape[0] + th) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, 150, 1, cv2.LINE_AA)
        # dashed cut line between tag (with quiet zone) and caption
        caption[0, ::12] = 180
        img = np.vstack([img, caption])
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()
