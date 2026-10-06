"""Health page and debug bundle: useful, and never a secret in the bundle."""
import base64
import io
import json
import logging
import zipfile

from app.health import day_summary, debug_bundle, health, log_ring, redact
from app.web import Api

from test_access import admin_app, wait_for
from test_web import app  # noqa: F401 - the fixture


def test_redact():
    r = redact({"mqtt_password": "p", "access_mqtt_password": "", "token": "t", "static_key": "k",
                "keys": [{"api_token": "x"}], "key_version": "A", "go2rtc_url": "http://x"})
    assert r["mqtt_password"] == r["token"] == r["static_key"] == "(hidden)"
    assert r["access_mqtt_password"] == "" and r["keys"][0]["api_token"] == "(hidden)"
    assert r["key_version"] == "A" and r["go2rtc_url"] == "http://x"


def test_day_summary():
    now = 100_000.0
    pts = [{"t": now - 90_000, "outcome": "miss", "frames": 5, "valid": 5, "reported": "absent"},   # too old
           {"t": now - 300, "outcome": "hit", "frames": 2, "valid": 2, "reported": "present"},
           {"t": now - 200, "outcome": "failed", "frames": 5, "valid": 0, "reported": "present"},
           {"t": now - 100, "outcome": "miss", "frames": 5, "valid": 3, "reported": "absent"}]
    d = day_summary(pts, now)
    assert d == {"checks": 3, "failed": 1, "failed_pct": 33, "discard_pct": 29, "state_changes": 1}


def test_health_page(app):   # noqa: F811
    assert wait_for(lambda: app.objects["bin"].checklog.points())
    h = Api(app).health()
    assert h["objects"][0]["name"] == "Bin" and h["objects"][0]["day"]["checks"] >= 1
    assert h["cameras"][0]["objects"] == ["Bin"] and h["access"] is None
    logging.getLogger("tagsense").warning("a line for the panel")
    assert any("a line for the panel" in x for x in Api(app).logs()["lines"])


def test_debug_bundle_has_no_secrets(tmp_path):
    app, api = admin_app(tmp_path)
    app.opts.update(mqtt_password="hunter2-main", access_mqtt_password="hunter2-access")
    api.access_create({"name": "Door", "source": "go2rtc", "go2rtc_stream": "d"})
    api.access_person_add({"label": "Sam", "ha_user_id": "u2"})
    api.access_static_issue({"label": "Plumber", "uses": 1})
    codes = app.access.codes
    secrets = ["hunter2-main", "hunter2-access", codes.confirm_token(),
               base64.b64encode(codes.static_key()[1]).decode(),
               *(p.secret for p in codes.people())]
    log_ring()
    logging.getLogger("tagsense").info("bundle test line")
    z = zipfile.ZipFile(io.BytesIO(debug_bundle(app)))
    names = z.namelist()
    assert "info.json" in names and "log.txt" in names and "access/scanners.json" in names
    text = "".join(z.read(n).decode() for n in names)
    for s in secrets:
        assert s not in text, "a secret leaked into the bundle"
    info = json.loads(z.read("info.json"))
    assert info["options"]["mqtt_password"] == "(hidden)" and "globals" in info
    assert health(app)["access"]["scanners"][0]["name"] == "Door"
