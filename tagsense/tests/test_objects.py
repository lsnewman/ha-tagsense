import json

import pytest

from app.main import camera_key
from app.objects import ConfigError, migrate_legacy_files, parse_objects, slugify


def obj(**kw):
    base = {"name": "Bin", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam1"}
    base.update(kw)
    return base


def test_parse_list_and_generated_ids():
    objs, legacy = parse_objects({"objects": [obj(), obj(name="Green Waste!", tag_id=7)]})
    assert not legacy
    assert [o.id for o in objs] == ["bin", "green_waste"]
    assert objs[1].tag_id == 7


def test_explicit_id_wins():
    objs, _ = parse_objects({"objects": [obj(name="Rubbish bin", id="bin")]})
    assert objs[0].id == "bin" and objs[0].name == "Rubbish bin"


@pytest.mark.parametrize("bad,msg", [
    ({"name": ""}, "name is required"),
    ({"id": "Bad Id"}, "id"),
    ({"id": "availability"}, "id"),
    ({"tag_id": 30}, "0-29"),
    ({"source": "rtsp"}, "source"),
    ({"go2rtc_stream": ""}, "go2rtc_stream is required"),
    ({"source": "ha_camera"}, "camera_entity is required"),
])
def test_validation_errors(bad, msg):
    with pytest.raises(ConfigError, match=msg):
        parse_objects({"objects": [obj(**bad)]})


def test_duplicate_ids_and_same_tag_on_same_camera_rejected():
    with pytest.raises(ConfigError, match="duplicate object id"):
        parse_objects({"objects": [obj(tag_id=5), obj(tag_id=6)]})
    with pytest.raises(ConfigError, match="same camera"):
        parse_objects({"objects": [obj(), obj(name="Other", tag_id=5)]})
    # same tag on a different camera is fine
    parse_objects({"objects": [obj(), obj(name="Other", go2rtc_stream="cam2")]})


def test_legacy_flat_options_become_one_object():
    objs, legacy = parse_objects({"objects": [], "object_name": "Bin", "tag_id": 5,
                                  "source": "go2rtc", "go2rtc_stream": "car_port_high"})
    assert legacy and len(objs) == 1
    assert (objs[0].id, objs[0].go2rtc_stream) == ("bin", "car_port_high")


def test_nothing_configured_is_an_error():
    with pytest.raises(ConfigError, match="no objects"):
        parse_objects({"objects": []})


def test_slugify():
    assert slugify("  Recycling Bin #2 ") == "recycling_bin_2"
    assert slugify("!!!") == "object"


def test_camera_grouping_key():
    a, b, c = parse_objects({"objects": [obj(), obj(name="Rec", tag_id=7),
                                         obj(name="Car", tag_id=5, go2rtc_stream="cam2")]})[0]
    assert camera_key(a, "none") == camera_key(b, "none") != camera_key(c, "none")


def test_migrate_legacy_files(tmp_path):
    for f, body in (("state.json", {"state": "present"}), ("settings.json", {"poll_interval": 30})):
        (tmp_path / f).write_text(json.dumps(body))
    first = parse_objects({"objects": [obj()]})[0][0]
    assert migrate_legacy_files(str(tmp_path), first)
    target = tmp_path / "objects" / "bin"
    assert json.loads((target / "state.json").read_text()) == {"state": "present"}
    assert not (tmp_path / "state.json").exists()
    assert not migrate_legacy_files(str(tmp_path), first)     # idempotent
