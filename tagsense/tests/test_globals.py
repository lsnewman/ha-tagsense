"""Detection globals: seeded from the app options once, then set in the panel."""
import json
import logging

import pytest

from app import globals as gl
from app.main import App, DEFAULT_OPTIONS
from app.objects import ConfigError
from app.web import Api, ApiError

from test_web import FakeMqtt, app, fake_source  # noqa: F401 - the fixture


def test_validate():
    v = gl.validate({"max_aspect": "2.5", "burst_size": 3.0, "log_level": "DEBUG"})
    assert v["max_aspect"] == 2.5 and v["burst_size"] == 3 and v["log_level"] == "debug"
    assert v["absent_checks"] == gl.DEFAULTS["absent_checks"]
    for bad, msg in (({"burst_size": 2.5}, "whole number"), ({"burst_size": 0}, "1-20"),
                     ({"max_aspect": "x"}, "a number"), ({"log_level": "loud"}, "log_level"),
                     ({"bogus": 1}, "unknown"),
                     ({"burst_size": 2, "present_min_hits": 3}, "more than frames per check")):
        with pytest.raises(ConfigError, match=msg):
            gl.validate(bad)


def test_tolerant_never_fails():
    v = gl.tolerant({"max_aspect": 99, "burst_size": 2, "present_min_hits": 3, "log_level": "?"})
    assert v["max_aspect"] == gl.DEFAULTS["max_aspect"] and v["log_level"] == "info"
    assert v["present_min_hits"] == 2                       # clamped to the burst


def test_seeded_once_from_options(tmp_path):
    opts = dict(DEFAULT_OPTIONS, max_aspect=2.5, sanity_min_ratio=0.5)
    v, seeded = gl.load(str(tmp_path), opts)
    assert seeded and v["max_aspect"] == 2.5 and v["sanity_min_ratio"] == 0.5
    assert json.loads((tmp_path / "globals.json").read_text())["max_aspect"] == 2.5
    # Later, the options no longer matter: the panel's file wins.
    v, seeded = gl.load(str(tmp_path), dict(DEFAULT_OPTIONS, max_aspect=3.0))
    assert not seeded and v["max_aspect"] == 2.5


def test_bad_file_does_not_stop_startup(tmp_path):
    (tmp_path / "globals.json").write_text("{not json")
    a = App(dict(DEFAULT_OPTIONS, go2rtc_url="http://x"), mqtt=FakeMqtt(),
            source_factory=fake_source, data_dir=str(tmp_path))
    assert a.globals == gl.DEFAULTS


def test_set_globals_live(app, tmp_path):        # noqa: F811
    api = Api(app)
    assert api.globals_get()["values"]["burst_size"] == 2          # seeded from the fixture's options
    r = api.globals_set({"max_aspect": 3, "burst_size": 4, "sanity_min_ratio": 0.1})
    assert r["values"]["max_aspect"] == 3.0
    assert app.tuning.max_aspect == 3.0 and app.tuning.sanity_min_ratio == 0.1
    o = app.objects["bin"]
    assert o.detector.max_aspect == 3.0 and o.tuning is app.tuning       # rebuilt objects
    assert next(iter(app.cameras.values())).burst_size == 4
    assert json.loads((tmp_path / "globals.json").read_text())["burst_size"] == 4
    before = dict(app.globals)
    with pytest.raises(ApiError, match="more than frames per check"):
        api.globals_set({"present_min_hits": 3, "burst_size": 1})
    assert app.globals == before                                    # nothing changed


def test_log_level_only_does_not_rebuild(app):    # noqa: F811
    o = app.objects["bin"]
    root = logging.getLogger()
    level = root.level
    try:
        Api(app).globals_set({"log_level": "debug"})
        assert root.level == logging.DEBUG and app.objects["bin"] is o
    finally:
        root.setLevel(level)


def test_globals_get_has_no_secrets(tmp_path):
    opts = dict(DEFAULT_OPTIONS, go2rtc_url="http://x", mqtt_host="broker", mqtt_password="s3cret-a",
                access_mqtt_username="tagsense_access", access_mqtt_password="s3cret-b")
    a = App(opts, mqtt=FakeMqtt(), source_factory=fake_source, data_dir=str(tmp_path))
    text = json.dumps(Api(a).globals_get())
    assert "s3cret" not in text and "broker:1883" in text


def test_export_import_carries_globals(app):      # noqa: F811
    api = Api(app)
    api.globals_set({"absent_checks": 5})
    exported = api.export()
    assert exported["globals"]["absent_checks"] == 5
    api.globals_set({"absent_checks": 3})
    r = api.import_({"text": json.dumps(exported)})
    assert r["globals_changed"] and app.globals["absent_checks"] == 5
    # A globals-only import works; a bad one changes nothing.
    assert api.import_({"globals": {"absent_checks": 4}})["globals_changed"]
    with pytest.raises(ApiError, match="globals: absent_checks"):
        api.import_({"objects": exported["objects"], "globals": {"absent_checks": 99}})
    assert app.globals["absent_checks"] == 4


def test_options_only_seed_and_are_optional(tmp_path):
    """config.yaml: no defaults for the moved keys (fresh installs never see them),
    but still in the schema, as optional, so existing values can be copied once."""
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
    for k in gl.KEYS:
        assert k not in cfg["options"], k
        assert str(cfg["schema"][k]).endswith("?"), k
    # An install that never saved them seeds the defaults.
    v, seeded = gl.load(str(tmp_path), {"go2rtc_url": "", "access_enabled": False})
    assert seeded and v == gl.DEFAULTS
