from app.decision import FAILED, HIT, INCONCLUSIVE, MISS
from app.scheduler import CONFIRM, MANUAL, POLL, STARTUP, Scheduler

ABSENT_CHECKS = 3


def test_poll_due_after_interval():
    s = Scheduler(0, poll_interval=60)
    assert s.due(59) is None
    assert s.due(60) == POLL
    assert s.due(60) is None          # popped


def test_manual_poll_zero_means_manual_only():
    s = Scheduler(0, poll_interval=0)
    assert s.due(10_000) is None
    assert s.seconds_until_due(0) is None
    s.request(MANUAL)
    assert s.due(1) == MANUAL


def test_requests_merge_into_one_check_with_priority():
    s = Scheduler(0, poll_interval=60)
    s.request(MANUAL)
    s.request(MANUAL)
    s.request(STARTUP)
    assert s.due(61) == MANUAL
    assert s.due(61) is None


def test_any_check_resets_poll_timer():
    s = Scheduler(0, poll_interval=60)
    s.request(MANUAL)
    s.due(50)
    s.on_result(MANUAL, HIT, 0, ABSENT_CHECKS, 55)
    assert s.due(60) is None
    assert s.due(115) == POLL


def test_confirmation_chain_runs_until_absent():
    s = Scheduler(0, poll_interval=3600, confirm_delay_s=45)
    s.request(MANUAL)
    assert s.due(0) == MANUAL
    s.on_result(MANUAL, MISS, 1, ABSENT_CHECKS, 5)
    assert s.due(49) is None
    assert s.due(50) == CONFIRM
    s.on_result(CONFIRM, MISS, 2, ABSENT_CHECKS, 55)
    assert s.due(100) == CONFIRM
    s.on_result(CONFIRM, MISS, 3, ABSENT_CHECKS, 105)   # absent reached
    assert s.confirm_at is None
    assert s.due(1000) is None


def test_chain_stops_on_hit_inconclusive_or_failed():
    for outcome in (HIT, INCONCLUSIVE, FAILED):
        s = Scheduler(0, poll_interval=3600)
        s.on_result(MANUAL, MISS, 1, ABSENT_CHECKS, 0)
        assert s.confirm_at is not None
        s.on_result(CONFIRM, outcome, 1, ABSENT_CHECKS, 45)
        assert s.confirm_at is None, outcome


def test_poll_and_startup_never_start_a_chain():
    for trig in (POLL, STARTUP):
        s = Scheduler(0, poll_interval=60)
        s.on_result(trig, MISS, 1, ABSENT_CHECKS, 0)
        assert s.confirm_at is None


def test_poll_miss_does_not_cancel_chain():
    s = Scheduler(0, poll_interval=60)
    s.on_result(MANUAL, MISS, 1, ABSENT_CHECKS, 0)
    s.on_result(POLL, MISS, 2, ABSENT_CHECKS, 30)
    assert s.confirm_at == 45


def test_manual_press_restarts_chain():
    s = Scheduler(0, poll_interval=3600)
    s.on_result(MANUAL, MISS, 1, ABSENT_CHECKS, 0)
    s.request(MANUAL)
    assert s.confirm_at is None
    assert s.due(1) == MANUAL


def test_disable_clears_everything_and_blocks_requests():
    s = Scheduler(0, poll_interval=60)
    s.on_result(MANUAL, MISS, 1, ABSENT_CHECKS, 0)
    s.request(MANUAL)
    s.set_enabled(False, 1)
    assert s.confirm_at is None and s.next_poll is None
    s.request(MANUAL)
    assert s.due(10_000) is None
    s.set_enabled(True, 100)
    assert s.due(160) == POLL


def test_set_poll_interval_reschedules():
    s = Scheduler(0, poll_interval=3600)
    s.set_poll_interval(10, 100)
    assert s.due(110) == POLL
