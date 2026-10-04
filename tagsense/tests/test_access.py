"""Access stage 2: QR reading on a scanner, events only, kept apart from the bin."""
import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.access.config import ScannerConfig, ScannerStore, parse_scanner
from app.access.manager import AccessError, AccessManager, access_mqtt_config
from app.access.mqtt import AVAILABILITY, AccessMqtt
from app.access.qr import QrDecoder, blackout, fingerprint
from app.access.scanner import DEBUG_MAX_FILES, Scanner
from app.main import App, DEFAULT_OPTIONS
from app.objects import ConfigError
from app.sources import FetchError, Frame
from app.web import Api, ApiError

from test_web import FakeMqtt, fake_source

PAYLOAD = "TS1RAB12CD34EF56GH78JK"          # stands in for a secret code


def qr_frame(payload=PAYLOAD, module_px=5.0, at=(700, 300), seed=0, size=(1280, 720)):
    """Noisy frame with a QR code (with its quiet zone), slightly skewed."""
    rng = np.random.default_rng(seed)
    w, h = size
    frame = rng.normal(110, 20, (h, w, 3)).clip(0, 255).astype(np.uint8)
    if payload is None:
        return frame
    q = cv2.QRCodeEncoder.create().encode(payload)
    side = int(q.shape[0] * module_px)
    img = cv2.cvtColor(cv2.resize(q, (side, side), interpolation=cv2.INTER_AREA),
                       cv2.COLOR_GRAY2BGR)
    x, y = at
    src = np.float32([[0, 0], [side, 0], [side, side], [0, side]])
    dst = np.float32([[x, y], [x + side, y + side * 0.05], [x + side * 0.97, y + side],
                      [x, y + side * 0.96]])
    H = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(img, H, (w, h))
    mask = cv2.warpPerspective(np.full((side, side), 255, np.uint8), H, (w, h))
    frame[mask > 0] = warped[mask > 0]
    return frame


def jpeg(img) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()


class FakePaho:
    """Stands in for paho's client so the real AccessMqtt publishing code runs."""

    def __init__(self):
        self.published = []         # (topic, payload, retain)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))

        class R:
            def wait_for_publish(self, t=None):
                pass
        return R()

    def disconnect(self):
        pass

    def loop_stop(self):
        pass

    def all_bytes(self) -> bytes:
        return b"".join(p if isinstance(p, bytes) else str(p).encode()
                        for t, p, _ in self.published) + \
            "".join(t for t, _, _ in self.published).encode()


def access_mqtt():
    m = AccessMqtt(None, "test", on_scan=lambda sid: None, on_connected=lambda: None,
                   client=FakePaho())
    m.start = lambda: None
    m.connected = True
    return m


class SeqSource:
    name = "fake"

    def __init__(self, frames):
        self.frames, self.i = [jpeg(f) for f in frames], 0

    def fetch(self):
        data = self.frames[min(self.i, len(self.frames) - 1)]
        self.i += 1
        return Frame(data, "fake", 1.0)


def scanner(tmp_path, frames, **cfg):
    c = ScannerConfig("door", "Door", "go2rtc", "doorbell", **({"window_s": 2.0,
                                                               "frame_interval_s": 0.0} | cfg))
    m = access_mqtt()
    s = Scanner(c, SeqSource(frames), m, str(tmp_path), threading.Event())
    return s, m


def wait_for(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def events(m):
    return [json.loads(p) for t, p, r in m.client.published if t.endswith("/event")]


# --- decoding ---------------------------------------------------------------------

def test_decoder_reads_code_and_maps_quad_to_full_frame():
    r = QrDecoder().decode(qr_frame(), (0.4, 0.2, 0.9, 0.9))
    assert r and r.text == PAYLOAD
    assert 680 < r.quad[:, 0].min() < 760 and 280 < r.quad[:, 1].min() < 360
    assert r.fingerprint == fingerprint(PAYLOAD) and len(r.fingerprint) == 8


def test_decoder_gives_up_on_tiny_codes():
    assert QrDecoder().decode(qr_frame(module_px=1.0)) is None


def test_blackout_makes_code_unreadable():
    img = qr_frame()
    r = QrDecoder().decode(img)
    assert QrDecoder().decode(blackout(img, [r.quad])) is None


# --- scanner window ----------------------------------------------------------------

def test_scan_reports_code_without_its_content(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    s, m = scanner(tmp_path, [qr_frame(None), qr_frame()])
    s.thread.start()
    s.scan()
    assert wait_for(lambda: events(m))
    ev = events(m)[0]
    assert ev["event_type"] == "qr_seen" and ev["fingerprint"] == fingerprint(PAYLOAD)
    assert ev["length"] == len(PAYLOAD) and ev["frames"] == 2
    # The payload must not appear anywhere: MQTT, logs, the published image.
    assert PAYLOAD.encode() not in m.client.all_bytes()
    assert PAYLOAD not in caplog.text
    img = cv2.imdecode(np.frombuffer(s.last_jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert QrDecoder().decode(img) is None
    # Events and images are never retained; the window closes after the read.
    assert all(not r for t, _, r in m.client.published if t.endswith(("/event", "/image")))
    assert wait_for(lambda: not s.scanning)
    s.stop_event.set()


def test_scan_times_out_when_no_code(tmp_path):
    s, m = scanner(tmp_path, [qr_frame(None)], window_s=0.5, frame_interval_s=0.05)
    s.thread.start()
    s.scan()
    assert wait_for(lambda: events(m))
    ev = events(m)[0]
    assert ev["event_type"] == "scan_timeout" and ev["frames"] >= 2
    scanning = [p for t, p, _ in m.client.published if t.endswith("/scanning")]
    assert scanning == ["ON", "OFF"]
    s.stop_event.set()


def test_no_scanning_without_a_trigger(tmp_path):
    s, m = scanner(tmp_path, [qr_frame()])
    s.thread.start()
    time.sleep(0.3)
    assert s.source.i == 0 and not events(m)
    s.stop_event.set()


def test_fetch_failures_end_in_timeout_not_a_read(tmp_path):
    class Broken:
        name = "fake"

        def fetch(self):
            raise FetchError("down")
    s, m = scanner(tmp_path, [], window_s=0.3)
    s.source = Broken()
    s.thread.start()
    s.scan()
    assert wait_for(lambda: events(m), timeout=5)
    assert events(m)[0]["event_type"] == "scan_timeout" and events(m)[0]["fetch_failures"] >= 1
    s.stop_event.set()


def test_debug_frames_are_capped(tmp_path):
    s, _ = scanner(tmp_path, [qr_frame(None)], debug_frames=True)
    for _ in range(DEBUG_MAX_FILES + 5):
        s._save_debug(b"x")
        time.sleep(0.002)
    assert len(s.debug_files()) == DEBUG_MAX_FILES


# --- MQTT ----------------------------------------------------------------------------

def test_discovery_under_own_node_id_and_retained_press_ignored():
    pressed = []
    m = AccessMqtt(None, "test", on_scan=pressed.append, on_connected=lambda: None,
                   client=FakePaho())
    m.publish_discovery("door", "Door")
    topics = [t for t, _, _ in m.client.published]
    configs = [t for t in topics if t.endswith("/config")]
    assert configs and all(t.split("/")[2] == "tagsense_access" for t in configs)
    event_cfg = next(json.loads(p) for t, p, _ in m.client.published if "/event/" in t)
    assert event_cfg["state_topic"] == "tagsense/access/door/event"
    assert event_cfg["availability_topic"] == AVAILABILITY

    class Msg:
        def __init__(self, topic, retain):
            self.topic, self.payload, self.retain = topic, b"PRESS", retain
    m._handle_message(None, None, Msg("tagsense/access/door/cmd/scan", True))
    m._handle_message(None, None, Msg("tagsense/door/cmd/scan", False))
    assert pressed == []
    m._handle_message(None, None, Msg("tagsense/access/door/cmd/scan", False))
    assert pressed == ["door"]


def test_access_login_required_and_separate():
    opts = dict(DEFAULT_OPTIONS, mqtt_host="broker", mqtt_username="addons", mqtt_password="x")
    with pytest.raises(AccessError, match="access_mqtt_username"):
        access_mqtt_config(opts)
    with pytest.raises(AccessError, match="separate MQTT user"):
        access_mqtt_config(opts | {"access_mqtt_username": "addons", "access_mqtt_password": "y"})
    cfg = access_mqtt_config(opts | {"access_mqtt_username": "tagsense_access",
                                     "access_mqtt_password": "y"})
    assert (cfg.host, cfg.username) == ("broker", "tagsense_access")


# --- config ----------------------------------------------------------------------------

def test_scanner_config_validation():
    ok = {"name": "Front door", "source": "ha_camera", "camera_entity": "camera.door"}
    assert parse_scanner(ok).id == "front_door"
    for bad, msg in (({"window_s": 1}, "window_s must be 5-120"),
                     ({"crop_x1": 0.5, "crop_x2": 0.4}, "crop is empty"),
                     ({"source": "rtsp"}, "source must be"),
                     ({"camera_entity": ""}, "camera_entity is required")):
        with pytest.raises(ConfigError, match=msg):
            parse_scanner(ok | bad)


# --- app integration ---------------------------------------------------------------------

def make_app(tmp_path, **opts):
    return App(dict(DEFAULT_OPTIONS, go2rtc_url="http://x", **opts), mqtt=FakeMqtt(),
               source_factory=fake_source, data_dir=str(tmp_path),
               access_mqtt=access_mqtt(), access_source_factory=lambda c, o: SeqSource([qr_frame()]))


def test_access_off_by_default_and_not_imported(tmp_path):
    code = ("import sys; sys.path.insert(0, 'tests'); sys.path.insert(0, '.');"
            "from test_web import FakeMqtt, fake_source; from app.main import App, DEFAULT_OPTIONS;"
            f"a = App(dict(DEFAULT_OPTIONS), mqtt=FakeMqtt(), source_factory=fake_source, "
            f"data_dir={str(tmp_path)!r});"
            "assert a.access is None;"
            "assert not [m for m in sys.modules if m.startswith('app.access')], 'imported';"
            "print('ok')")
    r = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "ok", r.stderr
    api = Api(make_app(tmp_path))
    assert api.state()["access"] == {"enabled": False, "error": None}
    for call in (lambda: api.access_status(), lambda: api.access_scan("door"),
                 lambda: api.access_create({"name": "Door"})):
        with pytest.raises(ApiError) as e:
            call()
        assert e.value.status == 404


def test_access_without_login_stays_off_and_bin_runs(tmp_path):
    app = App(dict(DEFAULT_OPTIONS, go2rtc_url="http://x", access_enabled=True,
                   mqtt_host="broker"),
              mqtt=FakeMqtt(), source_factory=fake_source, data_dir=str(tmp_path))
    assert app.access is None and "access_mqtt_username" in app.access_error
    assert Api(app).state()["access"]["error"] == app.access_error


def test_panel_manages_scanners_and_scans(tmp_path):
    app = make_app(tmp_path, access_enabled=True)
    api, acc = Api(app), app.access
    acc.start()
    sid = api.access_create({"name": "Front door", "source": "go2rtc", "go2rtc_stream": "doorbell",
                             "crop_x1": 0.4, "window_s": 5})["id"]
    assert sid == "front_door" and ScannerStore(acc.dir).load()[0].crop_x1 == 0.4
    m = acc.mqtt
    assert any("tagsense_access/front_door_code/config" in t for t, _, _ in m.client.published)
    api.access_scan(sid)
    assert wait_for(lambda: any(e["event_type"] == "qr_seen"
                                for e in api.access_status()["scanners"][0]["events"]))
    assert api.access_image(sid)
    with pytest.raises(ApiError, match="window_s"):
        api.access_update(sid, {"window_s": 500})
    api.access_delete(sid)
    cleared = [t for t, p, r in m.client.published if p == b"" and r]
    assert any(t.endswith("front_door_code/config") for t in cleared)
    assert (Path(acc.dir).stat().st_mode & 0o777) == 0o700
    acc.stop()
