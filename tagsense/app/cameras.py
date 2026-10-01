"""One worker thread per camera source. A burst requested by any object on the
camera is fetched once, decoded once and judged by every enabled object on it."""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime

from .sanity import decode_jpeg
from .scheduler import SHARED
from .sources import FetchError, Source
from .tracker import Burst, TrackedObject

log = logging.getLogger("tagsense")


class CameraWorker:
    def __init__(self, name: str, source: Source, cond: threading.Condition,
                 burst_size: int, burst_interval_s: float, stop_event: threading.Event):
        self.name = name
        self.source = source
        self.cond = cond
        self.burst_size = burst_size
        self.burst_interval_s = burst_interval_s
        self.stop_event = stop_event
        self.objects: list[TrackedObject] = []
        self.last_error: str | None = None
        self.fetch_failures_total = 0
        self.thread = threading.Thread(target=self.run, name=f"camera-{name}", daemon=True)

    def due(self, now: float) -> dict[TrackedObject, str]:
        """Pop due triggers for this camera's objects (call with cond held)."""
        due = {}
        for o in self.objects:
            if t := o.sched.due(now):
                due[o] = t
        return due

    def seconds_until_due(self, now: float) -> float | None:
        waits = [w for o in self.objects if (w := o.sched.seconds_until_due(now)) is not None]
        return min(waits) if waits else None

    def run(self):
        while not self.stop_event.is_set():
            with self.cond:
                due = self.due(time.monotonic())
                if not due:
                    wait = self.seconds_until_due(time.monotonic())
                    self.cond.wait(timeout=min(wait, 60.0) if wait is not None else 60.0)
                    continue
                enabled = [o for o in self.objects if o.settings.enabled]
            try:
                burst = self.fetch_burst()
                for o in enabled:
                    o.process(due.get(o, SHARED), burst)
            except Exception:       # never let one bad check kill the worker
                log.exception("[%s] check failed", self.name)

    def fetch_burst(self) -> Burst:
        b = Burst(requested=self.burst_size)
        for i in range(self.burst_size):
            if i and self.stop_event.wait(self.burst_interval_s):
                break
            try:
                frame = self.source.fetch()
            except FetchError as e:
                b.failures += 1
                self.last_error = f"{datetime.now().strftime('%H:%M:%S')} {e}"
                log.warning("[%s] fetch failed: %s", self.name, e)
                continue
            b.hashes.add(hashlib.blake2b(frame.data, digest_size=16).digest())
            b.frames.append(frame)
            b.images.append(decode_jpeg(frame.data))
        self.fetch_failures_total += b.failures
        b.last_error = self.last_error
        b.fetch_failures_total = self.fetch_failures_total
        return b
