import json

from app import mqtt_ha as m
from app.settings import Settings, clamp_poll


def configs(obj_id="bin", name="Bin"):
    return dict(m.discovery_configs("1.2.3", obj_id, name))


def test_entities_unique_and_namespaced_per_object():
    cfgs = configs()
    ids = [p["unique_id"] for p in cfgs.values()]
    assert len(ids) == len(set(ids))
    for topic, p in cfgs.items():
        assert topic.startswith("homeassistant/") and "/tagsense_bin/" in topic
        assert p["unique_id"].startswith("tagsense_bin_")
        assert p["device"]["identifiers"] == ["tagsense_bin"]
        assert p["device"]["name"] == "TagSense Bin"
        json.dumps(p)
        for k, v in p.items():
            if k.endswith("_topic"):
                assert v.startswith("tagsense/bin/"), (k, v)


def test_two_objects_do_not_collide():
    a, b = configs("bin", "Bin"), configs("car", "Car")
    assert not set(a) & set(b)
    assert not {p["unique_id"] for p in a.values()} & {p["unique_id"] for p in b.values()}


def test_main_sensor_takes_device_name():
    p = configs()["homeassistant/binary_sensor/tagsense_bin/presence/config"]
    assert p["name"] is None
    assert p["device_class"] == "occupancy"
    assert p["availability_mode"] == "all"
    assert {a["topic"] for a in p["availability"]} == {m.AVAILABILITY, "tagsense/bin/presence/available"}


def test_enum_sensor_options():
    p = configs()["homeassistant/sensor/tagsense_bin/status/config"]
    assert p["device_class"] == "enum"
    assert p["options"] == ["present", "absent", "unknown"]
    assert p["entity_category"] == "diagnostic"


def test_expected_components_and_attribute_topics():
    cfgs = configs()
    comps = {t.split("/")[1] for t in cfgs}
    assert comps == {"binary_sensor", "sensor", "button", "switch", "number", "image"}
    numbers = [p for t, p in cfgs.items() if t.split("/")[1] == "number"]
    assert len(numbers) == 5 and all(p["retain"] is True for p in numbers)
    warn = cfgs["homeassistant/sensor/tagsense_bin/warning/config"]
    assert warn["json_attributes_topic"] == "tagsense/bin/diag/reference/attributes"
    assert all("_attrs" not in p for p in cfgs.values())


def test_legacy_topics_cover_old_entities():
    old = m.legacy_topics()
    assert "homeassistant/binary_sensor/tagsense/bin/config" in old
    assert "homeassistant/sensor/tagsense/status/config" in old
    assert "tagsense/set/poll_interval" in old
    # never clears new-style topics
    new = {t for t, _ in m.discovery_configs("1", "bin", "Bin")}
    assert not new & set(old)


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
    assert back.crop == (0.65, 0.45, 1.0, 1.0)     # x1 == x2 -> default crop
