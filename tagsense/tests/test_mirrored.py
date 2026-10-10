"""Mirrored tags: the per-object option, and the hint while a tag has never been seen."""
import threading

import cv2
import numpy as np

from app import decision as dec
from app.cameras import CameraWorker
from app.detector import DEFAULT_CROP, Detector
from app.ha_notify import NullNotifier
from app.mqtt_ha import ObjectPublisher
from app.objects import parse_one
from app.rotation import lid_angle
from app.tracker import TrackedObject, Tuning

from test_cameras import CountingSource, FakeClient, run_once
from test_detector import synthetic_frame


def mirrored_frame(**kw):
    """The synthetic frame left-right reversed, as a camera with a flip setting shows it."""
    frame, dst = synthetic_frame(50, **kw)
    w = frame.shape[1]
    dst = dst.copy()
    dst[:, 0] = w - 1 - dst[:, 0]
    return np.ascontiguousarray(cv2.flip(frame, 1)), dst


def mirrored_crop():
    x1, y1, x2, y2 = DEFAULT_CROP
    return (1 - x2, y1, 1 - x1, y2)


def test_mirrored_tag_found_only_with_the_option_and_mapped_back():
    frame, dst = mirrored_frame()
    crop = mirrored_crop()
    assert not Detector(5).detect(frame, crop).found
    det = Detector(5, mirrored=True).detect(frame, crop)
    assert det.found
    # Same quad (in any corner order) as the tag drawn in the mirrored frame.
    for p in dst:
        assert np.linalg.norm(det.corners - p, axis=1).min() < 3


def test_mirrored_option_misses_a_normal_tag():
    frame, _ = synthetic_frame(50)
    assert not Detector(5, mirrored=True).detect(frame, DEFAULT_CROP).found


def test_rotation_measured_the_same_way_round_when_mirrored():
    """A camera flip must not reverse the Rotation sensor: the lid turns the same way."""
    angles = {}
    for mirrored in (False, True):
        corners = []
        for rot90 in (0, 1):
            if mirrored:
                frame, _ = mirrored_frame(rot90=rot90)
                det = Detector(5, mirrored=True).detect(frame, mirrored_crop())
            else:
                frame, _ = synthetic_frame(50, rot90=rot90)
                det = Detector(5).detect(frame, DEFAULT_CROP)
            corners.append(det.corners)
        angles[mirrored] = lid_angle(corners[0], corners[1])
    assert abs(angles[False] - angles[True]) < 3


def test_config_flag_parsed_and_defaults_off():
    base = {"name": "Bin", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam"}
    assert parse_one(base).mirrored is False
    assert parse_one(base | {"mirrored": True}).mirrored is True
    assert parse_one(base | {"mirrored": "on"}).mirrored is True
    assert parse_one(base | {"mirrored": "false"}).mirrored is False


def tracked(tmp_path, frame, mirrored=False):
    tuning = Tuning(dec.Config(present_min_hits=1), 2.0, 45.0, 0.5, 0.02, 0.5)
    src = CountingSource(cv2.imencode(".jpg", frame)[1].tobytes())
    cam = CameraWorker("cam", src, threading.Condition(), 2, 0.0, threading.Event())
    oc = parse_one({"name": "Bin", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam",
                    "mirrored": mirrored})
    o = TrackedObject(oc, tuning, str(tmp_path), ObjectPublisher(FakeClient(), oc.id, oc.name),
                      cam.cond, NullNotifier())
    o.settings.crop_x1, o.settings.crop_y1, o.settings.crop_x2, o.settings.crop_y2 = mirrored_crop()
    cam.objects.append(o)
    return cam, o


class CountingProbe:
    def __init__(self, det):
        self.det, self.calls = det, 0

    def detect(self, *a, **kw):
        self.calls += 1
        return self.det.detect(*a, **kw)


def test_never_seen_mirrored_tag_gets_a_hint(tmp_path):
    frame, _ = mirrored_frame()
    cam, o = tracked(tmp_path, frame)
    o.mirror_probe = probe = CountingProbe(o.mirror_probe)
    run_once(cam)
    assert "found mirrored: turn on Mirrored" in o.last_diag["warning"]
    assert probe.calls == 1                 # one frame of the burst, not every frame


def test_no_probe_once_the_tag_has_been_seen(tmp_path):
    frame, _ = mirrored_frame()
    cam, o = tracked(tmp_path, frame)
    o.reference.hits = 1
    o.mirror_probe = probe = CountingProbe(o.mirror_probe)
    run_once(cam)
    assert probe.calls == 0
    assert not o.last_diag["warning"]


def test_mirrored_object_finds_its_tag_without_a_hint(tmp_path):
    frame, _ = mirrored_frame()
    cam, o = tracked(tmp_path, frame, mirrored=True)
    o.mirror_probe = probe = CountingProbe(o.mirror_probe)
    run_once(cam)
    assert o.decision.state == dec.PRESENT
    assert probe.calls == 0
