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
    assert len(numbers) == 6 and all(p["retain"] is True for p in numbers)
    warn = cfgs["homeassistant/sensor/tagsense_bin/warning/config"]
    assert warn["json_attributes_topic"] == "tagsense/bin/diag/reference/attributes"
    assert all("_attrs" not in p for p in cfgs.values())


def test_remove_clears_discovery_and_retained_topics():
    class C:
        version = "1"
        msgs = []

        def pub(self, t, p, retain=True):
            self.msgs.append((t, p))
    c = C()
    m.ObjectPublisher(c, "bin", "Bin").remove()
    cleared = {t for t, p in c.msgs if p == b""}
    assert {t for t, _ in m.discovery_configs("1", "bin", "Bin")} <= cleared
    assert {"tagsense/bin/presence", "tagsense/bin/set/crop_x1", "tagsense/bin/setting/enabled",
            "tagsense/bin/rotation", "tagsense/bin/rotation/available", "tagsense/bin/set/rotation_steps",
            "tagsense/bin/cmd/set_orientation"} <= cleared
    assert all(t.startswith(("tagsense/bin/", "homeassistant/")) for t in cleared)


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


class _Client:
    version = "t"

    def __init__(self):
        self.msgs = []

    def pub(self, topic, payload, retain=True):
        self.msgs.append((topic, payload))


def test_last_check_sensor_retired_and_new_entities():
    cfgs = configs()
    assert "homeassistant/sensor/tagsense_bin/last_check/config" not in cfgs
    assert cfgs["homeassistant/sensor/tagsense_bin/tag_aspect/config"]["state_class"] == "measurement"
    p = cfgs["homeassistant/binary_sensor/tagsense_bin/problem/config"]
    assert p["device_class"] == "problem" and p["state_topic"] == "tagsense/bin/problem"
    assert cfgs["homeassistant/button/tagsense_bin/reset_reference/config"]["command_topic"] == \
        "tagsense/bin/cmd/reset_reference"
    c = _Client()
    m.ObjectPublisher(c, "bin", "Bin").publish_discovery()
    assert ("homeassistant/sensor/tagsense_bin/last_check/config", b"") in c.msgs
    assert ("tagsense/bin/diag/last_check", b"") in c.msgs


def test_commands_routed():
    calls = []
    client = m.MqttClient.__new__(m.MqttClient)
    client._on_command = lambda oid, cmd: calls.append((oid, cmd))
    client._on_setting = lambda *a: calls.append(a)

    class Msg:
        def __init__(self, topic, payload=b"PRESS"):
            self.topic, self.payload = topic, payload

    for t in ("tagsense/bin/cmd/check_now", "tagsense/bin/cmd/reset_reference",
              "tagsense/bin/cmd/set_orientation", "tagsense/bin/cmd/nope"):
        client._handle_message(None, None, Msg(t))
    client._handle_message(None, None, Msg("tagsense/bin/set/rotation_steps", b"8"))
    assert calls == [("bin", "check_now"), ("bin", "reset_reference"), ("bin", "set_orientation"),
                     ("bin", "rotation_steps", "8")]


def test_rotation_entities_follow_presence():
    cfgs = configs()
    sensor = cfgs["homeassistant/sensor/tagsense_bin/rotation/config"]
    assert sensor["unit_of_measurement"] == "°" and sensor["state_class"] == "measurement"
    assert sensor["json_attributes_topic"] == "tagsense/bin/rotation/attributes"
    for key in ("sensor/tagsense_bin/rotation", "number/tagsense_bin/rotation_steps",
                "button/tagsense_bin/set_orientation"):
        p = cfgs[f"homeassistant/{key}/config"]
        assert p["availability_mode"] == "all"
        assert {a["topic"] for a in p["availability"]} == {m.AVAILABILITY, "tagsense/bin/rotation/available"}
    steps = cfgs["homeassistant/number/tagsense_bin/rotation_steps/config"]
    assert (steps["min"], steps["max"], steps["step"], steps["entity_category"]) == (1, 36, 1, "config")
    c = _Client()
    pub = m.ObjectPublisher(c, "bin", "Bin")
    pub.publish_rotation(False, 180, 178.6, 4)
    assert c.msgs == [("tagsense/bin/rotation/available", "offline")]  # value kept, not overwritten
    pub.publish_rotation(True, 180, 178.6, 4)
    assert c.msgs[1:] == [("tagsense/bin/rotation/attributes", '{"angle": 178.6, "steps": 4}'),
                          ("tagsense/bin/rotation", "180"), ("tagsense/bin/rotation/available", "online")]


def test_rotation_steps_setting():
    s = Settings()
    assert s.rotation_steps == 4
    assert s.set("rotation_steps", "8") and s.rotation_steps == 8
    assert s.set("rotation_steps", "0") and s.rotation_steps == 1
    assert s.set("rotation_steps", "1000") and s.rotation_steps == 36
    assert not s.set("rotation_steps", "inf") and not s.set("rotation_steps", "x")
