"""Debounced present/absent decision. Pure logic; the caller supplies the clock.

Per check (one burst of frames):
  valid == 0                  -> FAILED: state and streak untouched; reported
                                 unknown once unknown_after_failures is reached
  hits >= present_min_hits    -> HIT: present
  0 < hits < present_min_hits -> INCONCLUSIVE: keep state, reset streak
  hits == 0                   -> MISS: streak += 1 (after expiry); absent once
                                 the streak reaches absent_checks

A bad or missing frame never moves the state towards absent. A read of the
target that the shape gate rejected is not the tag, so it is a miss too; it
also counts towards reject_streak, which raises an alert at ALERT_AFTER (the
gate may be rejecting the real tag, or a repeating phantom is being read).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

PRESENT, ABSENT, UNKNOWN = "present", "absent", "unknown"

HIT, INCONCLUSIVE, MISS, FAILED = "hit", "inconclusive", "miss", "failed"

# Reasons (enum sensor "reason" attribute)
R_STARTING = "starting"
R_RESTORED = "restored"
R_DISABLED = "disabled"
R_TAG_SEEN = "tag_seen"
R_INCONCLUSIVE = "inconclusive"
R_PENDING = "pending"            # misses seen, not yet enough to call absent
R_NO_TAG = "no_tag"              # absent confirmed
R_FETCH_FAILED = "fetch_failed"
R_ALL_INVALID = "all_frames_invalid"

MIN_STREAK_EXPIRY_S = 300.0
ALERT_AFTER = 3                  # consecutive misses with rejected target reads


@dataclass
class CheckResult:
    frames: int           # frames attempted
    fetch_failures: int
    valid: int
    hits: int
    rejected: int = 0     # frames whose target read the shape gate rejected


@dataclass
class Config:
    present_min_hits: int = 1
    absent_checks: int = 3
    unknown_after_failures: int = 2


def streak_expiry_s(poll_interval: float) -> float:
    if poll_interval and poll_interval > 0:
        return max(3.0 * poll_interval, MIN_STREAK_EXPIRY_S)
    return MIN_STREAK_EXPIRY_S


@dataclass
class Decision:
    state: str | None = None        # debounced PRESENT / ABSENT, None if never decided
    reason: str = R_STARTING
    miss_streak: int = 0
    last_miss_ts: float | None = None
    failed_checks: int = 0
    failure_reason: str | None = None
    last_seen_ts: float | None = None
    last_check_ts: float | None = None
    reject_streak: int = 0

    def update(self, r: CheckResult, cfg: Config, now: float, poll_interval: float) -> str:
        self.last_check_ts = now
        if r.valid == 0:
            self.failed_checks += 1
            self.failure_reason = (R_FETCH_FAILED if r.fetch_failures >= r.frames
                                   else R_ALL_INVALID)
            return FAILED

        self.failed_checks = 0
        self.failure_reason = None

        self.reject_streak = self.reject_streak + 1 if r.hits == 0 and r.rejected > 0 else 0

        if r.hits >= cfg.present_min_hits:
            self.state, self.reason = PRESENT, R_TAG_SEEN
            self._reset_streak()
            self.last_seen_ts = now
            return HIT

        if r.hits > 0:
            self.reason = R_INCONCLUSIVE
            self._reset_streak()
            self.last_seen_ts = now
            return INCONCLUSIVE

        if self.last_miss_ts is not None and now - self.last_miss_ts > streak_expiry_s(poll_interval):
            self._reset_streak()
        self.miss_streak += 1
        self.last_miss_ts = now
        if self.miss_streak >= cfg.absent_checks:
            self.state, self.reason = ABSENT, R_NO_TAG
        else:
            self.reason = R_PENDING
        return MISS

    def _reset_streak(self):
        self.miss_streak = 0
        self.last_miss_ts = None

    def reported(self, cfg: Config, enabled: bool = True) -> tuple[str, str]:
        """(present|absent|unknown, reason) as published to HA."""
        if not enabled:
            return UNKNOWN, R_DISABLED
        if self.failed_checks >= cfg.unknown_after_failures:
            return UNKNOWN, self.failure_reason or R_FETCH_FAILED
        if self.state is None:
            return UNKNOWN, self.reason
        return self.state, self.reason

    # --- persistence -------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "Decision":
        """Restore after a restart: keep state and streak, reset failure count."""
        raw = json.loads(s)
        d = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        if d.state not in (PRESENT, ABSENT):
            d.state = None
        d.reason = R_RESTORED if d.state else R_STARTING
        d.failed_checks, d.failure_reason = 0, None
        return d

    def save(self, path: str):
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            f.write(self.to_json())
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "Decision":
        try:
            with open(path) as f:
                return cls.from_json(f.read())
        except (OSError, ValueError, TypeError):
            return cls()
