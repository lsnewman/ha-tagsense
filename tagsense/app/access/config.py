"""Scanner configuration: /data/access/scanners.json, edited from the panel.

A scanner is a camera (usually a doorbell) that looks for a QR code for a
short window after it is triggered. It has its own source and crop, and does
not share anything with the bin objects, even when the camera is the same.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from ..detector import validate_crop
from ..objects import ID_RE, SOURCES, ConfigError, slugify

RESERVED_IDS = {"availability"}
CAPTURE_MODES = ("stream", "snapshot")
WINDOW_S = (5, 120)
INTERVAL_S = (0.0, 5.0)


@dataclass(frozen=True)
class ScannerConfig:
    id: str
    name: str
    source: str
    go2rtc_stream: str = ""
    camera_entity: str = ""
    fallback: bool = False
    crop_x1: float = 0.0
    crop_y1: float = 0.0
    crop_x2: float = 1.0
    crop_y2: float = 1.0
    window_s: float = 20.0          # how long one trigger keeps scanning
    frame_interval_s: float = 0.3   # pause between frames within the window
    debug_frames: bool = False      # keep raw scan frames (feasibility testing only)
    capture: str = "stream"         # "stream" (go2rtc video, held open) or "snapshot"

    @property
    def crop(self):
        return validate_crop((self.crop_x1, self.crop_y1, self.crop_x2, self.crop_y2))

    @property
    def fallback_source(self) -> str | None:
        if not self.fallback:
            return None
        return "ha_camera" if self.source == "go2rtc" else "go2rtc"

    def to_dict(self) -> dict:
        return asdict(self)


def _bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _num(raw: dict, key: str, default: float, lo: float, hi: float, where: str) -> float:
    try:
        v = float(raw.get(key, default) if raw.get(key) is not None else default)
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: {key} must be a number") from None
    if not lo <= v <= hi:
        raise ConfigError(f"{where}: {key} must be {lo:g}-{hi:g}")
    return v


def parse_scanner(raw: dict, where: str = "scanner") -> ScannerConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected an object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ConfigError(f"{where}: name is required")
    if len(name) > 60:
        raise ConfigError(f"{where}: name is too long (60 characters max)")
    where = f"{where} ({name})"
    sid = str(raw.get("id") or "").strip().lower() or slugify(name)
    if not ID_RE.match(sid) or sid in RESERVED_IDS:
        raise ConfigError(f"{where}: id {sid!r} must be 1-40 of a-z, 0-9, _")
    source = str(raw.get("source") or "go2rtc")
    if source not in SOURCES:
        raise ConfigError(f"{where}: source must be one of {SOURCES}")
    stream = str(raw.get("go2rtc_stream") or "").strip()
    entity = str(raw.get("camera_entity") or "").strip()
    if source == "go2rtc" and not stream:
        raise ConfigError(f"{where}: go2rtc_stream is required for source go2rtc")
    if source == "ha_camera" and not entity:
        raise ConfigError(f"{where}: camera_entity is required for source ha_camera")
    fallback = _bool(raw.get("fallback", False))
    if fallback and not (entity if source == "go2rtc" else stream):
        raise ConfigError(f"{where}: a fallback needs the other source to be set")
    crop = [_num(raw, k, d, 0, 1, where) for k, d in
            (("crop_x1", 0), ("crop_y1", 0), ("crop_x2", 1), ("crop_y2", 1))]
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        raise ConfigError(f"{where}: the crop is empty (x2 must be > x1 and y2 > y1)")
    return ScannerConfig(
        sid, name, source, stream, entity, fallback, *crop,
        window_s=_num(raw, "window_s", 20, *WINDOW_S, where),
        frame_interval_s=_num(raw, "frame_interval_s", 0.3, *INTERVAL_S, where),
        debug_frames=_bool(raw.get("debug_frames", False)),
        capture=_capture(raw, where))


def _capture(raw: dict, where: str) -> str:
    v = str(raw.get("capture") or "stream")
    if v not in CAPTURE_MODES:
        raise ConfigError(f"{where}: capture must be one of {CAPTURE_MODES}")
    return v


def validate_scanners(scanners: list[ScannerConfig]) -> list[ScannerConfig]:
    seen = set()
    for s in scanners:
        if s.id in seen:
            raise ConfigError(f"duplicate scanner id {s.id!r}")
        seen.add(s.id)
    return scanners


class ScannerStore:
    def __init__(self, access_dir: str):
        self.path = os.path.join(access_dir, "scanners.json")

    def load(self) -> list[ScannerConfig]:
        if not os.path.exists(self.path):
            return []
        with open(self.path) as f:
            return validate_scanners([parse_scanner(r, f"scanners[{i}]")
                                      for i, r in enumerate(json.load(f))])

    def save(self, scanners: list[ScannerConfig]):
        validate_scanners(scanners)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            json.dump([s.to_dict() for s in scanners], f, indent=2)
        os.replace(tmp, self.path)
