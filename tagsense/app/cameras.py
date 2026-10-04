"""One worker thread per camera source. A burst requested by any object on the
camera is fetched once, decoded once and judged by every enabled object on it.

Each frame is analysed as soon as it arrives, so the burst can stop early when
every object is already present and its first present_min_hits frames all hit.
Misses and uncertain checks always fetch the full burst."""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime

from .sanity import decode_jpeg
from .scheduler import SHARED
from .sources import FetchError, Frame, Source
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
        self.fetch_lock = threading.Lock()     # bursts and UI grabs share the source
        self.last_frame: Frame | None = None   # newest good frame, for the web UI
        self.last_frame_at = 0.0
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
                burst = self.fetch_burst(enabled)
                for o in enabled:
                    o.process(due.get(o, SHARED), burst)
            except Exception:       # never let one bad check kill the worker
                log.exception("[%s] check failed", self.name)

    def fetch(self) -> Frame:
        with self.fetch_lock:
            frame = self.source.fetch()
        self.last_frame, self.last_frame_at = frame, time.monotonic()
        return frame

    def grab(self, max_age_s: float = 0.0) -> Frame:
        """A full frame for the web UI: cached if fresh enough, else fetched now."""
        if self.last_frame and time.monotonic() - self.last_frame_at <= max_age_s:
            return self.last_frame
        return self.fetch()

    def fetch_burst(self, objects: list[TrackedObject] = ()) -> Burst:
        """Fetch up to burst_size frames, analysing each for `objects` as it arrives."""
        b = Burst(requested=self.burst_size)
        for o in objects:
            o.begin_burst()
        for i in range(self.burst_size):
            if i and self.stop_event.wait(self.burst_interval_s):
                break
            try:
                frame = self.fetch()
            except FetchError as e:
                b.failures += 1
                self.last_error = f"{datetime.now().strftime('%H:%M:%S')} {e}"
                log.warning("[%s] fetch failed: %s", self.name, e)
                continue
            b.hashes.add(hashlib.blake2b(frame.data, digest_size=16).digest())
            b.frames.append(frame)
            b.images.append(decode_jpeg(frame.data))
            for o in objects:
                o.analyse_frame(b.images[-1])
            if objects and len(b.frames) < self.burst_size and all(o.satisfied() for o in objects):
                log.debug("[%s] burst stopped after %d frames: all present", self.name, len(b.frames))
                break
        self.fetch_failures_total += b.failures
        b.last_error = self.last_error
        b.fetch_failures_total = self.fetch_failures_total
        return b
