"""Per-frame pipeline shared by the app and the check CLI: sanity, then detection."""
from __future__ import annotations

from dataclasses import dataclass

from .detector import Crop, Detection, Detector
from .sanity import DEFAULT_MIN_H, DEFAULT_MIN_RATIO, FrameCheck, check_frame


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


def analyse(data: bytes, detector: Detector, crop: Crop,
            min_ratio: float = DEFAULT_MIN_RATIO,
            min_h: float = DEFAULT_MIN_H) -> FrameResult:
    chk = check_frame(data, crop, min_ratio, min_h)
    det = detector.detect(chk.bgr, crop) if chk.bgr is not None else None
    return FrameResult(chk, det)
