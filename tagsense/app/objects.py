"""Object configuration: validation and the /data/objects.json store.

The web UI owns the object list (it can also be exported and imported as text).
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass

ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
RESERVED_IDS = {"availability"}
SOURCES = ("go2rtc", "ha_camera")


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
    fallback: bool = False      # if the main source fails, try the other one

    @property
    def fallback_source(self) -> str | None:
        if not self.fallback:
            return None
        return "ha_camera" if self.source == "go2rtc" else "go2rtc"

    @property
    def camera_key(self) -> tuple:
        """Objects with the same key share frame fetches (and so one worker)."""
        primary = self.go2rtc_stream if self.source == "go2rtc" else self.camera_entity
        if not self.fallback:
            return (self.source, primary)
        backup = self.camera_entity if self.source == "go2rtc" else self.go2rtc_stream
        return (self.source, primary, "fallback", backup)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_one(raw: dict, where: str = "object") -> ObjectConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected an object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ConfigError(f"{where}: name is required")
    if len(name) > 60:
        raise ConfigError(f"{where}: name is too long (60 characters max)")
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
    fallback = raw.get("fallback", False)
    if isinstance(fallback, str):
        fallback = fallback.strip().lower() in ("1", "true", "yes", "on")
    fallback = bool(fallback)
    if fallback and source == "go2rtc" and not entity:
        raise ConfigError(f"{where} ({name}): a fallback needs a camera_entity to fall back to")
    if fallback and source == "ha_camera" and not stream:
        raise ConfigError(f"{where} ({name}): a fallback needs a go2rtc_stream to fall back to")
    return ObjectConfig(oid, name, tag_id, source, stream, entity, fallback)


def validate_list(objs: list[ObjectConfig]) -> list[ObjectConfig]:
    seen_ids, seen_tags = set(), {}
    for o in objs:
        if o.id in seen_ids:
            raise ConfigError(f"duplicate object id {o.id!r}: choose a distinct id or name")
        seen_ids.add(o.id)
        key = (o.camera_key[:2], o.tag_id)
        if key in seen_tags:
            raise ConfigError(f"{o.name} and {seen_tags[key]} use tag {o.tag_id} on the same "
                              "camera; give them different tags")
        seen_tags[key] = o.name
    return objs


def parse_list(raw_list: list) -> list[ObjectConfig]:
    return validate_list([parse_one(r, f"objects[{i}]") for i, r in enumerate(raw_list or [])])


def unique_id(base: str, taken: set[str]) -> str:
    oid, n = base, 2
    while oid in taken:
        suffix = f"_{n}"
        oid, n = base[:40 - len(suffix)] + suffix, n + 1
    return oid


def object_dir(data_dir: str, obj_id: str) -> str:
    d = os.path.join(data_dir, "objects", obj_id)
    os.makedirs(d, exist_ok=True)
    return d


def remove_object_dir(data_dir: str, obj_id: str):
    shutil.rmtree(os.path.join(data_dir, "objects", obj_id), ignore_errors=True)


class ObjectStore:
    """/data/objects.json: the list of objects, edited from the web UI."""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "objects.json")

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def load(self) -> list[ObjectConfig]:
        with open(self.path) as f:
            return parse_list(json.load(f))

    def save(self, objs: list[ObjectConfig]):
        validate_list(objs)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            json.dump([o.to_dict() for o in objs], f, indent=2)
        os.replace(tmp, self.path)

    def load_or_empty(self) -> list[ObjectConfig]:
        return self.load() if self.exists() else []
