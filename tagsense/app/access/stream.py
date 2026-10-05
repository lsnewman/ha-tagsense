"""A video stream held open for one scan window, keeping only the newest frame.

go2rtc's frame.jpeg starts a conversion for every frame: it waits for a
keyframe and decodes it to JPEG, which took about 6 s per frame on a 5 MP
Reolink stream (20 s through the panel). Its /api/stream.mp4 passes the video
through unchanged; opened once, it took about 7 s to the first frame and then
delivered about 22 frames per second (SPEC.md, "Frame capture").

A reader thread decodes continuously and keeps only the latest frame, so the
scanner always works on the newest image and never on a backlog.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger("tagsense.access")

OPEN_TIMEOUT_S = 20.0
READ_TIMEOUT_S = 5.0


def open_capture(url: str):
    return cv2.VideoCapture(url, cv2.CAP_FFMPEG,
                            [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(OPEN_TIMEOUT_S * 1000),
                             cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(READ_TIMEOUT_S * 1000)])


class FrameStream:
    def __init__(self, url: str, name: str = "stream",
                 capture_factory: Callable[[str], object] = open_capture):
        self.url = url
        self.name = name
        self._factory = capture_factory
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._seq = 0                    # frames decoded so far
        self._stop = threading.Event()
        self.started = time.monotonic()
        self.first_frame_s: float | None = None
        self.error: str | None = None
        self.ended = False
        self._thread = threading.Thread(target=self._run, name=f"stream-{name}", daemon=True)
        self._thread.start()

    def _run(self):
        cap = None
        try:
            cap = self._factory(self.url)
            if not cap.isOpened():
                self.error = "could not open the video stream"
                return
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    self.error = "the video stream ended"
                    return
                with self._cond:
                    if self.first_frame_s is None:
                        self.first_frame_s = time.monotonic() - self.started
                    self._frame, self._seq = frame, self._seq + 1
                    self._cond.notify_all()
        except Exception as e:        # noqa: BLE001 - reported, never raised into the scanner
            self.error = f"video stream failed: {e}"
        finally:
            if cap is not None:
                cap.release()
            with self._cond:
                self.ended = True
                self._cond.notify_all()

    def next_frame(self, after_seq: int, timeout: float) -> tuple[np.ndarray | None, int]:
        """The newest frame decoded after `after_seq`, waiting up to `timeout`.
        Returns (None, seq) on timeout or when the stream has ended."""
        end = time.monotonic() + timeout
        with self._cond:
            while self._seq <= after_seq and not self.ended:
                left = end - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)
            if self._seq > after_seq:
                return self._frame, self._seq
            return None, self._seq

    @property
    def frames_decoded(self) -> int:
        return self._seq

    def latest(self) -> np.ndarray | None:
        with self._cond:
            return self._frame

    def close(self):
        self._stop.set()
        self._thread.join(timeout=READ_TIMEOUT_S + 1)
