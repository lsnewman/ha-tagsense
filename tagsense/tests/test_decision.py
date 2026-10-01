import pytest

from app import decision as d
from app.decision import CheckResult, Config, Decision

CFG = Config(present_min_hits=1, absent_checks=3, unknown_after_failures=2)
POLL = 60


def hit(n=5):
    return CheckResult(frames=5, fetch_failures=0, valid=5, hits=n)


def miss():
    return CheckResult(frames=5, fetch_failures=0, valid=5, hits=0)


def fetch_fail():
    return CheckResult(frames=5, fetch_failures=5, valid=0, hits=0)


def all_invalid():
    return CheckResult(frames=5, fetch_failures=1, valid=0, hits=0)


def run(dec, results, cfg=CFG, start=1000.0, step=60.0):
    out = []
    for i, r in enumerate(results):
        out.append(dec.update(r, cfg, start + i * step, POLL))
    return out


def test_fresh_reports_unknown_starting():
    assert Decision().reported(CFG) == (d.UNKNOWN, d.R_STARTING)


def test_single_hit_is_present():
    dec = Decision()
    assert run(dec, [hit(1)]) == [d.HIT]
    assert dec.reported(CFG) == (d.PRESENT, d.R_TAG_SEEN)
    assert dec.last_seen_ts == 1000.0


def test_absent_after_three_misses():
    dec = Decision(state=d.PRESENT)
    run(dec, [miss(), miss()])
    assert dec.reported(CFG) == (d.PRESENT, d.R_PENDING)
    run(dec, [miss()], start=1120.0)
    assert dec.reported(CFG) == (d.ABSENT, d.R_NO_TAG)


def test_pending_without_prior_state_is_unknown():
    dec = Decision()
    run(dec, [miss()])
    assert dec.reported(CFG) == (d.UNKNOWN, d.R_PENDING)


def test_hit_resets_streak():
    dec = Decision(state=d.PRESENT)
    run(dec, [miss(), miss(), hit(), miss(), miss()])
    assert dec.state == d.PRESENT and dec.miss_streak == 2


def test_inconclusive_keeps_state_resets_streak_updates_last_seen():
    cfg = Config(present_min_hits=3, absent_checks=3, unknown_after_failures=2)
    dec = Decision(state=d.ABSENT)
    run(dec, [miss(), miss()], cfg)
    assert dec.miss_streak == 2
    assert dec.update(hit(2), cfg, 5000.0, POLL) == d.INCONCLUSIVE
    assert dec.state == d.ABSENT
    assert dec.miss_streak == 0
    assert dec.last_seen_ts == 5000.0
    assert dec.reported(cfg) == (d.ABSENT, d.R_INCONCLUSIVE)
    assert dec.update(hit(3), cfg, 5060.0, POLL) == d.HIT
    assert dec.state == d.PRESENT


def test_failed_checks_never_produce_absent():
    dec = Decision(state=d.PRESENT)
    run(dec, [fetch_fail()] * 10)
    assert dec.state == d.PRESENT and dec.miss_streak == 0


def test_unknown_only_after_threshold_failures():
    dec = Decision(state=d.PRESENT, reason=d.R_TAG_SEEN)
    assert run(dec, [fetch_fail()]) == [d.FAILED]
    assert dec.reported(CFG) == (d.PRESENT, d.R_TAG_SEEN)
    run(dec, [all_invalid()], start=1060.0)
    assert dec.reported(CFG) == (d.UNKNOWN, d.R_ALL_INVALID)
    run(dec, [hit()], start=1120.0)
    assert dec.reported(CFG) == (d.PRESENT, d.R_TAG_SEEN)


def test_failure_reason_fetch_vs_invalid():
    cfg = Config(unknown_after_failures=1)
    dec = Decision()
    dec.update(fetch_fail(), cfg, 0, POLL)
    assert dec.reported(cfg) == (d.UNKNOWN, d.R_FETCH_FAILED)
    dec.update(all_invalid(), cfg, 1, POLL)
    assert dec.reported(cfg) == (d.UNKNOWN, d.R_ALL_INVALID)


def test_streak_kept_across_short_outage():
    dec = Decision(state=d.PRESENT)
    dec.update(miss(), CFG, 0, POLL)
    dec.update(fetch_fail(), CFG, 60, POLL)
    dec.update(miss(), CFG, 120, POLL)
    dec.update(miss(), CFG, 180, POLL)
    assert dec.state == d.ABSENT


def test_streak_expires_after_long_gap():
    dec = Decision(state=d.PRESENT)
    dec.update(miss(), CFG, 0, POLL)
    for t in range(60, 3600, 60):            # an hour of outage
        dec.update(fetch_fail(), CFG, t, POLL)
    dec.update(miss(), CFG, 3600, POLL)
    dec.update(miss(), CFG, 3660, POLL)
    assert dec.miss_streak == 2 and dec.state == d.PRESENT


@pytest.mark.parametrize("poll,expected", [(60, 300), (600, 1800), (0, 300)])
def test_streak_expiry_window(poll, expected):
    assert d.streak_expiry_s(poll) == expected


def test_disabled_reports_unknown():
    dec = Decision(state=d.PRESENT)
    assert dec.reported(CFG, enabled=False) == (d.UNKNOWN, d.R_DISABLED)


def test_persistence_round_trip_and_restored(tmp_path):
    p = str(tmp_path / "state.json")
    dec = Decision()
    run(dec, [hit(), miss()])
    dec.update(fetch_fail(), CFG, 2000, POLL)
    dec.save(p)
    back = Decision.load(p)
    assert back.state == d.PRESENT and back.miss_streak == 1
    assert back.last_miss_ts == dec.last_miss_ts
    assert back.failed_checks == 0
    assert back.reported(CFG) == (d.PRESENT, d.R_RESTORED)


def test_restored_persists_until_success_or_failure_threshold(tmp_path):
    p = str(tmp_path / "state.json")
    Decision(state=d.ABSENT).save(p)
    back = Decision.load(p)
    back.update(fetch_fail(), CFG, 0, POLL)
    assert back.reported(CFG) == (d.ABSENT, d.R_RESTORED)
    back.update(fetch_fail(), CFG, 60, POLL)
    assert back.reported(CFG) == (d.UNKNOWN, d.R_FETCH_FAILED)

    back = Decision.load(p)
    back.update(miss(), CFG, 0, POLL)
    assert back.reported(CFG) == (d.ABSENT, d.R_PENDING)   # successful check clears "restored"


def test_load_missing_or_corrupt(tmp_path):
    assert Decision.load(str(tmp_path / "nope.json")).reported(CFG) == (d.UNKNOWN, d.R_STARTING)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert Decision.load(str(bad)).state is None
