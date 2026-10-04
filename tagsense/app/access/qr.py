"""QR decoding on a crop, and blacking codes out of any image that leaves the app.

Two OpenCV decoders are tried in turn: the classic QRCodeDetector, then
QRCodeDetectorAruco. Synthetic tests (SPEC.md, "QR reading") put the limit at
about 3-4 px per QR module after the 2x upscale, so codes must stay small (QR
version 1-2) and be held close to the camera.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import cv2
import numpy as np

from ..detector import Crop, crop_pixels

UPSCALE = 2


@dataclass
class QrRead:
    text: str                       # never published, logged or stored
    quad: np.ndarray                # 4x2, full-frame px
    decoder: str
    ms: float

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.text)


def fingerprint(text: str) -> str:
    """Short, non-reversible id for a payload: lets two scans be compared in
    HA without the payload itself ever leaving the app."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:8]


class QrDecoder:
    """Not thread-safe: one per scanner thread."""

    def __init__(self):
        self._decoders = [("classic", cv2.QRCodeDetector()),
                          ("aruco", cv2.QRCodeDetectorAruco())]

    def decode(self, bgr: np.ndarray, crop: Crop = (0, 0, 1, 1)) -> QrRead | None:
        t0 = time.perf_counter()
        px1, py1, px2, py2 = crop_pixels(bgr.shape, crop)
        gray = cv2.cvtColor(bgr[py1:py2, px1:px2], cv2.COLOR_BGR2GRAY)
        up = cv2.resize(gray, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
        for name, det in self._decoders:
            try:
                text, pts, _ = det.detectAndDecode(up)
            except cv2.error:
                continue
            if text and pts is not None:
                quad = pts.reshape(4, 2) / UPSCALE + np.array([px1, py1], np.float32)
                return QrRead(text, quad.astype(np.float32), name,
                              (time.perf_counter() - t0) * 1000.0)
        return None


def blackout(bgr: np.ndarray, quads: list[np.ndarray], grow: float = 1.25) -> np.ndarray:
    """Copy of `bgr` with each QR quad (grown about its centre, to cover the
    quiet zone and any corner error) filled black, so the code cannot be read
    from a published or stored image."""
    out = bgr.copy()
    for q in quads:
        c = q.mean(axis=0)
        poly = (c + (q - c) * grow).round().astype(np.int32)
        cv2.fillConvexPoly(out, poly, (0, 0, 0))
    return out
