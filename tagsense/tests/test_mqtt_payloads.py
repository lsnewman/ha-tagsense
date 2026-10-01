import json

from app import mqtt_ha as m
from app.settings import Settings, clamp_poll


def configs():
    return dict(m.discovery_configs("1.2.3"))


def test_all_entities_unique_and_namespaced():
    cfgs = configs()
    ids = [p["unique_id"] for p in cfgs.values()]
    assert len(ids) == len(set(ids))
    for topic, p in cfgs.items():
        assert topic.startswith("homeassistant/") and "/tagsense/" in topic
        assert p["unique_id"].startswith("tagsense_")
        assert p["device"]["identifiers"] == ["tagsense"]
        json.dumps(p)
        for k, v in p.items():
            if k.endswith("_topic"):
                assert v.startswith("tagsense/"), (k, v)


def test_binary_sensor_availability_and_class():
    p = configs()["homeassistant/binary_sensor/tagsense/bin/config"]
    assert p["device_class"] == "occupancy"
    assert p["availability_mode"] == "all"
    assert {a["topic"] for a in p["availability"]} == {m.AVAILABILITY, m.BIN_AVAILABLE}


def test_enum_sensor_options():
    p = configs()["homeassistant/sensor/tagsense/status/config"]
    assert p["device_class"] == "enum"
    assert p["options"] == ["present", "absent", "unknown"]
    assert p["entity_category"] == "diagnostic"


def test_expected_components_present():
    comps = {t.split("/")[1] for t in configs()}
    assert comps == {"binary_sensor", "sensor", "button", "switch", "number", "image"}
    numbers = [t for t in configs() if t.split("/")[1] == "number"]
    assert len(numbers) == 5
    for t in numbers:
        assert configs()[t]["retain"] is True
    diag = [p for t, p in configs().items() if p.get("entity_category") == "diagnostic"]
    assert {"warning", "last_error", "unique_frames", "crop_mean", "crop_std"} <= {
        p["unique_id"].removeprefix("tagsense_") for p in diag}


def test_fmt():
    assert m.fmt(None) == "None"
    assert m.fmt(True) == "ON"
    assert m.fmt(0.123456) == "0.1235"
    assert m.fmt("x" * 400) == "x" * 255


def test_settings_clamping_and_persistence(tmp_path):
    assert [clamp_poll(v) for v in (0, -5, 1, 9, 10, 60, 10**9)] == [0, 0, 10, 10, 10, 60, 86400]
    s = Settings()
    assert s.set("crop_x1", "1.5") and s.crop_x1 == 1.0
    assert not s.set("crop_x1", "garbage")
    assert s.set("enabled", "OFF") and s.enabled is False
    assert not s.set("unknown_key", "1")
    p = str(tmp_path / "settings.json")
    s.save(p)
    back = Settings.load(p)
    assert back == s
    # x1 == x2 -> crop falls back to default
    assert back.crop == (0.65, 0.45, 1.0, 1.0)


def test_object_name_names_main_sensor_only():
    cfgs = dict(m.discovery_configs("1", "Table"))
    p = cfgs["homeassistant/binary_sensor/tagsense/bin/config"]
    assert p["name"] == "Table" and p["unique_id"] == "tagsense_bin"
    assert p["device"]["name"] == "TagSense"
