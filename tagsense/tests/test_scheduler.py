from app.decision import FAILED, HIT, INCONCLUSIVE, MISS
from app.scheduler import CONFIRM, MANUAL, POLL, SHARED, STARTUP, STEADY_CHECKS, Scheduler

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


def steady_run(s, n, now=0, trigger=POLL):
    for _ in range(n):
        s.on_result(trigger, HIT, 0, ABSENT_CHECKS, now, steady=True)


def test_slowdown_doubles_up_to_the_cap():
    s = Scheduler(0, poll_interval=60, slowdown_max=4)
    steady_run(s, STEADY_CHECKS - 1)
    assert s.poll_interval == 60
    steady_run(s, 1)
    assert s.poll_interval == 120 and s.next_poll == 120
    steady_run(s, STEADY_CHECKS, trigger=SHARED)          # shared checks count too
    assert s.poll_interval == 240
    steady_run(s, 3 * STEADY_CHECKS)
    assert s.poll_interval == 240                         # capped at 4x
    s3 = Scheduler(0, poll_interval=60, slowdown_max=3)
    steady_run(s3, 3 * STEADY_CHECKS)
    assert s3.poll_interval == 180                        # cap need not be a power of 2


def test_slowdown_resets_on_anything_unsteady():
    for event in ("miss", "manual", "confirm", "startup", "interval", "max"):
        s = Scheduler(0, poll_interval=60, slowdown_max=8)
        steady_run(s, 2 * STEADY_CHECKS)
        assert s.poll_interval == 240
        if event == "miss":
            s.on_result(POLL, MISS, 1, ABSENT_CHECKS, 0, steady=False)
        elif event in ("manual", "confirm", "startup"):
            trig = {"manual": MANUAL, "confirm": CONFIRM, "startup": STARTUP}[event]
            s.on_result(trig, HIT, 0, ABSENT_CHECKS, 0, steady=True)
        elif event == "interval":
            s.set_poll_interval(60, 0)
        else:
            s.set_slowdown(4, 0)
        assert (s.poll_interval, s.steady) == (60, 0), event


def test_slowdown_off_by_default():
    s = Scheduler(0, poll_interval=60)
    steady_run(s, 10 * STEADY_CHECKS)
    assert s.poll_interval == 60
