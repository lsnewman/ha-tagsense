"""Tag rotation on the object's surface, relative to a user-defined 0.

The angle in the image is wrong under perspective (a 90 deg turn measured
117-119 deg on a real bin lid), so the current corners are mapped through the
inverse of the reference homography onto the lid plane first, and the turn is
measured there against the unit square. Exact for any move within the same
plane; verified within 1 deg at 0-315 deg on real frames.

Angles increase clockwise as seen by the camera. The aruco corner order is
fixed to the tag pattern, so edge 0-1 is always the tag's own "top".
"""
from __future__ import annotations

import math

import cv2
import numpy as np

SQ = np.float32([[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]])
MIN_STEPS, MAX_STEPS, DEFAULT_STEPS = 1, 36, 4
MARGIN_DEG = 5.0      # go past a step boundary by this much before moving


def lid_angle(ref_corners, corners) -> float | None:
    """Rotation of `corners` against `ref_corners` (4x2, same units), 0-360."""
    try:
        hinv = cv2.getPerspectiveTransform(np.float32(ref_corners), SQ)
        p = cv2.perspectiveTransform(np.float32(corners).reshape(1, 4, 2), hinv)[0]
    except cv2.error:
        return None
    if not np.isfinite(p).all():
        return None
    p -= p.mean(axis=0)
    if np.abs(p).max() < 1e-6:          # degenerate quad: no direction to measure
        return None
    a = [math.atan2(SQ[i][0] * p[i][1] - SQ[i][1] * p[i][0], float(SQ[i] @ p[i])) for i in range(4)]
    return circular_mean([math.degrees(v) for v in a])


def circular_mean(angles_deg) -> float | None:
    """Mean direction: 359 and 1 average to 0."""
    if not angles_deg:
        return None
    s = sum(math.sin(math.radians(a)) for a in angles_deg)
    c = sum(math.cos(math.radians(a)) for a in angles_deg)
    return math.degrees(math.atan2(s, c)) % 360


def circ_dist(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def clamp_steps(v) -> int:
    return min(MAX_STEPS, max(MIN_STEPS, int(round(float(v)))))


def snap(angle: float, steps: int, prev: float | None = None) -> float:
    """Nearest of `steps` evenly spaced values (0, 360/steps, ...), sticking to
    `prev` until the angle is MARGIN_DEG past the halfway point, so a turn
    near a boundary does not flicker."""
    step = 360 / steps
    nearest = round(angle / step) * step % 360
    if prev is not None and prev != nearest and abs(prev / step - round(prev / step)) < 1e-6:
        margin = min(MARGIN_DEG, step / 4)
        if circ_dist(angle, prev) <= step / 2 + margin:
            return prev
    return nearest


def fmt_deg(v: float) -> int | float:
    """Whole degrees as int (90 rather than 90.0), else one decimal."""
    v = round(v, 1) % 360
    return int(v) if v == int(v) else v
