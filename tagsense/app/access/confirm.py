"""Confirm-back: Home Assistant asks TagSense "did you really send this event?"

The broker cannot stop other MQTT clients publishing a fake `verified` event:
the Home Assistant Mosquitto app treats every user as a superuser, so ACLs
have no effect (tested, SPEC.md). So each `verified` event carries a random
one-time `event_id`, and an automation that matters (a lock) first asks
TagSense, over HTTP from Home Assistant itself with a token, to confirm it.

An ID is confirmed once, within CONFIRM_TTL_S, and is then gone. IDs live in
memory only: after a restart nothing confirms (fails closed).
"""
from __future__ import annotations

import secrets
import threading
import time

CONFIRM_TTL_S = 30.0
MAX_PENDING = 200


class ConfirmBook:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[float, dict]] = {}

    def issue(self, info: dict) -> str:
        """A new one-time ID for a verified event; `info` is returned on confirm."""
        eid = secrets.token_urlsafe(16)               # 128 bits
        with self._lock:
            now = self._clock()
            self._pending = {k: v for k, v in self._pending.items() if v[0] > now}
            if len(self._pending) >= MAX_PENDING:     # keep the newest
                for k in sorted(self._pending, key=lambda k: self._pending[k][0])[:50]:
                    del self._pending[k]
            self._pending[eid] = (now + CONFIRM_TTL_S, dict(info))
        return eid

    def confirm(self, event_id: str) -> dict | None:
        """The event's info if `event_id` was issued, is unused and has not expired;
        it can never be confirmed again. Otherwise None."""
        with self._lock:
            entry = self._pending.pop(event_id, None)
        if entry is None or entry[0] < self._clock():
            return None
        return entry[1]
