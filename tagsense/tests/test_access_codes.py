"""Signed access codes: formats, verification (fail closed), replay, lockout, issuing."""
import os
import stat

import cv2
import numpy as np
import pytest

from app.access import codes, issue
from app.access.qr import QrDecoder
from app.access.store import AccessStore, StoreError
from app.access.verifier import LOCK_FOR_S, Verifier

NOW = 1791200000.0          # 2026-10-05, well after the clock floor
PERIOD = 30


@pytest.fixture
def store(tmp_path):
    return AccessStore(str(tmp_path / "access"))


@pytest.fixture
def ver(store):
    return Verifier(store, PERIOD, clock=lambda: NOW)


def person(store):
    return issue.add_person(store, "Luke", "ha-user-1", now=NOW)


def pass_at(p, t):
    return issue.current_pass(p, PERIOD, t)[0]


# --- formats -------------------------------------------------------------------------

def test_formats_fit_qr_version_2_and_read_back(store):
    p = person(store)
    _, static = issue.issue_static(store, "Plumber", expires=NOW + 3600, now=NOW)
    for payload, n in ((pass_at(p, NOW), 23), (static, 34)):
        assert len(payload) == n and codes.parse(payload) is not None
        assert codes.qr_modules(payload) <= 25                     # version 2 at most
        img = cv2.imdecode(np.frombuffer(codes.qr_png(payload), np.uint8), cv2.IMREAD_COLOR)
        assert QrDecoder().decode(img).text == payload


def test_parse_rejects_foreign_and_malformed():
    for t in ("", "HELLO", "https://example.com", "TR" + "A" * 20, "ts" + "A" * 32, "TSA" + "1" * 31):
        assert codes.parse(t) is None


# --- rotating --------------------------------------------------------------------------

def test_rotating_current_and_previous_step_only(store):
    p = person(store)
    v = lambda: Verifier(store, PERIOD, clock=lambda: NOW)
    assert v().verify(pass_at(p, NOW), "door").status == "verified"
    p = store.person(p.handle)
    assert v().verify(pass_at(p, NOW - PERIOD), "door").status == "replayed"   # older than last used
    p2 = issue.add_person(store, "Sam", "ha-user-2", now=NOW)
    assert v().verify(pass_at(p2, NOW - PERIOD), "door").status == "verified"   # previous step ok
    p3 = issue.add_person(store, "Kim", "ha-user-3", now=NOW)
    assert v().verify(pass_at(p3, NOW - 2 * PERIOD), "door").status == "invalid"  # too old
    assert v().verify(pass_at(p3, NOW + PERIOD), "door").status == "invalid"      # future


def test_rotating_replay_in_same_step(store, ver):
    p = person(store)
    code = pass_at(p, NOW)
    r = ver.verify(code, "door")
    assert r.status == "verified" and r.attrs["label"] == "Luke" and r.attrs["code_type"] == "rotating"
    assert ver.verify(code, "door").status == "replayed"


def test_every_single_character_tamper_fails(store, ver):
    p = person(store)
    code = pass_at(p, NOW)
    for i in range(3, len(code)):                 # every char after the type marker
        c = codes.B32[(codes.B32.index(code[i]) + 1) % 32]
        assert ver.verify(code[:i] + c + code[i + 1:], "door").status == "invalid"


def test_disabled_reenrolled_and_removed_people(store, ver):
    p = person(store)
    old = pass_at(p, NOW)
    issue.set_enabled(store, p.handle, False)
    assert ver.verify(old, "door").status == "invalid"
    issue.set_enabled(store, p.handle, True)
    p2 = issue.re_enrol(store, p.handle)
    assert ver.verify(old, "door").status == "invalid"          # old secret is dead
    assert ver.verify(pass_at(p2, NOW), "door").status == "verified"
    issue.remove_person(store, p.handle)
    assert ver.verify(pass_at(p2, NOW), "door").status == "invalid"


# --- static -----------------------------------------------------------------------------

def test_static_single_use_survives_restart(store, tmp_path):
    _, code = issue.issue_static(store, "Plumber", uses=1, now=NOW)
    assert Verifier(store, clock=lambda: NOW).verify(code, "door").status == "verified"
    again = Verifier(AccessStore(store.dir), clock=lambda: NOW + 5)          # "restart"
    assert again.verify(code, "door").status == "replayed"


def test_static_validity_window_and_uses(store):
    rec, code = issue.issue_static(store, "Cleaner", expires=NOW + 7200, uses=2,
                                   valid_from=NOW + 3600, now=NOW)
    at = lambda t: Verifier(store, clock=lambda: t).verify(code, "door")
    r = at(NOW + 60)
    assert r.status == "not_yet_valid" and not r.bad
    assert at(NOW + 3700).status == "verified"
    assert at(NOW + 3800).attrs["uses_left"] == 0
    assert at(NOW + 3900).status == "replayed"
    rec2, code2 = issue.issue_static(store, "Late", expires=NOW + 7200, now=NOW)
    assert Verifier(store, clock=lambda: NOW + 7300).verify(code2, "door").status == "expired"


def test_static_revoke_rotate_key_and_forged_expiry(store, ver):
    rec, code = issue.issue_static(store, "Painter", expires=NOW + 3600, now=NOW)
    # Extending the expiry in the payload breaks the MAC.
    forged = code[:11] + codes.int_b32(rec.expiry_min + 10000, 7) + code[18:]
    assert ver.verify(forged, "door").status == "invalid"
    issue.revoke_static(store, rec.code_id)
    assert ver.verify(code, "door").status == "revoked"
    _, code2 = issue.issue_static(store, "Gardener", expires=NOW + 3600, now=NOW)
    store.rotate_static_key()
    assert ver.verify(code2, "door").status == "invalid"


def test_issue_rules(store):
    with pytest.raises(issue.IssueError, match="expiry, a number of uses"):
        issue.issue_static(store, "x", now=NOW)
    with pytest.raises(issue.IssueError, match="10 minutes"):
        issue.issue_static(store, "x", expires=NOW + 300, now=NOW)
    with pytest.raises(issue.IssueError, match="within 365 days"):
        issue.issue_static(store, "x", expires=NOW + 400 * 86400, now=NOW)
    rec, _ = issue.issue_static(store, "Uses only", uses=3, now=NOW)
    assert abs(rec.expires - (NOW + issue.BACKSTOP_DAYS * 86400)) < 61       # backstop
    rec, _ = issue.issue_static(store, "Uses only", uses=3, backstop_days=7,
                                valid_from=NOW + 86400, now=NOW)
    assert abs(rec.expires - (NOW + 8 * 86400)) < 61                       # from the start


# --- fail closed ----------------------------------------------------------------------------

def test_write_failure_never_verifies(store, ver, monkeypatch):
    p = person(store)
    _, scode = issue.issue_static(store, "Plumber", uses=1, now=NOW)

    def boom(*a, **k):
        raise StoreError("disk full")
    monkeypatch.setattr(store, "update_person", boom)
    monkeypatch.setattr(store, "update_static", boom)
    assert ver.verify(pass_at(p, NOW), "door").status == "unavailable"
    assert ver.verify(scode, "door").status == "unavailable"


def test_unreadable_store_and_unset_clock(store, ver):
    p = person(store)
    with open(os.path.join(store.dir, "people.json"), "w") as f:
        f.write("{not json")
    assert ver.verify(pass_at(p, NOW), "door").status == "unavailable"
    assert Verifier(store, clock=lambda: 86400.0).verify("TRAAAAA" + "A" * 16, "door").status \
        == "unavailable"


def test_files_are_private(store):
    person(store)
    issue.issue_static(store, "x", uses=1, now=NOW)
    for name in ("keys.json", "people.json", "static.json"):
        assert stat.S_IMODE(os.stat(os.path.join(store.dir, name)).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.dir).st_mode) == 0o700


# --- lockout -----------------------------------------------------------------------------

def test_lockout_by_window_and_recent_counts_and_restart(store):
    v = Verifier(store, clock=lambda: NOW)
    assert not any(v.register_bad("door", n, NOW) for n in (1, 2, 3))
    assert v.register_bad("door", 4, NOW)                  # 4 in one window
    r = v.verify("TRAAAAA" + "A" * 16, "door")
    assert r.status == "locked_out" and "locked_until" in r.attrs
    assert Verifier(AccessStore(store.dir)).locked_until("door", NOW + 60)   # survives restart
    assert Verifier(store).locked_until("door", NOW + LOCK_FOR_S + 1) is None
    v.clear_lockout("door")
    assert v.locked_until("door", NOW) is None
    # 6 bad scans in 10 minutes, one per window, also lock.
    assert [v.register_bad("gate", 1, NOW + i * 60) for i in range(6)][-1] is True


def test_unrecognised_does_not_count(ver):
    r = ver.verify("https://parcel.example/track/123", "door")
    assert r.status == "unrecognised" and not r.bad
