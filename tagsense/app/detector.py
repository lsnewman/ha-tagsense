"""AprilTag (tag16h5) detection on a normalised crop of a camera frame.

Pure OpenCV: no HA or MQTT imports. Parameters are the ones that won the
tuning sweep (see the handover spec, section 5). Do not add the
adaptiveThreshWinSize*, minMarkerPerimeterRate or polygonalApproxAccuracyRate
tweaks: they produced a phantom ID in full-frame tests.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import cv2.aruco as aruco
import numpy as np

DEFAULT_CROP = (0.65, 0.45, 1.0, 1.0)
UPSCALE = 2

# Shape gate, part 1: longest/shortest edge. A square tag seen at an angle
# stays fairly square (measured 1.45-1.52 on a flat bin lid); phantom decodes
# in gravel measured 2.2-6. Scale-invariant. (Part 2, the size check against
# the learned usual size, lives in the tracker since it needs the reference.)
DEFAULT_MAX_ASPECT = 2.0

Crop = tuple[float, float, float, float]


def build_params() -> aruco.DetectorParameters:
    p = aruco.DetectorParameters()
    p.perspectiveRemovePixelPerCell = 8
    p.perspectiveRemoveIgnoredMarginPerCell = 0.15
    p.maxErroneousBitsInBorderRate = 0.5
    p.cornerRefinementMethod = aruco.CORNER_REFINE_APRILTAG
    p.aprilTagQuadDecimate = 1.0
    p.cornerRefinementMaxIterations = 30
    return p


def validate_crop(crop) -> Crop:
    """Clamp to [0, 1]; fall back to DEFAULT_CROP if empty or malformed."""
    try:
        x1, y1, x2, y2 = (min(1.0, max(0.0, float(v))) for v in crop)
    except (TypeError, ValueError):
        return DEFAULT_CROP
    if x2 <= x1 or y2 <= y1:
        return DEFAULT_CROP
    return (x1, y1, x2, y2)


def crop_pixels(shape, crop: Crop) -> tuple[int, int, int, int]:
    h, w = shape[:2]
    x1, y1, x2, y2 = validate_crop(crop)
    px1, py1 = int(round(x1 * w)), int(round(y1 * h))
    px2, py2 = int(round(x2 * w)), int(round(y2 * h))
    return px1, py1, max(px2, px1 + 1), max(py2, py1 + 1)


def quad_size(corners: np.ndarray) -> float:
    """Longer diagonal of a tag quad (the tag is heavily foreshortened)."""
    c = corners
    return float(max(np.linalg.norm(c[0] - c[2]), np.linalg.norm(c[1] - c[3])))


def quad_edges(corners: np.ndarray) -> np.ndarray:
    return np.linalg.norm(np.roll(corners, -1, axis=0) - corners, axis=1)


def quad_aspect(corners: np.ndarray) -> float:
    e = quad_edges(corners)
    return float(e.max() / max(e.min(), 1e-6))


@dataclass
class Phantom:
    """A decode that does not count: another tag16h5 ID (phantom candidate),
    or the target ID rejected by the shape gate."""
    id: int
    corners: np.ndarray              # 4x2, full-frame px
    frame_wh: tuple[int, int]
    rejected_target: bool = False
    reason: str = ""                 # why a target decode was rejected

    @property
    def centre_norm(self) -> tuple[float, float]:
        cx, cy = self.corners.mean(axis=0)
        return float(cx) / self.frame_wh[0], float(cy) / self.frame_wh[1]

    @property
    def size_px(self) -> float:
        return quad_size(self.corners)

    @property
    def aspect(self) -> float:
        return quad_aspect(self.corners)

    @property
    def label(self) -> str:
        if self.rejected_target:
            return f"rejected id {self.id}" + (f" ({self.reason})" if self.reason else "")
        return f"phantom id {self.id}"

    def describe(self) -> str:
        cx, cy = self.centre_norm
        e = quad_edges(self.corners)
        area = cv2.contourArea(self.corners.astype(np.float32))
        return (f"{self.label} at ({cx:.3f},{cy:.3f}) size {self.size_px:.0f}px "
                f"edges {e.max():.0f}x{e.min():.0f}px aspect {self.aspect:.1f} area {area:.0f}px2")


@dataclass
class Detection:
    found: bool
    tag_id: int
    frame_wh: tuple[int, int]
    crop_px: tuple[int, int, int, int]
    ms: float
    mean: float                      # grey mean of the crop
    std: float                       # grey std of the crop
    corners: np.ndarray | None = None  # 4x2, full-frame px
    others: list[Phantom] = field(default_factory=list)

    @property
    def other_ids(self) -> list[int]:
        return [o.id for o in self.others]

    @property
    def centre(self) -> tuple[float, float] | None:
        if self.corners is None:
            return None
        c = self.corners.mean(axis=0)
        return float(c[0]), float(c[1])

    @property
    def centre_norm(self) -> tuple[float, float] | None:
        c = self.centre
        if c is None:
            return None
        w, h = self.frame_wh
        return c[0] / w, c[1] / h

    @property
    def size_px(self) -> float | None:
        """Longer diagonal of the tag quad, in full-frame px."""
        if self.corners is None:
            return None
        return quad_size(self.corners)

    @property
    def area_px(self) -> float | None:
        if self.corners is None:
            return None
        return float(cv2.contourArea(self.corners.astype(np.float32)))


class Detector:
    def __init__(self, tag_id: int = 5, max_aspect: float = DEFAULT_MAX_ASPECT):
        self.tag_id = int(tag_id)
        self.max_aspect = float(max_aspect)   # 0 disables the shape gate
        self._det = aruco.ArucoDetector(
            aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_16h5), build_params())

    def detect(self, bgr: np.ndarray, crop: Crop = DEFAULT_CROP) -> Detection:
        t0 = time.perf_counter()
        px1, py1, px2, py2 = crop_pixels(bgr.shape, crop)
        gray = cv2.cvtColor(bgr[py1:py2, px1:px2], cv2.COLOR_BGR2GRAY)
        mean, std = (float(v[0][0]) for v in cv2.meanStdDev(gray))
        up = cv2.resize(gray, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
        corners, ids, _ = self._det.detectMarkers(up)
        ms = (time.perf_counter() - t0) * 1000.0

        h, w = bgr.shape[:2]
        det = Detection(found=False, tag_id=self.tag_id, frame_wh=(w, h), crop_px=(px1, py1, px2, py2),
                        ms=ms, mean=mean, std=std)
        if ids is None:
            return det
        offset = np.array([px1, py1], dtype=np.float32)
        for c, i in zip(corners, ids.flatten()):
            full = c.reshape(4, 2) / UPSCALE + offset
            if int(i) != self.tag_id:
                det.others.append(Phantom(int(i), full, (w, h)))
            elif self.max_aspect > 0 and quad_aspect(full) > self.max_aspect:
                det.others.append(Phantom(int(i), full, (w, h), rejected_target=True,
                                          reason=f"aspect {quad_aspect(full):.1f} > {self.max_aspect:g}"))
            elif not det.found:
                det.found = True
                det.corners = full
        return det


PHANTOM_BGR = (0, 140, 255)   # orange


def annotate(bgr: np.ndarray, det: Detection, phantoms: list[Phantom] | None = None,
             quality: int = 85) -> bytes:
    """JPEG of the crop: target tag in green (with corner-0 dot), phantom
    decodes in orange. `phantoms` defaults to det.others; pass the whole
    burst's phantoms to show them all on one frame."""
    px1, py1, px2, py2 = det.crop_px
    img = bgr[py1:py2, px1:px2].copy()
    for ph in det.others if phantoms is None else phantoms:
        pts = (ph.corners - np.array([px1, py1])).round().astype(np.int32)
        cv2.polylines(img, [pts], True, PHANTOM_BGR, 2)
        x, y = pts[:, 0].min(), pts[:, 1].max()
        cv2.putText(img, ph.label, (int(x), int(y) + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, PHANTOM_BGR, 1, cv2.LINE_AA)
    if det.found:
        pts = (det.corners - np.array([px1, py1])).round().astype(np.int32)
        cv2.polylines(img, [pts], True, (0, 255, 0), 2)
        cv2.circle(img, tuple(int(v) for v in pts[0]), 4, (0, 0, 255), -1)
        x, y = pts[:, 0].min(), pts[:, 1].min()
        cv2.putText(img, f"id {det.tag_id}  {det.size_px:.0f}px",
                    (int(x), max(15, int(y) - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 0), 1, cv2.LINE_AA)
    else:
        cv2.putText(img, "no tag", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 0, 255), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""
