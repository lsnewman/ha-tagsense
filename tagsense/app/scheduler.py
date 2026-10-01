"""When to run the next check. Pure logic; the worker thread supplies the clock.

Triggers: startup, poll, manual (button), confirm (follow-up to a manual miss).
Several due triggers merge into one check, labelled with the highest priority.

Confirmation chain: a manual or confirm check that is a clean miss, with the
streak still short of absent_checks, schedules a confirm check confirm_delay_s
later. The chain stops on a hit, an inconclusive or failed check, reaching
absent, or being disabled. A manual press restarts it. Polls and the startup
check never start a chain.
"""
from __future__ import annotations

from .decision import MISS

STARTUP, POLL, MANUAL, CONFIRM = "startup", "poll", "manual", "confirm"
_PRIORITY = {MANUAL: 0, STARTUP: 1, CONFIRM: 2, POLL: 3}


class Scheduler:
    def __init__(self, now: float, poll_interval: float = 60.0,
                 confirm_delay_s: float = 45.0, enabled: bool = True):
        self.poll_interval = poll_interval
        self.confirm_delay_s = confirm_delay_s
        self.enabled = enabled
        self._pending: set[str] = set()
        self.confirm_at: float | None = None
        self.next_poll: float | None = None
        self._schedule_poll(now)

    def _schedule_poll(self, now: float):
        self.next_poll = now + self.poll_interval if self.enabled and self.poll_interval > 0 else None

    def request(self, trigger: str):
        if not self.enabled:
            return
        if trigger == MANUAL:
            self.confirm_at = None      # a manual press restarts any chain
        self._pending.add(trigger)

    def set_enabled(self, enabled: bool, now: float):
        self.enabled = enabled
        if not enabled:
            self._pending.clear()
            self.confirm_at = None
        self._schedule_poll(now)

    def set_poll_interval(self, seconds: float, now: float):
        self.poll_interval = seconds
        self._schedule_poll(now)

    def due(self, now: float) -> str | None:
        """Pop the trigger to run now (merging everything due), or None."""
        if not self.enabled:
            return None
        due = set(self._pending)
        if self.confirm_at is not None and now >= self.confirm_at:
            due.add(CONFIRM)
        if self.next_poll is not None and now >= self.next_poll:
            due.add(POLL)
        if not due:
            return None
        self._pending.clear()
        if CONFIRM in due:
            self.confirm_at = None
        self._schedule_poll(now)        # this check satisfies any due poll
        return min(due, key=_PRIORITY.__getitem__)

    def seconds_until_due(self, now: float) -> float | None:
        if not self.enabled:
            return None
        if self._pending:
            return 0.0
        times = [t for t in (self.confirm_at, self.next_poll) if t is not None]
        return max(0.0, min(times) - now) if times else None

    def on_result(self, trigger: str, outcome: str, miss_streak: int,
                  absent_checks: int, now: float):
        """Called after each check completes."""
        self._schedule_poll(now)        # any check resets the poll timer
        if outcome != MISS or miss_streak >= absent_checks:
            self.confirm_at = None
        elif trigger in (MANUAL, CONFIRM) and self.enabled:
            self.confirm_at = now + self.confirm_delay_s
