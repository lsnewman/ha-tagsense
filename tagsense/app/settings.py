"""Runtime-tunable settings (MQTT number/switch entities), persisted in /data."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from .detector import DEFAULT_CROP, validate_crop
from .rotation import DEFAULT_STEPS, clamp_steps

MIN_POLL_S = 10
MAX_POLL_S = 86400


def clamp_poll(v) -> int:
    v = int(round(float(v)))
    if v <= 0:
        return 0
    return min(MAX_POLL_S, max(MIN_POLL_S, v))


@dataclass
class Settings:
    poll_interval: int = 60
    crop_x1: float = DEFAULT_CROP[0]
    crop_y1: float = DEFAULT_CROP[1]
    crop_x2: float = DEFAULT_CROP[2]
    crop_y2: float = DEFAULT_CROP[3]
    enabled: bool = True
    rotation_steps: int = DEFAULT_STEPS

    @property
    def crop(self):
        return validate_crop((self.crop_x1, self.crop_y1, self.crop_x2, self.crop_y2))

    def set(self, key: str, value) -> bool:
        """Apply a value from MQTT. Returns True if it was accepted and changed."""
        if key not in self.__dataclass_fields__:
            return False
        old = getattr(self, key)
        try:
            if key == "poll_interval":
                new = clamp_poll(value)
            elif key == "enabled":
                new = str(value).strip().upper() in ("ON", "TRUE", "1")
            elif key == "rotation_steps":
                new = clamp_steps(value)
            else:
                new = round(min(1.0, max(0.0, float(value))), 4)
        except (TypeError, ValueError, OverflowError):
            return False
        setattr(self, key, new)
        return new != old

    def save(self, path: str):
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(asdict(self), f)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "Settings":
        s = cls()
        try:
            with open(path) as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return s
        for k, v in raw.items():
            s.set(k, "ON" if v is True else "OFF" if v is False else v)
        return s
