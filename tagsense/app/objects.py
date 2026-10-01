"""Object configuration: parsing, validation, legacy migration, data folders."""
from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass

log = logging.getLogger(__name__)

ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
RESERVED_IDS = {"availability"}
SOURCES = ("go2rtc", "ha_camera")
STATE_FILES = ("settings.json", "state.json", "reference.json")


class ConfigError(ValueError):
    pass


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s[:40] or "object"


@dataclass(frozen=True)
class ObjectConfig:
    id: str
    name: str
    tag_id: int
    source: str
    go2rtc_stream: str = ""
    camera_entity: str = ""

    @property
    def camera_key(self) -> tuple[str, str]:
        """Objects with the same key share frame fetches."""
        return (self.source, self.go2rtc_stream if self.source == "go2rtc" else self.camera_entity)


def _one(raw: dict, where: str) -> ObjectConfig:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ConfigError(f"{where}: name is required")
    oid = str(raw.get("id") or "").strip().lower() or slugify(name)
    if not ID_RE.match(oid) or oid in RESERVED_IDS:
        raise ConfigError(f"{where} ({name}): id {oid!r} must be 1-40 of a-z, 0-9, _ "
                          f"and not {sorted(RESERVED_IDS)}")
    try:
        tag_id = int(raw.get("tag_id", 5))
    except (TypeError, ValueError):
        raise ConfigError(f"{where} ({name}): tag_id must be a number") from None
    if not 0 <= tag_id <= 29:
        raise ConfigError(f"{where} ({name}): tag_id must be 0-29 (tag16h5)")
    source = str(raw.get("source") or "go2rtc")
    if source not in SOURCES:
        raise ConfigError(f"{where} ({name}): source must be one of {SOURCES}")
    stream = str(raw.get("go2rtc_stream") or "").strip()
    entity = str(raw.get("camera_entity") or "").strip()
    if source == "go2rtc" and not stream:
        raise ConfigError(f"{where} ({name}): go2rtc_stream is required for source go2rtc")
    if source == "ha_camera" and not entity:
        raise ConfigError(f"{where} ({name}): camera_entity is required for source ha_camera")
    return ObjectConfig(oid, name, tag_id, source, stream, entity)


def parse_objects(opts: dict) -> tuple[list[ObjectConfig], bool]:
    """Objects from options. Returns (objects, from_legacy_flat_options)."""
    raw_list = opts.get("objects") or []
    legacy = False
    if not raw_list:
        if not (opts.get("object_name") or opts.get("go2rtc_stream") or opts.get("camera_entity")):
            raise ConfigError("no objects configured: add at least one entry under 'objects'")
        # 0.2.x single-object options
        raw_list = [{"name": opts.get("object_name") or "Object",
                     "tag_id": opts.get("tag_id", 5),
                     "source": opts.get("source") or "go2rtc",
                     "go2rtc_stream": opts.get("go2rtc_stream", ""),
                     "camera_entity": opts.get("camera_entity", "")}]
        legacy = True
    objs = [_one(r, f"objects[{i}]") for i, r in enumerate(raw_list)]

    seen_ids, seen_tags = set(), {}
    for o in objs:
        if o.id in seen_ids:
            raise ConfigError(f"duplicate object id {o.id!r}: set a distinct 'id' or name")
        seen_ids.add(o.id)
        key = (o.camera_key, o.tag_id)
        if key in seen_tags:
            raise ConfigError(f"{o.name} and {seen_tags[key]} use tag_id {o.tag_id} on the same "
                              "camera; they would always agree, so give them different tags")
        seen_tags[key] = o.name
    return objs, legacy


def object_dir(data_dir: str, obj_id: str) -> str:
    d = os.path.join(data_dir, "objects", obj_id)
    os.makedirs(d, exist_ok=True)
    return d


def migrate_legacy_files(data_dir: str, first: ObjectConfig) -> bool:
    """Move 0.2.x state files from /data into the first object's folder, once."""
    present = [f for f in STATE_FILES if os.path.exists(os.path.join(data_dir, f))]
    if not present:
        return False
    target = object_dir(data_dir, first.id)
    if any(os.path.exists(os.path.join(target, f)) for f in STATE_FILES):
        log.warning("legacy state files found but %s already has state; leaving them", target)
        return False
    for f in present:
        shutil.move(os.path.join(data_dir, f), os.path.join(target, f))
    log.info("migrated %s into objects/%s/", ", ".join(present), first.id)
    return True
