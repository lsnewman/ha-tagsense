"""Web API against a real App with fake MQTT and fake cameras."""
import json
import threading
import time
import urllib.error
import urllib.request

import cv2
import pytest

from app.main import App, DEFAULT_OPTIONS
from app.objects import ObjectStore, parse_list
from app.sources import Frame
from app.web import Api, ApiError, start_web

from test_detector import synthetic_frame

JPEG = cv2.imencode(".jpg", synthetic_frame(50)[0])[1].tobytes()


class FakeMqtt:
    version = "test"

    def __init__(self):
        self.msgs = []

    def pub(self, topic, payload, retain=True):
        self.msgs.append((topic, payload))

    def start(self):
        pass

    def stop(self):
        pass

    def topics(self, prefix):
        return [t for t, _ in self.msgs if t.startswith(prefix)]


class FakeSource:
    name = "fake"

    def fetch(self):
        return Frame(JPEG, "fake", 1.0)


def fake_source(oc, opts):
    if oc.source == "go2rtc" and not opts.get("go2rtc_url"):
        raise ValueError("go2rtc_url is not set")
    return FakeSource()


@pytest.fixture
def app(tmp_path):
    opts = dict(DEFAULT_OPTIONS, go2rtc_url="http://x", burst_size=2, burst_interval_s=0)
    ObjectStore(str(tmp_path)).save(
        parse_list([{"name": "Bin", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam"}]))
    a = App(opts, mqtt=FakeMqtt(), source_factory=fake_source, data_dir=str(tmp_path))
    a._on_connected(True)
    a._start_workers()
    yield a
    a.shutdown()
    a._stop_workers()


def wait_for(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_state_and_startup_check(app):
    api = Api(app)
    assert wait_for(lambda: api.state()["objects"][0]["state"] == "present")
    o = api.state()["objects"][0]
    assert o["id"] == "bin" and o["last_corners"] and o["has_image"]
    assert api.history("bin")[0]["trigger"] == "startup"
    assert api.image("bin", "last")[:2] == b"\xff\xd8"
    assert api.frame("bin", 60)[:2] == b"\xff\xd8"


def test_create_update_delete_live(app, tmp_path):
    api = Api(app)
    assert api.create({"name": "Recycling", "tag_id": 7, "source": "go2rtc",
                       "go2rtc_stream": "cam"}) == {"id": "recycling"}
    assert set(app.objects) == {"bin", "recycling"}
    assert len(app.cameras) == 1                                   # shared camera
    assert wait_for(lambda: app.objects["recycling"].decision.last_check_ts)
    api.update("recycling", {"name": "Green bin", "id": "ignored"})
    with pytest.raises(ApiError, match="fall back"):
        api.update("recycling", {"fallback": True})
    api.update("recycling", {"fallback": True, "camera_entity": "camera.x"})
    assert app.objects["recycling"].oc.fallback and len(app.cameras) == 2
    assert app.objects["recycling"].oc.name == "Green bin"         # id unchanged
    stored = json.loads((tmp_path / "objects.json").read_text())
    assert [o["id"] for o in stored] == ["bin", "recycling"]
    app.mqtt.msgs.clear()
    api.delete("recycling")
    assert set(app.objects) == {"bin"}
    assert "homeassistant/binary_sensor/tagsense_recycling/presence/config" in app.mqtt.topics("homeassistant/")
    assert not (tmp_path / "objects" / "recycling").exists()


def test_validation_errors_leave_running_config(app):
    api = Api(app)
    with pytest.raises(ApiError, match="same camera"):
        api.create({"name": "Dup", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam"})
    with pytest.raises(ApiError, match="already used"):
        api.create({"name": "Other", "id": "bin", "tag_id": 9, "source": "go2rtc", "go2rtc_stream": "x"})
    with pytest.raises(ApiError, match="camera_entity"):
        api.create({"name": "Car", "tag_id": 1, "source": "ha_camera"})
    app.opts["go2rtc_url"] = ""
    with pytest.raises(ApiError, match="go2rtc_url"):
        api.create({"name": "Car", "tag_id": 1, "source": "go2rtc", "go2rtc_stream": "x"})
    assert list(app.objects) == ["bin"]
    assert all(c.thread.is_alive() for c in app.cameras.values())


def test_panel_setting_overwrites_retained_command(app):
    Api(app).settings("bin", {"crop_x1": 0.77})
    assert ("tagsense/bin/set/crop_x1", "0.77") in app.mqtt.msgs
    app.mqtt.msgs.clear()
    Api(app).settings("bin", {"crop_x1": 0.77})                # unchanged: still republished
    assert ("tagsense/bin/set/crop_x1", "0.77") in app.mqtt.msgs


def test_export_import_round_trip(app, tmp_path):
    api = Api(app)
    assert wait_for(lambda: app.objects["bin"].decision.last_check_ts)
    api.settings("bin", {"crop_x1": 0.3})
    exported = api.export()
    assert exported["objects"][0]["settings"]["crop_x1"] == 0.3
    text = json.dumps(exported["objects"] + [
        {"name": "Car", "tag_id": 9, "source": "go2rtc", "go2rtc_stream": "cam2",
         "settings": {"poll_interval": 120}}])
    api.settings("bin", {"crop_x1": 0.5})
    r = api.import_({"text": text})
    assert (r["added"], r["updated"]) == (["car"], ["bin"])
    assert r["history"]["bin"]["checks_added"] == 0          # its own history: nothing doubled
    assert set(app.objects) == {"bin", "car"}
    assert app.objects["bin"].settings.crop_x1 == 0.3
    assert app.objects["car"].settings.poll_interval == 120
    assert app.objects["bin"].decision.last_check_ts                 # bin kept its state


def test_import_errors_change_nothing(app):
    api = Api(app)
    before = list(app.configs)
    for body in ({"text": "nope"}, {"objects": []}, {"objects": [{"name": ""}]},
                 {"objects": [{"name": "Car", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam"}]},
                 {"objects": [{"name": "Bin", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam",
                               "settings": {"bogus": 1}}]}):
        with pytest.raises(ApiError) as e:
            api.import_(body)
        assert e.value.status == 400
    assert app.configs == before


def test_settings_and_check(app):
    api = Api(app)
    s = api.settings("bin", {"crop_x1": 0.1, "poll_interval": 5, "rotation_steps": 99})
    assert s["crop_x1"] == 0.1 and s["poll_interval"] == 10 and s["rotation_steps"] == 36
    s = api.settings("bin", {"enabled": False})
    assert s["enabled"] is False
    with pytest.raises(ApiError, match="disabled"):
        api.check("bin")
    with pytest.raises(ApiError, match="unknown setting"):
        api.settings("bin", {"nope": 1})
    with pytest.raises(ApiError) as e:
        api.check("missing")
    assert e.value.status == 404


def http(server, method, path, body=None, headers=None):
    port = server.server_address[1]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def test_http_layer(app):
    server = start_web(app, 0, allow_all=True, trust_admin=True)
    try:
        status, ctype, body = http(server, "GET", "/")
        assert status == 200 and b"TagSense" in body and ctype.startswith("text/html")
        status, _, body = http(server, "GET", "/api/state")
        assert status == 200 and json.loads(body)["objects"][0]["id"] == "bin"
        status, ctype, body = http(server, "GET", "/api/tag/5.png?cell=20")
        assert status == 200 and ctype == "image/png" and body[:4] == b"\x89PNG"
        assert http(server, "GET", "/api/tag/31.png")[0] == 400
        status, ctype, body = http(server, "GET", "/api/tag/5.svg?size_mm=50&quiet=0")
        assert status == 200 and ctype == "image/svg+xml" and b'width="50mm"' in body
        assert http(server, "POST", "/api/objects", {"name": ""})[0] == 400
        assert http(server, "GET", "/api/objects/nope/history")[0] == 404
        assert http(server, "GET", "/api/whatever")[0] == 404
        status, _, body = http(server, "PATCH", "/api/objects/bin/settings", {"crop_y1": 0.2})
        assert status == 200 and json.loads(body)["crop_y1"] == 0.2
        assert wait_for(lambda: app.objects["bin"].snapshots.list())
        status, _, body = http(server, "GET", "/api/objects/bin/snapshots")
        name = json.loads(body)[0]["name"]
        status, ctype, body = http(server, "GET", f"/api/objects/bin/snapshots/{name}.jpg")
        assert status == 200 and ctype == "image/jpeg"
        assert http(server, "GET", "/api/objects/bin/snapshots/123.jpg")[0] == 404
        assert http(server, "GET", "/api/objects/bin/snapshots/..%2Fstate.json")[0] == 404
        status, _, body = http(server, "GET", "/api/objects/bin/chart")
        assert status == 200 and json.loads(body)[0]["reported"] == "present"
        assert http(server, "POST", "/api/objects/bin/reset_reference")[0] == 200
        status, _, body = http(server, "POST", "/api/objects/bin/set_orientation")
        assert status == 200
        rot = json.loads(http(server, "GET", "/api/state")[2])["objects"][0]["rotation"]
        assert rot["value"] == 0 and rot["zero_set"] and len(rot["zero_corners"]) == 4
        status, _, body = http(server, "GET", "/api/export")
        assert json.loads(body)["objects"][0]["id"] == "bin"
        status, _, body = http(server, "POST", "/api/import", {"text": body.decode()})
        assert status == 200 and json.loads(body)["updated"] == ["bin"]
        with app.objects["bin"].cond:
            app.objects["bin"].decision.state = "absent"
        assert http(server, "POST", "/api/objects/bin/set_orientation")[0] == 409
    finally:
        server.shutdown()


def test_http_rejects_non_ingress_clients(app):
    server = start_web(app, 0, allow_all=False)
    try:
        assert http(server, "GET", "/api/state")[0] == 403
    finally:
        server.shutdown()


def test_history_round_trip_without_doubling(tmp_path):
    """dev -> main -> dev: chart records merge once; the newer learned position wins."""
    import time as _t
    from app.reference import Reference
    from app.web import Api

    def install(name):
        d = tmp_path / name
        d.mkdir()
        ObjectStore(str(d)).save(parse_list(
            [{"name": "Bin", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam"}]))
        a = App(dict(DEFAULT_OPTIONS, go2rtc_url="http://x"), mqtt=FakeMqtt(),
                source_factory=fake_source, data_dir=str(d))
        return a, Api(a)

    main, main_api = install("main")
    dev, dev_api = install("dev")
    now = _t.time()
    for i in range(3):
        main.objects["bin"].checklog.append({"t": now - 300 + i, "hits": 2})
    main.objects["bin"].reference = Reference(hits=40, size=0.05, cx=0.9, cy=0.6, updated=now - 100)
    dev.objects["bin"].checklog.append({"t": now - 10, "hits": 1})
    dev.objects["bin"].reference = Reference(hits=12, size=0.07, cx=0.88, cy=0.4, updated=now - 5)

    r = dev_api.import_({"text": json.dumps(main_api.export())})
    assert r["history"]["bin"] == {"checks_added": 3, "reference_taken": False}   # dev's is newer
    assert len(dev.objects["bin"].chart_points()) == 4
    r = main_api.import_({"text": json.dumps(dev_api.export())})
    assert r["history"]["bin"] == {"checks_added": 1, "reference_taken": True}
    assert len(main.objects["bin"].chart_points()) == 4
    assert main.objects["bin"].reference.hits == 12
    r = dev_api.import_({"text": json.dumps(main_api.export())})                  # and back again
    assert r["history"]["bin"]["checks_added"] == 0
    assert len(dev.objects["bin"].chart_points()) == 4


def test_import_without_history_still_works(app):
    from app.web import Api
    api = Api(app)
    old = {"tagsense": 1, "objects": [{"id": "bin", "name": "Bin", "tag_id": 5,
                                        "source": "go2rtc", "go2rtc_stream": "cam"}]}
    r = api.import_({"text": json.dumps(old)})
    assert r["updated"] == ["bin"] and r["history"] == {}
