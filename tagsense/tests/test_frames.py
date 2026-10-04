"""Data-driven tests over real frames (skipped without TAGSENSE_TESTDATA / test-frames/)."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.analysis import analyse
from app.detector import DEFAULT_CROP, Detector
from app.rotation import SQ, circ_dist, lid_angle
from app.sanity import check_image

EXTS = {".jpg", ".jpeg", ".png"}
DET = Detector(5)


def frames(root: Path, folder: str):
    d = root / folder
    return sorted(p for p in d.glob("*") if p.suffix.lower() in EXTS) if d.is_dir() else []


def test_present_frames_all_hit(testdata):
    fs = frames(testdata, "present")
    assert fs
    missed = [p.name for p in fs if not analyse(p.read_bytes(), DET, DEFAULT_CROP).hit]
    assert not missed


def test_absent_frames_no_phantoms(testdata):
    fs = frames(testdata, "absent")
    assert fs
    phantoms = [p.name for p in fs if analyse(p.read_bytes(), DET, DEFAULT_CROP).hit]
    assert not phantoms


def test_smear_frames_invalid(testdata):
    fs = frames(testdata, "smear") + frames(testdata, "corrupt")
    assert fs
    for p in fs:
        r = analyse(p.read_bytes(), DET, DEFAULT_CROP)
        assert not r.valid and not r.hit, p.name


@pytest.mark.parametrize("gain", [0.3, 0.15, 0.08])
def test_night_proxy_passes_sanity(testdata, gain):
    """Darkened, blurred, noised copies stand in for IR frames (provisional)."""
    rng = np.random.default_rng(0)
    for p in frames(testdata, "present")[:4] + frames(testdata, "absent"):
        img = cv2.imread(str(p)).astype(np.float32) * gain
        img = cv2.GaussianBlur(img, (5, 5), 1.5) + rng.normal(0, 1.0, img.shape)
        c = check_image(np.clip(img, 0, 255).astype(np.uint8), DEFAULT_CROP)
        assert c.ok, (p.name, gain, c.reason, c.ratio)


def turned(frame, corners, deg, scale=1.6):
    """The frame with the tag turned `deg` clockwise on its own plane, in place.
    The patch is pasted inside the quad scaled 1.6x, so the quiet zone survives."""
    H = cv2.getPerspectiveTransform(corners.astype(np.float32), SQ * 100 + 50)
    R = np.vstack([cv2.getRotationMatrix2D((50, 50), -deg, 1.0), [0, 0, 1]])
    warped = cv2.warpPerspective(frame, np.linalg.inv(H) @ R @ H, frame.shape[1::-1])
    c = corners.mean(axis=0)
    mask = np.zeros(frame.shape[:2], np.uint8)
    cv2.fillPoly(mask, [((corners - c) * scale + c).astype(np.int32)], 255)
    out = frame.copy()
    out[mask > 0] = warped[mask > 0]
    return out


@pytest.mark.parametrize("deg", [90, 180, 270])
def test_rotation_on_real_frames(testdata, deg):
    fs = frames(testdata, "present")
    assert fs
    for p in fs:
        frame = cv2.imread(str(p))
        ref = DET.detect(frame, DEFAULT_CROP).corners
        det = DET.detect(turned(frame, ref, deg), DEFAULT_CROP)
        assert det.found, p.name
        assert circ_dist(lid_angle(ref, det.corners), deg) < 2, p.name
