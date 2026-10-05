"""The one place a code is judged. Fails closed: anything unexpected (an
unreadable store, a failed write, an exception) gives `unavailable`, never
`verified`. A code is reported verified only after its use is recorded.

Order of checks:
  1. clock sanity (before 2025 = not set)       -> unavailable
  2. scanner locked out                          -> locked_out (payload not parsed)
  3. not a TagSense code                         -> unrecognised (no lockout count)
  4. rotating: handle, MAC for step/step-1, replay (step <= last used)
  5. static: key version, MAC, record (revoked, not yet valid, expired, used up)
"""
from __future__ import annotations

import hmac
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import codes
from .store import AccessStore

log = logging.getLogger("tagsense.access")

CLOCK_MIN = codes.EPOCH                 # 2025-01-01: an unset clock reads earlier
DEFAULT_PERIOD = 30
LOCK_WINDOW_BAD = 3                     # more than this in one scan window...
LOCK_RECENT_BAD = 5                     # ...or more than this in LOCK_RECENT_S
LOCK_RECENT_S = 600
LOCK_FOR_S = 900

VERIFIED = "verified"
BAD = ("invalid", "expired", "replayed", "revoked")        # count towards lockout


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


@dataclass
class Result:
    status: str
    code_type: str | None = None          # "rotating" | "static"
    attrs: dict = field(default_factory=dict)

    @property
    def bad(self) -> bool:
        return self.status in BAD


class Verifier:
    def __init__(self, store: AccessStore, period: int = DEFAULT_PERIOD, clock=time.time):
        self.store = store
        self.period = int(period)
        self.clock = clock

    # --- lockout -------------------------------------------------------------------------

    def locked_until(self, scanner: str, now: float | None = None) -> float | None:
        now = self.clock() if now is None else now
        until = self.store.state().get(scanner, {}).get("locked_until") or 0
        return until if until > now else None

    def register_bad(self, scanner: str, window_bad: int, now: float | None = None) -> bool:
        """Record one bad scan. Returns True if it has just locked the scanner out."""
        now = self.clock() if now is None else now
        with self.store.lock:
            state = self.store.state()
            s = state.setdefault(scanner, {})
            fails = [t for t in s.get("fails", []) if now - t < LOCK_RECENT_S] + [now]
            s["fails"] = fails
            locked = window_bad > LOCK_WINDOW_BAD or len(fails) > LOCK_RECENT_BAD
            if locked and not (s.get("locked_until", 0) > now):
                s["locked_until"] = now + LOCK_FOR_S
            else:
                locked = False
            self.store.save_state(state)
            return locked

    def clear_lockout(self, scanner: str):
        with self.store.lock:
            state = self.store.state()
            state.pop(scanner, None)
            self.store.save_state(state)

    # --- verification ----------------------------------------------------------------------

    def verify(self, payload: str, scanner: str, now: float | None = None) -> Result:
        now = self.clock() if now is None else now
        try:
            with self.store.lock:
                return self._verify(payload, scanner, now)
        except Exception as e:            # noqa: BLE001 - fail closed; never log the payload
            log.error("access [%s]: verification unavailable: %s", scanner, type(e).__name__)
            return Result("unavailable", attrs={"reason": type(e).__name__})

    def _verify(self, payload: str, scanner: str, now: float) -> Result:
        if now < CLOCK_MIN:
            return Result("unavailable", attrs={"reason": "clock not set"})
        if until := self.locked_until(scanner, now):
            return Result("locked_out", attrs={"locked_until": iso(until)})
        code = codes.parse(payload)
        if isinstance(code, codes.Rotating):
            return self._rotating(code, now)
        if isinstance(code, codes.Static):
            return self._static(code, now)
        return Result("unrecognised")

    def _rotating(self, c: codes.Rotating, now: float) -> Result:
        person = self.store.person(c.handle)
        if person is None or not person.enabled or person.v != c.v:
            return Result("invalid", "rotating")
        step_now = int(now // self.period)
        matched = None
        for step in (step_now, step_now - 1):
            if hmac.compare_digest(codes.rotating_mac(person.key, c.v, c.handle, step), c.mac):
                matched = step
                break
        if matched is None:
            return Result("invalid", "rotating")
        attrs = {"label": person.label, "code_type": "rotating", "code_id": person.handle,
                 "expires": iso((matched + 2) * self.period), "valid_from": None,
                 "uses_left": None}
        if matched <= person.last_step:
            return Result("replayed", "rotating", attrs)
        person.last_step = matched
        self.store.update_person(person)          # raises if it cannot be recorded
        return Result(VERIFIED, "rotating", attrs)

    def _static(self, c: codes.Static, now: float) -> Result:
        v, key = self.store.static_key()
        if c.v != v or not hmac.compare_digest(
                codes.static_mac(key, c.v, c.code_id, c.expiry_min), c.mac):
            return Result("invalid", "static")
        rec = self.store.static_record(c.code_id)
        if rec is None or rec.revoked or rec.v != c.v or rec.expiry_min != c.expiry_min:
            return Result("revoked", "static", {"code_type": "static", "code_id": c.code_id,
                                                "label": rec.label if rec else None})
        attrs = {"label": rec.label, "code_type": "static", "code_id": rec.code_id,
                 "expires": iso(rec.expires), "valid_from": iso(rec.valid_from),
                 "uses_left": rec.uses_left}
        if rec.valid_from and now < rec.valid_from:
            return Result("not_yet_valid", "static", attrs)
        if now >= rec.expires:
            return Result("expired", "static", attrs)
        if rec.uses_left is not None and rec.uses_left <= 0:
            return Result("replayed", "static", attrs)
        if rec.uses_left is not None:
            rec.uses_left -= 1
        rec.used_at.append(now)
        self.store.update_static(rec)              # raises if it cannot be recorded
        attrs["uses_left"] = rec.uses_left
        return Result(VERIFIED, "static", attrs)
