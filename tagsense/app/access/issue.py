"""Issuing codes and managing people (called from the admin panel only).

Static-code rules (decided 2026-10-04):
  - an expiry and/or a number of uses is required (at least one);
  - with no expiry, a backstop expiry applies: 90 days by default, adjustable,
    at most 365 days;
  - an optional start time (valid_from) lets a code be issued in advance;
  - the code is valid for at least 10 minutes.
"""
from __future__ import annotations

import base64
import time

from . import codes
from .store import AccessStore, Person, StaticRecord

BACKSTOP_DAYS = 90
MAX_DAYS = 365
MIN_VALID_S = 600
MAX_USES = 1000


class IssueError(ValueError):
    pass


# --- static codes -----------------------------------------------------------------

def issue_static(store: AccessStore, label: str, expires: float | None = None,
                 uses: int | None = None, valid_from: float | None = None,
                 backstop_days: int = BACKSTOP_DAYS, now: float | None = None
                 ) -> tuple[StaticRecord, str]:
    now = time.time() if now is None else now
    label = (label or "").strip()
    if not label or len(label) > 60:
        raise IssueError("a label of 1-60 characters is required")
    if expires is None and not uses:
        raise IssueError("set an expiry, a number of uses, or both")
    if uses is not None and not 1 <= int(uses) <= MAX_USES:
        raise IssueError(f"uses must be 1-{MAX_USES}")
    start = max(now, valid_from) if valid_from else now
    if valid_from and valid_from > now + MAX_DAYS * 86400:
        raise IssueError(f"the start must be within {MAX_DAYS} days")
    if expires is None:
        if not 1 <= int(backstop_days) <= MAX_DAYS:
            raise IssueError(f"the backstop expiry must be 1-{MAX_DAYS} days")
        expires = start + int(backstop_days) * 86400
    if expires - start < MIN_VALID_S:
        raise IssueError("the code must be valid for at least 10 minutes after it starts")
    if expires > now + (MAX_DAYS + 1) * 86400:
        raise IssueError(f"the expiry must be within {MAX_DAYS} days")
    v, _ = store.static_key()
    with store.lock:
        taken = {r.code_id for r in store.static_codes()}
        code_id = codes.new_id(8)
        while code_id in taken:
            code_id = codes.new_id(8)
        expiry_min = -(-int(expires - codes.EPOCH) // 60)       # round up to a minute
        rec = StaticRecord(code_id, label, v, expiry_min, valid_from=valid_from,
                           uses_total=int(uses) if uses else None,
                           uses_left=int(uses) if uses else None, created=now)
        store.save_static(store.static_codes() + [rec])
    return rec, static_payload(store, rec)


def static_payload(store: AccessStore, rec: StaticRecord) -> str:
    v, key = store.static_key()
    if rec.v != v:
        raise IssueError("this code was signed with a key that has since been rotated")
    return codes.static_code(key, rec.v, rec.code_id, rec.expiry_min)


def static_status(rec: StaticRecord, now: float | None = None) -> str:
    now = time.time() if now is None else now
    if rec.revoked:
        return "revoked"
    if now >= rec.expires:
        return "expired"
    if rec.uses_left is not None and rec.uses_left <= 0:
        return "used"
    if rec.valid_from and now < rec.valid_from:
        return "not yet valid"
    return "active"


def revoke_static(store: AccessStore, code_id: str):
    with store.lock:
        rec = store.static_record(code_id)
        if rec is None:
            raise IssueError(f"no code {code_id!r}")
        rec.revoked = True
        store.update_static(rec)


def revoke_all_static(store: AccessStore) -> int:
    with store.lock:
        recs = store.static_codes()
        n = sum(not r.revoked for r in recs)
        for r in recs:
            r.revoked = True
        store.save_static(recs)
        return n


# --- people (rotating passes) --------------------------------------------------------

def add_person(store: AccessStore, label: str, ha_user_id: str, now: float | None = None) -> Person:
    label = (label or "").strip()
    if not label or len(label) > 60:
        raise IssueError("a label of 1-60 characters is required")
    if not ha_user_id:
        raise IssueError("choose the Home Assistant user who will carry this pass")
    with store.lock:
        people = store.people()
        if any(p.ha_user_id == ha_user_id for p in people):
            raise IssueError("that Home Assistant user already has a pass")
        taken = {p.handle for p in people}
        handle = codes.new_id(4)
        while handle in taken:
            handle = codes.new_id(4)
        p = Person(handle, label, ha_user_id, base64.b64encode(codes.new_secret()).decode(),
                   created=time.time() if now is None else now)
        store.save_people(people + [p])
        return p


def re_enrol(store: AccessStore, handle: str) -> Person:
    """New secret and version: every earlier code of this person stops working."""
    with store.lock:
        p = store.person(handle)
        if p is None:
            raise IssueError(f"no person {handle!r}")
        p.secret = base64.b64encode(codes.new_secret()).decode()
        p.v = codes.next_version(p.v)
        p.last_step = -1
        store.update_person(p)
        return p


def set_enabled(store: AccessStore, handle: str, enabled: bool):
    with store.lock:
        p = store.person(handle)
        if p is None:
            raise IssueError(f"no person {handle!r}")
        p.enabled = bool(enabled)
        store.update_person(p)


def remove_person(store: AccessStore, handle: str):
    with store.lock:
        people = store.people()
        if not any(p.handle == handle for p in people):
            raise IssueError(f"no person {handle!r}")
        store.save_people([p for p in people if p.handle != handle])


def current_pass(person: Person, period: int, now: float | None = None) -> tuple[str, float]:
    """(payload, seconds until it changes) for a person's rotating pass."""
    now = time.time() if now is None else now
    step = int(now // period)
    return (codes.rotating_code(person.key, person.v, person.handle, step),
            (step + 1) * period - now)
