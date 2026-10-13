"""Learned reference geometry: where the tag usually is, and how big.

Warns (never rejects) when a hit is far from what is normal for this install,
which is what a phantom decode of the target ID tends to look like. Learned
from accepted hits as an exponential moving average, so it needs no setup and
slowly follows a permanent move of the tagged object.

Home is a fixed copy of that average, taken once MIN_HITS are learned (or set
by the user). It does not follow moves, so the distance from home says how far
the object is from where it belongs, for as long as it stays away.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass

from .detector import Detection

ALPHA = 0.1                 # weight of each new hit (~20 hits to mostly adapt)
MIN_HITS = 10               # learn this many before warning
SIZE_TOLERANCE = 0.40       # +/- fraction of the learned size
CENTRE_TOLERANCE = 0.08     # distance in normalised frame coords


@dataclass
class Reference:
    hits: int = 0
    size: float = 0.0       # tag size as a fraction of frame height
    cx: float = 0.0         # normalised centre
    cy: float = 0.0
    orient: list | None = None  # normalised corners that define 0 deg (None = not set yet)
    updated: float = 0.0        # unix time of the last change (export/import: newer wins)
    home_size: float | None = None  # home: fixed, same units as size / cx / cy
    home_cx: float | None = None
    home_cy: float | None = None

    def __post_init__(self):
        self._ensure_home()     # learned before home existed (0.5.1 and older)

    @property
    def ready(self) -> bool:
        return self.hits >= MIN_HITS

    @property
    def home(self) -> list[float] | None:
        return None if self.home_size is None else [self.home_cx, self.home_cy]

    def _ensure_home(self) -> bool:
        """Take home from the learned average once it is ready. True if it was set now."""
        if self.home_size is not None or not self.ready:
            return False
        self.home_size, self.home_cx, self.home_cy = self.size, self.cx, self.cy
        return True

    def set_home(self, size: float, cx: float, cy: float):
        self.home_size, self.home_cx, self.home_cy = size, cx, cy
        self.updated = time.time()

    def distance(self, det: Detection) -> float | None:
        """How far a hit is from home, in tag widths (home's tag), or None."""
        if not det.found or not self.home_size:
            return None
        _, cx, cy = self._measure(det)
        w, h = det.frame_wh
        dx = (cx - self.home_cx) * w / h            # both in frame heights
        dy = cy - self.home_cy
        width = self.home_size / math.sqrt(2)       # size is the diagonal
        return round(math.hypot(dx, dy) / width, 1)

    @staticmethod
    def _measure(det: Detection) -> tuple[float, float, float]:
        cx, cy = det.centre_norm
        return det.size_px / det.frame_wh[1], cx, cy

    def warning(self, det: Detection) -> str | None:
        """How far a hit is from the learned reference, or None."""
        if not det.found or not self.ready:
            return None
        size, cx, cy = self._measure(det)
        msgs = []
        if abs(size - self.size) > SIZE_TOLERANCE * self.size:
            msgs.append(f"tag size {size * 100:.1f}% of frame height "
                        f"(usual {self.size * 100:.1f}%)")
        dist = math.hypot(cx - self.cx, cy - self.cy)
        if dist > CENTRE_TOLERANCE:
            msgs.append(f"tag centre ({cx:.2f},{cy:.2f}) is {dist:.2f} from usual "
                        f"({self.cx:.2f},{self.cy:.2f})")
        return "; ".join(msgs) or None

    def learn(self, det: Detection):
        if not det.found:
            return
        size, cx, cy = self._measure(det)
        if self.hits == 0:
            self.size, self.cx, self.cy = size, cx, cy
        else:
            a = ALPHA
            self.size += a * (size - self.size)
            self.cx += a * (cx - self.cx)
            self.cy += a * (cy - self.cy)
        self.hits += 1
        self._ensure_home()
        self.updated = time.time()

    def set_orient(self, corners_norm):
        self.orient = [[round(float(x), 5), round(float(y), 5)] for x, y in corners_norm]
        self.updated = time.time()

    def as_attrs(self) -> dict:
        return {"learned_hits": self.hits, "usual_size_pct": round(self.size * 100, 2),
                "usual_centre": [round(self.cx, 3), round(self.cy, 3)],
                "warnings_active": self.ready,
                "home": [round(v, 3) for v in self.home] if self.home else None}

    def save(self, path: str):
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(asdict(self), f)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "Reference":
        try:
            with open(path) as f:
                raw = json.load(f)
            return cls(**{k: raw[k] for k in cls.__dataclass_fields__ if k in raw})
        except (OSError, ValueError, TypeError):
            return cls()
