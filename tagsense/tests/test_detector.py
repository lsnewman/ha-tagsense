import logging

import cv2
import cv2.aruco as aruco
import numpy as np
import pytest

from app.detector import DEFAULT_CROP, Detector, annotate, validate_crop

log = logging.getLogger(__name__)


def synthetic_frame(diag_px: float, centre=(1800, 736), seed=0, tag_id=5):
    """1080p noisy frame with a foreshortened tag16h5 marker (plus quiet zone)."""
    rng = np.random.default_rng(seed)
    frame = rng.normal(110, 25, (1080, 1920, 3)).clip(0, 255).astype(np.uint8)
    frame = cv2.GaussianBlur(frame, (5, 5), 1.0)
    marker = aruco.generateImageMarker(
        aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_16h5), tag_id, 120)
    tile = cv2.copyMakeBorder(marker, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    s = tile.shape[0]
    # Trapezoid squashed vertically (camera looking down at the lid).
    half_w = diag_px * 0.42
    half_h = diag_px * 0.26
    cx, cy = centre
    dst_tag = np.float32([[cx - half_w * 0.9, cy - half_h], [cx + half_w * 0.9, cy - half_h],
                          [cx + half_w, cy + half_h], [cx - half_w, cy + half_h]])
    # Map the tile corners so that the inner marker lands on dst_tag.
    src_marker = np.float32([[20, 20], [140, 20], [140, 140], [20, 140]])
    H = cv2.getPerspectiveTransform(src_marker, dst_tag)
    warped = cv2.warpPerspective(cv2.cvtColor(tile, cv2.COLOR_GRAY2BGR), H, (1920, 1080),
                                 flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.full((s, s), 255, np.uint8), H, (1920, 1080))
    frame[mask > 0] = warped[mask > 0]
    return frame, dst_tag


def test_synthetic_tag_found_and_mapped_to_full_frame():
    frame, dst = synthetic_frame(50)
    det = Detector(5).detect(frame, DEFAULT_CROP)
    assert det.found
    assert np.allclose(det.centre, dst.mean(axis=0), atol=2.0)
    assert det.size_px == pytest.approx(np.linalg.norm(dst[0] - dst[2]), rel=0.1)
    assert annotate(frame, det)[:2] == b"\xff\xd8"


def test_wrong_id_is_not_a_hit():
    frame, _ = synthetic_frame(50)
    det = Detector(7).detect(frame, DEFAULT_CROP)
    assert not det.found
    assert 5 in det.other_ids


def frame_with_phantom():
    """Target ID 5 plus an ID 16 elsewhere in the crop."""
    frame, _ = synthetic_frame(50)
    other, _ = synthetic_frame(50, centre=(1500, 700), seed=1, tag_id=16)
    frame[600:800, 1400:1600] = other[600:800, 1400:1600]
    return frame


def test_phantom_ids_kept_with_geometry_and_drawn():
    frame = frame_with_phantom()
    det = Detector(5).detect(frame, DEFAULT_CROP)
    assert det.found and det.other_ids == [16]
    ph = det.others[0]
    assert np.allclose(ph.centre_norm, (1500 / 1920, 700 / 1080), atol=0.003)
    assert ph.describe().startswith("ignored: id 16 (not this object's tag) at (0.78")
    jpg = annotate(frame, det)
    assert jpg[:2] == b"\xff\xd8"


def test_tag_outside_crop_not_found():
    frame, _ = synthetic_frame(50, centre=(300, 300))
    assert not Detector(5).detect(frame, DEFAULT_CROP).found


def test_small_tag_informational():
    """~20 px tag: near the decode limit, synthetic data is flaky, so log only."""
    frame, _ = synthetic_frame(20)
    det = Detector(5).detect(frame, DEFAULT_CROP)
    log.warning("synthetic ~20px tag found=%s", det.found)


@pytest.mark.parametrize("crop,expected", [
    ((0.1, 0.2, 0.3, 0.4), (0.1, 0.2, 0.3, 0.4)),
    ((-1, 0, 2, 1), (0.0, 0.0, 1.0, 1.0)),
    ((0.5, 0.5, 0.4, 0.9), DEFAULT_CROP),
    (("x", 0, 1, 1), DEFAULT_CROP),
    ((0, 0, 1), DEFAULT_CROP),
])
def test_validate_crop(crop, expected):
    assert validate_crop(crop) == expected


def test_shape_gate_rejects_target_above_max_aspect():
    frame, _ = synthetic_frame(50)          # synthetic quad aspect ~1.6
    det = Detector(5, max_aspect=1.2).detect(frame, DEFAULT_CROP)
    assert not det.found
    assert det.other_ids == [5] and det.others[0].rejected_target
    assert det.others[0].describe().startswith("rejected id 5 (aspect 1.6 > 1.2) at")
    assert annotate(frame, det)[:2] == b"\xff\xd8"


def test_shape_gate_default_and_off_accept_real_shape():
    frame, _ = synthetic_frame(50)
    assert Detector(5).detect(frame, DEFAULT_CROP).found
    assert Detector(5, max_aspect=0).detect(frame, DEFAULT_CROP).found


def test_quad_aspect():
    from app.detector import quad_aspect
    sliver = np.float32([[0, 0], [37, 0], [37, 6], [0, 6]])
    assert quad_aspect(sliver) == pytest.approx(37 / 6)
