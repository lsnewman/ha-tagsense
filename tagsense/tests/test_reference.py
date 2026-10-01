import numpy as np

from app import reference as ref
from app.detector import Detection
from app.reference import Reference


def hit(cx=1800.0, cy=736.0, size=50.0, frame=(1920, 1080)):
    """A found Detection whose quad has the given centre and diagonal."""
    h = size / (2 * np.sqrt(2))
    corners = np.float32([[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]])
    return Detection(found=True, tag_id=5, frame_wh=frame, crop_px=(0, 0, 1, 1), ms=0,
                     mean=0, std=0, corners=corners)


def trained(n=ref.MIN_HITS):
    r = Reference()
    for _ in range(n):
        r.learn(hit())
    return r


def test_no_warning_until_enough_hits():
    r = trained(ref.MIN_HITS - 1)
    assert r.warning(hit(cx=200)) is None
    r.learn(hit())
    assert r.warning(hit(cx=200)) is not None


def test_normal_hit_no_warning():
    assert trained().warning(hit(cx=1805, size=52)) is None


def test_far_centre_and_odd_size_warn():
    r = trained()
    assert "centre" in r.warning(hit(cx=1400))
    assert "size" in r.warning(hit(size=20))


def test_resolution_independent():
    r = trained()
    # same scene at 720p: everything scaled by 2/3
    assert r.warning(hit(cx=1200, cy=490.7, size=33.3, frame=(1280, 720))) is None


def test_follows_a_permanent_move():
    r = trained()
    for _ in range(40):
        r.learn(hit(cx=1500))
    assert r.warning(hit(cx=1500)) is None


def test_misses_are_ignored_and_persistence(tmp_path):
    r = trained()
    r.learn(Detection(found=False, tag_id=5, frame_wh=(1920, 1080), crop_px=(0, 0, 1, 1),
                      ms=0, mean=0, std=0))
    assert r.hits == ref.MIN_HITS
    p = str(tmp_path / "reference.json")
    r.save(p)
    assert Reference.load(p) == r
    assert Reference.load(str(tmp_path / "missing.json")) == Reference()
