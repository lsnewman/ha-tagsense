import math

import cv2
import numpy as np
import pytest

from app import rotation as rot
from app.reference import Reference


def lid_to_image():
    """A homography that looks down at a lid at an angle (strong perspective)."""
    lid = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])
    img = np.float32([[900, 600], [1100, 610], [1180, 720], [840, 700]])
    return cv2.getPerspectiveTransform(lid, img)


def tag_corners(H, deg, centre=(0.5, 0.5), size=0.2):
    """Image corners of a square tag on the lid, turned `deg` clockwise."""
    t = math.radians(deg)
    R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
    lid = (rot.SQ * size) @ R.T + centre
    return cv2.perspectiveTransform(np.float32(lid).reshape(1, 4, 2), H)[0]


@pytest.mark.parametrize("deg", [0, 10, 45, 90, 135, 180, 225, 270, 315, 359])
def test_lid_angle_exact_under_perspective(deg):
    H = lid_to_image()
    ref = tag_corners(H, 0)
    assert rot.circ_dist(rot.lid_angle(ref, tag_corners(H, deg)), deg) < 0.01


def test_lid_angle_ignores_moves_within_the_plane():
    H = lid_to_image()
    ref = tag_corners(H, 0)
    assert rot.circ_dist(rot.lid_angle(ref, tag_corners(H, 180, centre=(0.3, 0.8))), 180) < 0.01


def test_image_plane_angle_would_be_wrong():
    """Why the homography is needed: the raw image angle of a 90 deg turn is far off."""
    H = lid_to_image()
    ref, c = tag_corners(H, 0), tag_corners(H, 90)
    e0, e1 = ref[1] - ref[0], c[1] - c[0]
    raw = math.degrees(math.atan2(e1[1], e1[0]) - math.atan2(e0[1], e0[0])) % 360
    assert rot.circ_dist(raw, 90) > 10


def test_lid_angle_resolution_independent():
    H = lid_to_image()
    ref, c = tag_corners(H, 0), tag_corners(H, 90)
    s = np.array([1 / 1920, 1 / 1080])
    assert rot.lid_angle(ref * s, c * s) == pytest.approx(rot.lid_angle(ref, c), abs=1e-3)


def test_lid_angle_degenerate():
    assert rot.lid_angle(np.zeros((4, 2)), np.ones((4, 2))) is None


def test_circular_mean_wraps():
    assert rot.circ_dist(rot.circular_mean([359, 1]), 0) < 1e-9
    assert rot.circular_mean([170, 190]) == pytest.approx(180)
    assert rot.circular_mean([]) is None


def test_snap_steps():
    assert rot.snap(178.6, 4) == 180
    assert rot.snap(44, 4) == 0
    assert rot.snap(46, 4) == 90
    assert rot.snap(350, 4) == 0
    assert rot.snap(46, 8) == 45
    assert rot.snap(123, 1) == 0                       # 1 step = always 0
    assert rot.snap(19.5, 36) == 20


def test_snap_hysteresis():
    assert rot.snap(47, 4, prev=0) == 0                # within the 5 deg margin
    assert rot.snap(51, 4, prev=0) == 90
    assert rot.snap(314, 4, prev=0) == 0               # margin works across 0/360
    assert rot.snap(309, 4, prev=0) == 270
    assert rot.snap(47, 4, prev=45) == 90              # prev is not a step: ignored
    assert rot.snap(23, 8, prev=0) == 0
    assert rot.snap(28, 8, prev=0) == 45


def test_clamp_and_format():
    assert [rot.clamp_steps(v) for v in (0, 1, 4, "8", 36, 99)] == [1, 1, 4, 8, 36, 36]
    assert rot.fmt_deg(180.0) == 180 and isinstance(rot.fmt_deg(180.0), int)
    assert rot.fmt_deg(22.5) == 22.5
    assert rot.fmt_deg(359.99) == 0


def test_old_reference_file_loads_without_orientation(tmp_path):
    p = tmp_path / "reference.json"
    p.write_text('{"hits": 12, "size": 0.05, "cx": 0.9, "cy": 0.7}')
    r = Reference.load(str(p))
    assert r.hits == 12 and r.orient is None
    r.set_orient(np.float32([[0.1, 0.2], [0.3, 0.2], [0.3, 0.4], [0.1, 0.4]]))
    r.save(str(p))
    assert Reference.load(str(p)) == r
