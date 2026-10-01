"""Per-frame pipeline shared by the app and the check CLI: sanity, then detection."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .detector import Crop, Detection, Detector
from .sanity import (DEFAULT_MIN_H, DEFAULT_MIN_RATIO, FrameCheck, check_image,
                     decode_jpeg)


@dataclass
class FrameResult:
    check: FrameCheck
    det: Detection | None       # None only if the frame did not decode

    @property
    def hit(self) -> bool:
        return self.det is not None and self.det.found

    @property
    def valid(self) -> bool:
        # A hit proves the tag region was intact, so it overrides a failed sanity check.
        return self.check.ok or self.hit


def analyse_image(bgr: np.ndarray | None, detector: Detector, crop: Crop,
                  min_ratio: float = DEFAULT_MIN_RATIO,
                  min_h: float = DEFAULT_MIN_H) -> FrameResult:
    """Sanity-check and detect on an already decoded frame (None = undecodable)."""
    if bgr is None:
        return FrameResult(FrameCheck(ok=False, reason="decode_failed"), None)
    chk = check_image(bgr, crop, min_ratio, min_h)
    return FrameResult(chk, detector.detect(bgr, crop))


def analyse(data: bytes, detector: Detector, crop: Crop,
            min_ratio: float = DEFAULT_MIN_RATIO,
            min_h: float = DEFAULT_MIN_H) -> FrameResult:
    return analyse_image(decode_jpeg(data), detector, crop, min_ratio, min_h)
