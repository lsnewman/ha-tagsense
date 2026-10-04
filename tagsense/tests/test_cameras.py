"""Shared bursts: one fetch per burst per camera, judged by every object on it."""
import json
import threading

import cv2
import numpy as np

from app import decision as dec
from app.cameras import CameraWorker
from app.ha_notify import NullNotifier
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


def setup(tmp_path, burst=3, min_hits=1, tags=(("bin", 5), ("recycling", 7))):
    tuning = Tuning(dec.Config(present_min_hits=min_hits), 2.0, 45.0, 0.5, 0.02, 0.5)
    client = FakeClient()
    src = CountingSource(two_tag_jpeg())
    cam = CameraWorker("cam", src, threading.Condition(), burst, 0.0, threading.Event())
    cam.notifier = NullNotifier()
    objs = {}
    for oid, tag in tags:
        oc = ObjectConfig(oid, oid.title(), tag, "go2rtc", "cam")
        o = TrackedObject(oc, tuning, str(tmp_path), ObjectPublisher(client, oid, oc.name), cam.cond,
                          cam.notifier)
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
        enabled = [o for o in cam.objects if o.settings.enabled]
    burst = cam.fetch_burst(enabled)
    for o in enabled:
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


def test_rejection_streak_alerts_and_reset_clears(tmp_path):
    cam, client, objs = trained_bin(tmp_path, 0.185)
    b, notes = objs["bin"], cam.notifier.calls
    for _ in range(2):
        b.on_check_now()
        run_once(cam)
    assert client.last("tagsense/bin/problem") == "OFF" and not notes
    b.on_check_now()
    run_once(cam)
    assert client.last("tagsense/bin/problem") == "ON"
    assert b.status()["alert"]["reason"] == "size"
    assert [c[0] for c in notes] == ["create"]
    assert "Reset learned position" in notes[0][1]["message"]
    assert notes[0][1]["notification_id"] == "tagsense_bin_rejected"
    b.on_check_now()
    run_once(cam)
    assert len(notes) == 1                               # same message: not resent
    b.reset_reference()
    assert not (b.reference.hits or b.decision.reject_streak)
    assert json.loads((tmp_path / "objects/bin/reference.json").read_text())["hits"] == 0
    assert client.last("tagsense/bin/problem") == "OFF"
    assert [c[0] for c in notes] == ["create", "dismiss"]
    b.on_check_now()
    run_once(cam)                                        # size gate off again: a hit
    assert '"outcome": "hit"' in client.last("tagsense/bin/status/attributes")


def test_early_exit_when_present(tmp_path):
    cam, src, client, objs = setup(tmp_path, burst=5, min_hits=2, tags=(("bin", 5),))
    b = objs["bin"]
    b.on_check_now()
    run_once(cam)
    assert src.calls == 5                                # not yet present: full burst
    b.on_check_now()
    run_once(cam)
    assert src.calls == 7                                # present, 2/2 hits: stop
    assert '"frames": 2' in client.last("tagsense/bin/status/attributes")
    assert '"outcome": "hit"' in client.last("tagsense/bin/status/attributes")


def test_no_early_exit_on_shared_camera_with_a_miss(tmp_path):
    cam, src, client, objs = setup(tmp_path, burst=5, min_hits=2)
    for o in objs.values():
        o.decision.state = dec.PRESENT
    objs["bin"].on_check_now()
    run_once(cam)
    assert src.calls == 5                                # recycling's tag is missing


def test_snapshot_on_state_change_and_chart(tmp_path):
    cam, src, client, objs = setup(tmp_path)
    objs["bin"].on_check_now()
    run_once(cam)
    snaps = objs["bin"].snapshots.list()
    assert [(e["from"], e["to"]) for e in snaps] == [("unknown", "present")]
    assert objs["bin"].snapshots.read(snaps[0]["name"])[:2] == b"\xff\xd8"
    objs["bin"].on_check_now()
    run_once(cam)
    assert len(objs["bin"].snapshots.list()) == 1        # no change: no new snapshot
    pts = objs["bin"].chart_points()
    assert len(pts) == 2 and pts[-1]["hits"] == 3 and pts[-1]["reported"] == "present"
    assert pts[-1]["aspect"] is not None and pts[-1]["contrast"] is not None
