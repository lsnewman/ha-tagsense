import json

import pytest

from app.objects import (ConfigError, ObjectStore, parse_list, parse_one, slugify,
                         unique_id)


def obj(**kw):
    base = {"name": "Bin", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam1"}
    base.update(kw)
    return base


def test_parse_list_and_generated_ids():
    objs = parse_list([obj(), obj(name="Green Waste!", tag_id=7)])
    assert [o.id for o in objs] == ["bin", "green_waste"]
    assert objs[1].tag_id == 7


def test_explicit_id_wins():
    assert parse_one(obj(name="Rubbish bin", id="bin")).id == "bin"


@pytest.mark.parametrize("bad,msg", [
    ({"name": ""}, "name is required"),
    ({"name": "x" * 61}, "too long"),
    ({"id": "Bad Id"}, "id"),
    ({"id": "availability"}, "id"),
    ({"tag_id": 30}, "0-29"),
    ({"tag_id": "five"}, "number"),
    ({"source": "rtsp"}, "source"),
    ({"go2rtc_stream": ""}, "go2rtc_stream is required"),
    ({"source": "ha_camera"}, "camera_entity is required"),
])
def test_validation_errors(bad, msg):
    with pytest.raises(ConfigError, match=msg):
        parse_one(obj(**bad))


def test_duplicate_ids_and_same_tag_on_same_camera_rejected():
    with pytest.raises(ConfigError, match="duplicate object id"):
        parse_list([obj(tag_id=5), obj(tag_id=6)])
    with pytest.raises(ConfigError, match="same camera"):
        parse_list([obj(), obj(name="Other", tag_id=5)])
    parse_list([obj(), obj(name="Other", go2rtc_stream="cam2")])     # other camera: fine


def test_slug_and_unique_id():
    assert slugify("  Recycling Bin #2 ") == "recycling_bin_2"
    assert slugify("!!!") == "object"
    assert unique_id("bin", {"bin", "bin_2"}) == "bin_3"
    assert len(unique_id("x" * 40, {"x" * 40})) == 40


def test_camera_grouping_key():
    a, b, c = parse_list([obj(), obj(name="Rec", tag_id=7), obj(name="Car", go2rtc_stream="cam2")])
    assert a.camera_key == b.camera_key != c.camera_key


def test_fallback():
    o = parse_one(obj(fallback=True, camera_entity="camera.x"))
    assert o.fallback_source == "ha_camera"
    assert o.camera_key == ("go2rtc", "cam1", "fallback", "camera.x")
    assert parse_one(obj()).fallback_source is None
    assert parse_one(obj(fallback="false")).fallback is False
    with pytest.raises(ConfigError, match="camera_entity to fall back to"):
        parse_one(obj(fallback=True))
    with pytest.raises(ConfigError, match="go2rtc_stream to fall back to"):
        parse_one(obj(source="ha_camera", camera_entity="camera.x", go2rtc_stream="", fallback=True))
    # same tag on the same primary camera still conflicts, fallback or not
    with pytest.raises(ConfigError, match="same camera"):
        parse_list([obj(), obj(name="B", fallback=True, camera_entity="camera.x")])


def test_store_save_and_load(tmp_path):
    store = ObjectStore(str(tmp_path))
    assert store.load_or_empty() == []
    store.save(parse_list([obj()]))
    assert json.loads((tmp_path / "objects.json").read_text())[0]["go2rtc_stream"] == "cam1"
    assert [o.id for o in store.load_or_empty()] == ["bin"]


def test_store_empty_and_invalid(tmp_path):
    assert ObjectStore(str(tmp_path)).load_or_empty() == []
    (tmp_path / "objects.json").write_text(json.dumps([obj(), obj()]))
    with pytest.raises(ConfigError):
        ObjectStore(str(tmp_path)).load()
