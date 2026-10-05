"""QR decoding on a crop, and blacking codes out of any image that leaves the app.

The decoder is ZXing (zxing-cpp). OpenCV's QR detectors were tried first and
rejected: on a real camera frame of a phone screen, the bright screen bled
into the dark modules, the finder squares lost their 1:1:3:1:1 proportions,
and OpenCV did not even detect an easily readable code, while ZXing read it
in about 1 ms (SPEC.md, "QR reading"). The OpenCV decoders are kept only so
app.sweep can compare them.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import cv2
import numpy as np
import zxingcpp

from ..detector import Crop, crop_pixels

UPSCALE = 2                      # OpenCV decoders only
ZXING_FORMATS = [zxingcpp.BarcodeFormat.QRCode, zxingcpp.BarcodeFormat.MicroQRCode]


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
    """Not thread-safe: one per scanner thread. `decoders` picks the backends,
    in order ("zxing", "classic", "aruco"); the app uses ZXing alone."""

    def __init__(self, decoders: tuple[str, ...] = ("zxing",)):
        self.names = decoders
        self._cv = {"classic": cv2.QRCodeDetector, "aruco": cv2.QRCodeDetectorAruco}
        self._cv = {n: self._cv[n]() for n in decoders if n in self._cv}

    def decode(self, bgr: np.ndarray, crop: Crop = (0, 0, 1, 1)) -> QrRead | None:
        reads = self.decode_all(bgr, crop, first_only=True)
        return reads[0] if reads else None

    def decode_all(self, bgr: np.ndarray, crop: Crop = (0, 0, 1, 1),
                   first_only: bool = False) -> list[QrRead]:
        """Every code found in the crop (a phone can show several). The OpenCV
        back ends find at most one."""
        t0 = time.perf_counter()
        px1, py1, px2, py2 = crop_pixels(bgr.shape, crop)
        gray = cv2.cvtColor(bgr[py1:py2, px1:px2], cv2.COLOR_BGR2GRAY)
        offset = np.array([px1, py1], np.float32)
        up = None
        for name in self.names:
            if name == "zxing":
                found = []
                for r in zxingcpp.read_barcodes(gray, formats=ZXING_FORMATS):
                    if r.valid and r.text:
                        p = r.position
                        quad = np.float32([[p.top_left.x, p.top_left.y], [p.top_right.x, p.top_right.y],
                                           [p.bottom_right.x, p.bottom_right.y],
                                           [p.bottom_left.x, p.bottom_left.y]]) + offset
                        found.append(QrRead(r.text, quad, name, (time.perf_counter() - t0) * 1000.0))
                if found:
                    return found[:1] if first_only else found
                continue
            if up is None:
                up = cv2.resize(gray, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
            try:
                text, pts, _ = self._cv[name].detectAndDecode(up)
            except cv2.error:
                continue
            if text and pts is not None:
                quad = pts.reshape(4, 2) / UPSCALE + offset
                return [QrRead(text, quad.astype(np.float32), name,
                               (time.perf_counter() - t0) * 1000.0)]
        return []


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
