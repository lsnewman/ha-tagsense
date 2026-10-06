"""Global detection options, set in the panel (Settings) and kept in /data/globals.json.

The same keys are still app options, but those only seed globals.json the first
time (so an upgrade keeps what was set on the Configuration tab). After that the
panel's values win. Loading never fails: a bad or missing value falls back to
the default, so a setting can never stop the app from starting.
"""
from __future__ import annotations

import json
import logging
import os

from .objects import ConfigError

log = logging.getLogger("tagsense")

LOG_LEVELS = ("debug", "info", "warning", "error")

# key: (type, min, max, default)
SPEC = {
    "max_aspect": (float, 0.0, 20.0, 2.0),
    "min_size_ratio": (float, 0.0, 1.0, 0.5),
    "burst_size": (int, 1, 20, 5),
    "burst_interval_s": (float, 0.0, 10.0, 1.0),
    "present_min_hits": (int, 1, 3, 2),
    "absent_checks": (int, 1, 20, 3),
    "confirm_delay_s": (int, 5, 600, 45),
    "unknown_after_failures": (int, 1, 5, 2),
    "sanity_min_ratio": (float, 0.0, 1.0, 0.2),
    "sanity_min_h": (float, 0.0, 10.0, 0.02),
}
KEYS = (*SPEC, "log_level")
DEFAULTS = {**{k: v[3] for k, v in SPEC.items()}, "log_level": "info"}


def _one(key: str, value):
    """Check one value; raise ConfigError with a message for the panel."""
    if key == "log_level":
        v = str(value).strip().lower()
        if v not in LOG_LEVELS:
            raise ConfigError(f"log_level must be one of {', '.join(LOG_LEVELS)}")
        return v
    typ, lo, hi, _ = SPEC[key]
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{key} must be a number") from None
    if typ is int and f != int(f):
        raise ConfigError(f"{key} must be a whole number")
    if not lo <= f <= hi:
        raise ConfigError(f"{key} must be {lo:g}-{hi:g}")
    return int(f) if typ is int else round(f, 4)


def validate(values: dict) -> dict:
    """A complete, checked set of globals; raises ConfigError on the first problem."""
    if unknown := set(values) - set(KEYS):
        raise ConfigError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    out = {k: _one(k, values.get(k, DEFAULTS[k])) for k in KEYS}
    if out["present_min_hits"] > out["burst_size"]:
        raise ConfigError("Hits needed for present cannot be more than frames per check")
    return out


def tolerant(values: dict) -> dict:
    """Like validate, but a bad value falls back to the default (used at startup)."""
    out = {}
    for k in KEYS:
        try:
            out[k] = _one(k, values.get(k, DEFAULTS[k]))
        except ConfigError as e:
            log.warning("setting %s ignored (%s); using %s", k, e, DEFAULTS[k])
            out[k] = DEFAULTS[k]
    if out["present_min_hits"] > out["burst_size"]:
        log.warning("present_min_hits %s > burst_size %s: clamping",
                    out["present_min_hits"], out["burst_size"])
        out["present_min_hits"] = out["burst_size"]
    return out


def path_in(data_dir: str) -> str:
    return os.path.join(data_dir, "globals.json")


def load(data_dir: str, seed: dict) -> tuple[dict, bool]:
    """(globals, seeded). With no globals.json yet, seed it from the app options."""
    p = path_in(data_dir)
    try:
        with open(p) as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            return tolerant(raw), False
        log.warning("globals.json is not an object; seeding from the app options")
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        log.warning("could not read globals.json (%s); seeding from the app options", e)
    values = tolerant({k: seed[k] for k in KEYS if seed.get(k) is not None})
    try:
        save(data_dir, values)
    except OSError as e:
        log.warning("could not write globals.json: %s", e)
    return values, True


def save(data_dir: str, values: dict):
    p = path_in(data_dir)
    tmp = f"{p}.tmp"
    with open(tmp, "w") as f:
        json.dump(values, f, indent=1)
    os.replace(tmp, p)
