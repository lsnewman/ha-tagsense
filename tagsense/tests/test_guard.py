"""The admin guard and "My pass": non-admins reach only their own pass; fail closed."""
import json
import re
import urllib.error
import urllib.request

import cv2
import numpy as np
import pytest

from app.access import codes
from app.access.qr import QrDecoder
from app.users import HaUser, UserDirectory
from app.web import NON_ADMIN_ROUTES, ROUTES, Api, start_web

from test_access import admin_app

ADMIN, KID, STRANGER = "u1", "u2", "u9"


def call(server, method, path, user=None):
    req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}",
                                 method=method, data=b"{}" if method in ("POST", "PUT", "PATCH") else None,
                                 headers={"Content-Type": "application/json",
                                          **({"X-Remote-User-Id": user} if user else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def sample_path(pattern):
    vals = {"oid": "bin", "sid": "door", "handle": "ABCD", "code_id": "ABCDEFGH", "tag": "5",
            "fmt": "png", "kind": "last", "name": "1"}
    path = re.sub(r"\(\?P<(\w+)>[^)]*\)", lambda m: vals[m.group(1)], pattern)
    return path.replace("\\.", ".")


@pytest.fixture
def server(tmp_path):
    app, api = admin_app(tmp_path)          # users: u1 Luke (admin), u2 Sam (not)
    app.access.start()
    api.access_create({"name": "Door", "source": "go2rtc", "go2rtc_stream": "d"})
    srv = start_web(app, 0, allow_all=True)
    yield srv, app, api
    srv.shutdown()
    app.access.stop()


def test_non_admin_and_anonymous_get_only_the_pass_routes(server):
    srv, app, api = server
    checked = 0
    for method, pattern, name in ROUTES:
        path = sample_path(pattern)
        for user in (KID, STRANGER, None):
            status, body = call(srv, method, path, user)
            if name in NON_ADMIN_ROUTES:
                assert status != 403 or user is None, (name, user, status)
            else:
                assert status == 403, (method, path, user, status)
                checked += 1
    assert checked == 3 * sum(name not in NON_ADMIN_ROUTES for _, _, name in ROUTES)
    status, body = call(srv, "GET", "/", KID)            # the page itself has no data
    assert status == 200


def test_admin_passes_the_guard(server):
    srv, *_ = server
    assert call(srv, "GET", "/api/state", ADMIN)[0] == 200
    assert call(srv, "GET", "/api/access", ADMIN)[0] == 200


def test_user_lookup_failure_locks_admins_out(server):
    srv, app, _ = server
    def boom():
        raise ConnectionError("HA down")
    app.users = UserDirectory(fetch=boom, token="x")
    assert call(srv, "GET", "/api/state", ADMIN)[0] == 403
    assert call(srv, "GET", "/api/whoami", ADMIN)[0] == 200     # still says why


def test_my_pass_is_mine_only(server):
    srv, app, api = server
    api.access_person_add({"label": "Sam", "ha_user_id": KID})
    status, body = call(srv, "GET", "/api/pass", KID)
    info = json.loads(body)
    assert status == 200 and info["has_pass"] and info["label"] == "Sam" and 0 < info["seconds_left"] <= 30
    assert "secret" not in body.decode() and "handle" not in info
    status, png = call(srv, "GET", "/api/pass.png", KID)
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    payload = QrDecoder().decode(img).text
    assert isinstance(codes.parse(payload), codes.Rotating)
    assert app.access.verifier.verify(payload, "door").status == "verified"
    # Somebody else gets no pass, not Sam's; nobody signed in gets nothing.
    assert json.loads(call(srv, "GET", "/api/pass", ADMIN)[1])["has_pass"] is False
    assert call(srv, "GET", "/api/pass.png", ADMIN)[0] == 404
    assert call(srv, "GET", "/api/pass", None)[0] == 403
    assert call(srv, "GET", "/api/pass.png", None)[0] == 403


def test_disabled_pass_is_not_shown(server):
    srv, app, api = server
    h = api.access_person_add({"label": "Sam", "ha_user_id": KID})["handle"]
    api.access_person_update(h, {"enabled": False})
    assert json.loads(call(srv, "GET", "/api/pass", KID)[1])["has_pass"] is False
    assert call(srv, "GET", "/api/pass.png", KID)[0] == 404


def test_pass_when_access_is_off(tmp_path):
    from app.main import App, DEFAULT_OPTIONS
    from test_web import FakeMqtt, fake_source
    app = App(dict(DEFAULT_OPTIONS), mqtt=FakeMqtt(), source_factory=fake_source, data_dir=str(tmp_path))
    app.users = UserDirectory(fetch=lambda: [HaUser(KID, "Sam", False, True)], token="x")
    srv = start_web(app, 0, allow_all=True)
    try:
        assert json.loads(call(srv, "GET", "/api/pass", KID)[1]) == {"available": False, "has_pass": False}
        assert call(srv, "GET", "/api/state", KID)[0] == 403
    finally:
        srv.shutdown()
