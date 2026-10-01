"""Shared bursts: one fetch per burst per camera, judged by every object on it."""
import threading

import cv2
import numpy as np

from app import decision as dec
from app.cameras import CameraWorker
from app.mqtt_ha import ObjectPublisher
from app.objects import ObjectConfig
from app.scheduler import CONFIRM, MANUAL, SHARED
from app.sources import Frame
from app.tracker import TrackedObject, Tuning

from test_detector import synthetic_frame


class FakeClient:
    version = "test"

    def __init__(self):
        self.msgs = []

    def pub(self, topic, payload, retain=True):
        self.msgs.append((topic, payload))

    def last(self, topic):
        return next((p for t, p in reversed(self.msgs) if t == topic), None)


class CountingSource:
    name = "fake"

    def __init__(self, jpeg):
        self.jpeg, self.calls = jpeg, 0

    def fetch(self):
        self.calls += 1
        return Frame(self.jpeg, "fake", 1.0)


def two_tag_jpeg():
    """Frame with tag 5 (bin) present and tag 7 (recycling) absent."""
    frame, _ = synthetic_frame(50)
    return cv2.imencode(".jpg", frame)[1].tobytes()


def setup(tmp_path):
    tuning = Tuning(dec.Config(present_min_hits=1), 2.0, 45.0, 0.5, 0.02, 0.5)
    client = FakeClient()
    src = CountingSource(two_tag_jpeg())
    cam = CameraWorker("cam", src, threading.Condition(), 3, 0.0, threading.Event())
    objs = {}
    for oid, tag in (("bin", 5), ("recycling", 7)):
        oc = ObjectConfig(oid, oid.title(), tag, "go2rtc", "cam")
        o = TrackedObject(oc, tuning, str(tmp_path), ObjectPublisher(client, oid, oc.name), cam.cond)
        cam.objects.append(o)
        objs[oid] = o
    for o in cam.objects:
        o.known_ids = {p.oc.tag_id for p in cam.objects if p is not o}
    return cam, src, client, objs


def run_once(cam):
    """One iteration of CameraWorker.run without the thread."""
    import time
    with cam.cond:
        due = cam.due(time.monotonic())
    burst = cam.fetch_burst()
    for o in cam.objects:
        if o.settings.enabled:
            o.process(due.get(o, SHARED), burst)
    return due


def test_one_fetch_serves_all_objects(tmp_path):
    cam, src, client, objs = setup(tmp_path)
    objs["bin"].on_check_now()
    due = run_once(cam)
    assert list(due.values()) == [MANUAL]
    assert src.calls == 3                                   # burst_size, not 2x
    assert client.last("tagsense/bin/status") == "present"
    rec_attrs = client.last("tagsense/recycling/status/attributes")
    assert '"trigger": "shared"' in rec_attrs and '"outcome": "miss"' in rec_attrs


def test_only_requester_starts_confirmation_chain(tmp_path):
    cam, src, client, objs = setup(tmp_path)
    objs["recycling"].on_check_now()
    run_once(cam)
    assert objs["recycling"].sched.confirm_at is not None   # manual miss -> chain
    assert objs["bin"].sched.confirm_at is None             # shared hit -> nothing


def test_shared_check_resets_poll_timer(tmp_path):
    cam, src, client, objs = setup(tmp_path)
    before = objs["recycling"].sched.next_poll
    objs["bin"].on_check_now()
    run_once(cam)
    assert objs["recycling"].sched.next_poll >= before


def test_disabled_object_skipped(tmp_path):
    cam, src, client, objs = setup(tmp_path)
    objs["recycling"].on_setting("enabled", "OFF")
    objs["bin"].on_check_now()
    run_once(cam)
    assert client.last("tagsense/recycling/status/attributes") is not None
    assert '"reason": "disabled"' in client.last("tagsense/recycling/status/attributes")
    assert client.last("tagsense/recycling/diag/last_check") is None


def test_state_files_per_object(tmp_path):
    cam, src, client, objs = setup(tmp_path)
    objs["bin"].on_check_now()
    run_once(cam)
    assert (tmp_path / "objects" / "bin" / "state.json").exists()
    assert (tmp_path / "objects" / "recycling" / "state.json").exists()


def test_other_objects_tags_are_not_phantoms(tmp_path):
    cam, src, client, objs = setup(tmp_path)
    objs["recycling"].on_check_now()
    run_once(cam)
    assert client.last("tagsense/recycling/diag/phantom_decodes") == "0"   # tag 5 is Bin's
    objs["recycling"].known_ids = set()
    objs["recycling"].on_check_now()
    run_once(cam)
    assert client.last("tagsense/recycling/diag/phantom_decodes") == "3"


def trained_bin(tmp_path, usual_size_frac):
    cam, src, client, objs = setup(tmp_path)
    ref = objs["bin"].reference
    ref.hits, ref.size, ref.cx, ref.cy = 10, usual_size_frac, 0.94, 0.68
    return cam, client, objs


def test_size_gate_rejects_tiny_target_decode(tmp_path):
    # synthetic tag is ~50px in a 1080p frame (4.6%); pretend the usual size is 4x that
    cam, client, objs = trained_bin(tmp_path, 0.185)
    objs["bin"].on_check_now()
    run_once(cam)
    attrs = client.last("tagsense/bin/status/attributes")
    assert '"hits": 0' in attrs and '"outcome": "miss"' in attrs
    assert "size 24% of usual < 50%" in client.last("tagsense/bin/diag/warning")
    assert client.last("tagsense/bin/diag/phantom_decodes") == "3"
    assert objs["bin"].reference.hits == 10          # rejected reads are not learned


def test_size_gate_passes_normal_size_and_waits_for_learning(tmp_path):
    cam, client, objs = trained_bin(tmp_path, 0.05)      # usual ~ actual
    objs["bin"].on_check_now()
    run_once(cam)
    assert '"hits": 3' in client.last("tagsense/bin/status/attributes")
    cam, client, objs = trained_bin(tmp_path / "b", 0.185)
    objs["bin"].reference.hits = 9                       # not ready yet: no size gate
    objs["bin"].on_check_now()
    run_once(cam)
    assert '"hits": 3' in client.last("tagsense/bin/status/attributes")
