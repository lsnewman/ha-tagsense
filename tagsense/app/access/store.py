"""The access module's secrets and records in /data/access. Only this module
touches these files.

  keys.json     the static-code signing key and its version
  people.json   people with rotating passes (secret, HA user, last step used)
  static.json   issued static codes (label, validity, uses left, revoked)
  state.json    per-scanner failure times and lockouts

Every file is mode 0600 and written atomically (tmp, fsync, rename). A write
that fails raises, so the verifier can fail closed: a code is only reported
verified after its use has been recorded.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import asdict, dataclass, field

from . import codes


class StoreError(Exception):
    pass


@dataclass
class Person:
    handle: str
    label: str
    ha_user_id: str
    secret: str                    # base64
    v: str = "A"                   # secret version; re-enrolling bumps it
    enabled: bool = True
    last_step: int = -1            # highest step accepted (replay protection)
    created: float = 0.0

    @property
    def key(self) -> bytes:
        return base64.b64decode(self.secret)

    def public(self) -> dict:
        return {"handle": self.handle, "label": self.label, "ha_user_id": self.ha_user_id,
                "enabled": self.enabled, "created": self.created}


@dataclass
class StaticRecord:
    code_id: str
    label: str
    v: str                          # static key version it was signed with
    expiry_min: int
    valid_from: float | None = None  # unix; None = straight away
    uses_total: int | None = None    # None = unlimited until expiry
    uses_left: int | None = None
    revoked: bool = False
    created: float = 0.0
    used_at: list[float] = field(default_factory=list)

    @property
    def expires(self) -> int:
        return codes.unix_of(self.expiry_min)


def _write_json(path: str, data):
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError as e:
        raise StoreError(f"could not write {os.path.basename(path)}: {e}") from e


def _read_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        raise StoreError(f"could not read {os.path.basename(path)}: {e}") from e


class AccessStore:
    def __init__(self, access_dir: str):
        self.dir = access_dir
        os.makedirs(access_dir, mode=0o700, exist_ok=True)
        self.lock = threading.RLock()

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    # --- static signing key ---------------------------------------------------------

    def static_key(self) -> tuple[str, bytes]:
        """(version, key); created on first use."""
        with self.lock:
            k = _read_json(self._path("keys.json"), None)
            if not k:
                k = {"static": {"v": "A", "key": base64.b64encode(codes.new_secret(32)).decode()}}
                _write_json(self._path("keys.json"), k)
            return k["static"]["v"], base64.b64decode(k["static"]["key"])

    def rotate_static_key(self) -> str:
        with self.lock:
            v, _ = self.static_key()
            nv = codes.next_version(v)
            _write_json(self._path("keys.json"), {"static": {
                "v": nv, "key": base64.b64encode(codes.new_secret(32)).decode()}})
            return nv

    # --- people -------------------------------------------------------------------------

    def people(self) -> list[Person]:
        with self.lock:
            return [Person(**p) for p in _read_json(self._path("people.json"), [])]

    def save_people(self, people: list[Person]):
        with self.lock:
            _write_json(self._path("people.json"), [asdict(p) for p in people])

    def person(self, handle: str) -> Person | None:
        return next((p for p in self.people() if p.handle == handle), None)

    def update_person(self, person: Person):
        with self.lock:
            people = [person if p.handle == person.handle else p for p in self.people()]
            self.save_people(people)

    # --- static codes ----------------------------------------------------------------------

    def static_codes(self) -> list[StaticRecord]:
        with self.lock:
            return [StaticRecord(**r) for r in _read_json(self._path("static.json"), [])]

    def save_static(self, records: list[StaticRecord]):
        with self.lock:
            _write_json(self._path("static.json"), [asdict(r) for r in records])

    def static_record(self, code_id: str) -> StaticRecord | None:
        return next((r for r in self.static_codes() if r.code_id == code_id), None)

    def update_static(self, record: StaticRecord):
        with self.lock:
            self.save_static([record if r.code_id == record.code_id else r
                              for r in self.static_codes()])

    # --- per-scanner state ----------------------------------------------------------------

    def state(self) -> dict:
        with self.lock:
            return _read_json(self._path("state.json"), {})

    def save_state(self, state: dict):
        with self.lock:
            _write_json(self._path("state.json"), state)
