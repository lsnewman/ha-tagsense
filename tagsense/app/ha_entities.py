"""The entity ids Home Assistant gave an object's entities, for the panel's
"Use in automations" card.

Read from the entity registry (config/entity_registry/list, by unique_id) so a
renamed entity shows its real id. If that cannot be read, the ids Home
Assistant would give by default are shown instead, marked as a guess.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import unicodedata

from .users import ws_command

log = logging.getLogger("tagsense")

CACHE_S = 60.0

# key: (domain, entity name as discovered; None = the device name only)
ENTITIES = {
    "presence": ("binary_sensor", None),
    "rotation": ("sensor", "Rotation"),
    "check_now": ("button", "Check now"),
    "problem": ("binary_sensor", "Tag rejected"),
    "enabled": ("switch", "Enabled"),
}


def slugify(text: str) -> str:
    """Close to Home Assistant's slugify for the usual names."""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


def default_ids(obj_name: str) -> dict[str, str]:
    dev = slugify(f"TagSense {obj_name}")
    return {k: f"{dom}.{dev}" + (f"_{slugify(n)}" if n else "") for k, (dom, n) in ENTITIES.items()}


class EntityDirectory:
    """unique_id -> entity_id for TagSense's MQTT entities; never raises."""

    def __init__(self, fetch=None, token: str | None = None):
        self._token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self._fetch = fetch or self._fetch_ws
        self._lock = threading.Lock()
        self._ids: dict[str, str] = {}
        self._at: float | None = None   # never read yet (not 0: monotonic time can be small after a boot)
        self.error: str | None = None

    def _fetch_ws(self) -> dict[str, str]:
        if not self._token:
            raise RuntimeError("no Supervisor token")
        return {e["unique_id"]: e["entity_id"]
                for e in ws_command(self._token, "config/entity_registry/list") or []
                if e.get("platform") == "mqtt" and str(e.get("unique_id", "")).startswith("tagsense_")}

    def _current(self) -> dict[str, str]:
        with self._lock:
            if self._at is None or time.monotonic() - self._at > CACHE_S:
                try:
                    self._ids, self.error = self._fetch(), None
                except Exception as e:      # noqa: BLE001 - fall back to the default ids
                    self._ids, self.error = {}, f"{type(e).__name__}: {e}"
                    log.debug("entity registry not readable (%s)", self.error)
                self._at = time.monotonic()
            return dict(self._ids)

    def check(self) -> str | None:
        """None if the registry can be read (cached), else why not."""
        self._current()
        return self.error

    def for_object(self, obj_id: str, obj_name: str) -> dict:
        ids = self._current()
        guess = default_ids(obj_name)
        found = {k: ids.get(f"tagsense_{obj_id}_{k}") for k in ENTITIES}
        return {"entities": {k: found[k] or guess[k] for k in ENTITIES},
                "from_registry": all(found.values())}
