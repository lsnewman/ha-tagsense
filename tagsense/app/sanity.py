"""Frame sanity check: reject corrupt (smeared) frames so they never count as a miss.

The Tapo smear is vertical streaks: rows are near-identical (vertical
difference ~0) while columns still differ (horizontal difference large).
A dark IR frame is low in both, so the test is the ratio v/h, not v alone.
Scored on the crop region, since that is the part detection relies on.

Calibrated on daytime frames plus darkened/blurred copies (provisional until
real night/IR frames exist): smeared crops scored ratio <= 0.01, good and
darkened crops >= 0.93.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .detector import DEFAULT_CROP, Crop, crop_pixels

DEFAULT_MIN_RATIO = 0.5
DEFAULT_MIN_H = 0.02   # below this the crop is effectively uniform (black/dead feed)


@dataclass
class FrameCheck:
    ok: bool
    reason: str                 # "ok", "decode_failed", "smeared", "flat"
    bgr: np.ndarray | None = None
    v: float = 0.0
    h: float = 0.0
    ratio: float = 0.0

    @property
    def resolution(self) -> str | None:
        if self.bgr is None:
            return None
        h, w = self.bgr.shape[:2]
        return f"{w}x{h}"


def scores(gray: np.ndarray) -> tuple[float, float, float]:
    g = gray.astype(np.int16)
    v = float(np.abs(np.diff(g, axis=0)).mean()) if g.shape[0] > 1 else 0.0
    h = float(np.abs(np.diff(g, axis=1)).mean()) if g.shape[1] > 1 else 0.0
    return v, h, (v / h if h > 0 else 0.0)


def check_frame(data: bytes, crop: Crop = DEFAULT_CROP,
                min_ratio: float = DEFAULT_MIN_RATIO,
                min_h: float = DEFAULT_MIN_H) -> FrameCheck:
    bgr = None
    if data:
        bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None or bgr.size == 0:
        return FrameCheck(ok=False, reason="decode_failed")
    return check_image(bgr, crop, min_ratio, min_h)


def check_image(bgr: np.ndarray, crop: Crop = DEFAULT_CROP,
                min_ratio: float = DEFAULT_MIN_RATIO,
                min_h: float = DEFAULT_MIN_H) -> FrameCheck:
    x1, y1, x2, y2 = crop_pixels(bgr.shape, crop)
    gray = cv2.cvtColor(bgr[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    v, h, ratio = scores(gray)
    if h < min_h:
        reason = "flat"
    elif ratio < min_ratio:
        reason = "smeared"
    else:
        reason = "ok"
    return FrameCheck(ok=reason == "ok", reason=reason, bgr=bgr, v=v, h=h, ratio=ratio)
