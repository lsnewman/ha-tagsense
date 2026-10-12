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


# --- home ----------------------------------------------------------------------

WIDTH = 50 / np.sqrt(2)     # side of hit()'s tag, in px


def test_home_is_set_once_learned_and_then_fixed():
    r = trained(ref.MIN_HITS - 1)
    assert r.home is None and r.distance(hit()) is None
    r.learn(hit())
    assert r.home == [r.cx, r.cy]
    for _ in range(30):                              # the average follows a move...
        r.learn(hit(cx=1500))
    assert r.home[0] == 1800 / 1920                  # ...home does not
    assert r.distance(hit(cx=1500)) == round(300 / WIDTH, 1)


def test_distance_in_tag_widths_on_a_wide_frame():
    r = trained()
    assert r.distance(hit()) == 0.0
    assert r.distance(hit(cx=1800 - 3 * WIDTH)) == 3.0            # sideways
    assert r.distance(hit(cy=736 + 3 * WIDTH)) == 3.0             # down: same px, same widths
    assert r.distance(hit(cx=1800 - 3 * WIDTH, frame=(1920, 1080))) == \
        r.distance(hit(cy=736 - 3 * WIDTH))
    assert r.distance(Detection(found=False, tag_id=5, frame_wh=(1920, 1080), crop_px=(0, 0, 1, 1),
                                ms=0, mean=0, std=0)) is None


def test_set_home_and_legacy_files(tmp_path):
    r = trained()
    r.set_home(*Reference._measure(hit(cx=1000)))
    assert r.distance(hit(cx=1000)) == 0.0
    path = str(tmp_path / "reference.json")
    r.save(path)
    assert Reference.load(path).home == r.home
    # Learned before 0.5.2: no home stored, so it is taken from the average on load.
    import json
    raw = json.loads(open(path).read())
    for k in ("home_size", "home_cx", "home_cy"):
        raw.pop(k)
    open(path, "w").write(json.dumps(raw))
    legacy = Reference.load(path)
    assert legacy.home == [legacy.cx, legacy.cy]
    assert Reference(hits=3, size=0.1, cx=0.5, cy=0.5).home is None   # not ready: no home yet
