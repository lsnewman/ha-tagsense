"""Selectable AprilTag family: id ranges come from OpenCV, tag16h5 stays the default."""
import cv2
import numpy as np
import pytest

from app.detector import DEFAULT_CROP, DEFAULT_FAMILY, FAMILIES, Detector, family_info
from app.main import App, DEFAULT_OPTIONS
from app.objects import ConfigError, ObjectStore, parse_list, parse_one
from app.tagprint import tag_grid, tag_png, tag_svg

from test_detector import synthetic_frame
from test_web import FakeMqtt, fake_source

# Pinned to opencv-python-headless 5.0.0.93; a change here means OpenCV changed
# its dictionaries, and the docs' id ranges need checking.
EXPECTED = {"tag16h5": (30, 6), "tag25h9": (35, 7), "tag36h10": (2320, 8), "tag36h11": (587, 8)}


def test_default_family_is_tag16h5():
    assert DEFAULT_FAMILY == "tag16h5"
    assert "tag_family" not in DEFAULT_OPTIONS          # per object, set in the panel
    assert Detector(5).family.name == "tag16h5"
    oc = parse_one({"name": "Bin", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam"})
    assert oc.tag_family == "tag16h5"


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_family_info_matches_opencv(family):
    fi = family_info(family)
    assert (fi.id_count, fi.cells) == EXPECTED[family]


def test_unknown_family_rejected():
    with pytest.raises(ValueError, match="tag99h1"):
        family_info("tag99h1")


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_tag_id_range_follows_family(family):
    n = EXPECTED[family][0]
    obj = {"name": "Bin", "source": "go2rtc", "go2rtc_stream": "cam", "tag_family": family}
    oc = parse_one(obj | {"tag_id": n - 1})
    assert (oc.tag_family, oc.tag_id) == (family, n - 1)
    with pytest.raises(ConfigError, match=f"tag_id must be 0-{n - 1} \\({family}\\)"):
        parse_one(obj | {"tag_id": n})


def test_unknown_family_rejected_in_object():
    with pytest.raises(ConfigError, match="tag_family must be one of"):
        parse_one({"name": "Bin", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam",
                   "tag_family": "tag99h1"})


def test_same_id_in_different_families_on_one_camera_is_allowed():
    base = {"source": "go2rtc", "go2rtc_stream": "cam", "tag_id": 5}
    objs = parse_list([base | {"name": "Bin"}, base | {"name": "Car", "tag_family": "tag36h11"}])
    assert [o.tag_family for o in objs] == ["tag16h5", "tag36h11"]
    with pytest.raises(ConfigError, match="tag16h5 tag 5"):
        parse_list([base | {"name": "Bin"}, base | {"name": "Car"}])


def test_old_objects_json_without_family_loads_as_tag16h5(tmp_path):
    import json
    (tmp_path / "objects.json").write_text(json.dumps(
        [{"id": "bin", "name": "Bin", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam"}]))
    assert ObjectStore(str(tmp_path)).load()[0].tag_family == "tag16h5"


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_synthetic_detection_per_family(family):
    tag = EXPECTED[family][0] - 1
    frame, dst = synthetic_frame(90, tag_id=tag, family=family)
    det = Detector(tag, family=family).detect(frame, DEFAULT_CROP)
    assert det.found
    assert np.abs(det.corners - dst).max() < 3


def test_tag16h5_detector_ignores_other_family_tag():
    frame, _ = synthetic_frame(90, tag_id=5, family="tag36h11")
    assert not Detector(5).detect(frame, DEFAULT_CROP).found


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_printed_tag_per_family(family):
    n, cells = EXPECTED[family]
    tag = n - 1
    assert tag_grid(tag, family).shape == (cells, cells)
    img = cv2.imdecode(np.frombuffer(tag_png(tag, cell_px=20, family=family), np.uint8),
                       cv2.IMREAD_GRAYSCALE)
    bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    assert Detector(tag, family=family).detect(bgr, (0, 0, 1, 1)).found
    svg = tag_svg(tag, size_mm=cells * 10, quiet=True, family=family).decode()
    assert f'width="{(cells + 2) * 10}mm"' in svg and family in svg
    with pytest.raises(ValueError, match=f"0-{n - 1}"):
        tag_grid(n, family)


def make_app(tmp_path, objs):
    ObjectStore(str(tmp_path)).save(parse_list(objs))
    opts = dict(DEFAULT_OPTIONS, go2rtc_url="http://x")
    return App(opts, mqtt=FakeMqtt(), source_factory=fake_source, data_dir=str(tmp_path))


CAM = {"source": "go2rtc", "go2rtc_stream": "cam"}


def test_objects_use_their_own_family(tmp_path):
    app = make_app(tmp_path, [CAM | {"name": "Bin", "tag_id": 5},
                              CAM | {"name": "Car", "tag_id": 300, "tag_family": "tag36h11"},
                              CAM | {"name": "Chair", "tag_id": 7, "tag_family": "tag36h11"}])
    bin_, car, chair = (app.objects[k] for k in ("bin", "car", "chair"))
    assert (bin_.detector.family.name, bin_.detector.tag_id) == ("tag16h5", 5)
    assert (car.detector.family.name, car.detector.tag_id) == ("tag36h11", 300)
    # Other objects' tags are only "known" (not logged as ignored) within the same family.
    assert bin_.known_ids == set() and car.known_ids == {7} and chair.known_ids == {300}


def test_api_family_field_and_print(tmp_path):
    from app.web import Api, ApiError
    app = make_app(tmp_path, [CAM | {"name": "Bin", "tag_id": 5}])
    api = Api(app)
    st = api.state()
    assert st["default_family"] == "tag16h5"
    assert st["families"]["tag36h11"] == {"ids": 587, "cells": 8}
    assert st["objects"][0]["tag_family"] == "tag16h5"
    with pytest.raises(ApiError, match="0-586"):
        api.create(CAM | {"name": "Car", "tag_id": 587, "tag_family": "tag36h11"})
    api.create(CAM | {"name": "Car", "tag_id": 586, "tag_family": "tag36h11"})
    api.update("bin", {"tag_family": "tag25h9", "tag_id": 34})
    assert app.objects["bin"].detector.family.name == "tag25h9"
    exported = {o["id"]: o for o in api.export()["objects"]}
    assert exported["car"]["tag_family"] == "tag36h11"
    app.shutdown()
    app._stop_workers()


def test_sweep_transplant_and_phantoms_on_synthetic_frames():
    from app import sweep
    reals = []
    for seed in range(3):
        frame, _ = synthetic_frame(90, seed=seed)
        reals.append((f"f{seed}", frame, Detector(5).detect(frame, DEFAULT_CROP).corners))
    res = sweep.transplant(["tag16h5", "tag36h11"], reals, DEFAULT_CROP, 5)
    assert res["tag16h5 plain"][0] == "3/3" and res["tag36h11 plain"][0] == "3/3"
    tex = [(f"t{i}", t) for i, t in enumerate(sweep.textures(5))]
    out = sweep.phantoms(["tag36h11"], {"textures": tex, "real": [reals[0][:2]]}, DEFAULT_CROP,
                         {"f0": reals[0][2]})
    assert out["tag36h11"] == ["0", "0"]
