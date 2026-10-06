"""Entity ids for the panel's automation examples."""
import time

import pytest

from app.ha_entities import EntityDirectory, default_ids, slugify
from app.web import Api, ApiError

from test_web import app  # noqa: F401 - the fixture


def test_default_ids_follow_home_assistant():
    ids = default_ids("Green bin")
    assert ids["presence"] == "binary_sensor.tagsense_green_bin"
    assert ids["rotation"] == "sensor.tagsense_green_bin_rotation"
    assert ids["check_now"] == "button.tagsense_green_bin_check_now"
    assert ids["problem"] == "binary_sensor.tagsense_green_bin_tag_rejected"
    assert slugify("Café chair!") == "cafe_chair"


def test_registry_wins_and_failures_fall_back(monkeypatch):
    calls = []

    def fetch():
        calls.append(1)
        return {"tagsense_bin_presence": "binary_sensor.wheelie_bin",
                "tagsense_bin_rotation": "sensor.tagsense_bin_rotation",
                "tagsense_bin_check_now": "button.tagsense_bin_check_now",
                "tagsense_bin_problem": "binary_sensor.tagsense_bin_tag_rejected",
                "tagsense_bin_enabled": "switch.tagsense_bin_enabled"}
    d = EntityDirectory(fetch=fetch)
    monkeypatch.setattr(time, "monotonic", lambda: 5.0)       # just after a boot
    r = d.for_object("bin", "Bin")
    assert r["from_registry"] and r["entities"]["presence"] == "binary_sensor.wheelie_bin"
    d.for_object("bin", "Bin")
    assert len(calls) == 1                                     # cached

    def broken():
        raise OSError("no route")
    r = EntityDirectory(fetch=broken).for_object("car", "Car")
    assert not r["from_registry"] and r["entities"]["presence"] == "binary_sensor.tagsense_car"
    assert not EntityDirectory(token="").for_object("car", "Car")["from_registry"]   # no token: no call


def test_entities_endpoint(app):   # noqa: F811
    api = Api(app)
    app.entities = EntityDirectory(token="")
    assert api.entities("bin")["entities"]["presence"] == "binary_sensor.tagsense_bin"
    with pytest.raises(ApiError):
        api.entities("nope")
