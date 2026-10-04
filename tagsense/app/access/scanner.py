"""One scanner: a thread that scans for a QR code during a short window.

A Scan press opens a window of `window_s` seconds (a press during an open
window extends it). Frames are fetched back to back, `frame_interval_s` apart,
and each is decoded in the scanner's crop. The window ends at the first code
read, or with a scan_timeout event. There is no continuous scanning.

The payload text never leaves this module: events carry its length and a
short fingerprint, logs the same, and images have the code blacked out.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

import cv2
import numpy as np

from ..detector import crop_pixels
from ..sanity import decode_jpeg
from ..sources import FetchError, Source
from .config import ScannerConfig
from .qr import QrDecoder, QrRead, blackout

log = logging.getLogger("tagsense.access")

EVENTS_KEPT = 20
DEBUG_MAX_FILES = 50
DEBUG_MAX_AGE_S = 24 * 3600


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Scanner:
    def __init__(self, cfg: ScannerConfig, source: Source, mqtt, access_dir: str,
                 stop_event: threading.Event):
        self.cfg = cfg
        self.source = source
        self.mqtt = mqtt
        self.stop_event = stop_event
        self.debug_dir = os.path.join(access_dir, "debug", cfg.id)
        self.cond = threading.Condition()
        self.deadline = 0.0             # monotonic end of the open window, 0 = closed
        self.decoder = QrDecoder()
        self.fetch_lock = threading.Lock()
        # For the panel
        self.events: deque[dict] = deque(maxlen=EVENTS_KEPT)
        self.last_jpeg: bytes | None = None
        self.last_error: str | None = None
        self.scanning = False
        self.thread = threading.Thread(target=self.run, name=f"scanner-{cfg.id}", daemon=True)

    @property
    def id(self) -> str:
        return self.cfg.id

    # --- triggers ---------------------------------------------------------------

    def scan(self, trigger: str = "button") -> None:
        with self.cond:
            self.deadline = time.monotonic() + self.cfg.window_s
            self.cond.notify_all()
        log.info("access [%s]: scan requested (%s), window %gs", self.id, trigger, self.cfg.window_s)

    # --- worker -------------------------------------------------------------------

    def run(self):
        while not self.stop_event.is_set():
            with self.cond:
                if self.deadline <= time.monotonic():
                    self.deadline = 0.0
                    self.cond.wait(timeout=5.0)
                    continue
            try:
                self._window()
            except Exception:       # never let one bad window kill the scanner
                log.exception("access [%s]: scan window failed", self.id)
                self._set_scanning(False)

    def _window(self):
        self._set_scanning(True)
        frames = failures = 0
        last_img = None
        read: QrRead | None = None
        while not self.stop_event.is_set():
            with self.cond:
                if time.monotonic() >= self.deadline:
                    break
            try:
                data = self.fetch()
            except FetchError as e:
                failures += 1
                self.last_error = f"{datetime.now().strftime('%H:%M:%S')} {e}"
                log.warning("access [%s]: fetch failed: %s", self.id, e)
                self.stop_event.wait(max(self.cfg.frame_interval_s, 1.0))
                continue
            img = decode_jpeg(data)
            if img is None:
                failures += 1
                continue
            frames += 1
            last_img = img
            if self.cfg.debug_frames:
                self._save_debug(data)
            read = self.decoder.decode(img, self.cfg.crop)
            if read:
                break
            if self.cfg.frame_interval_s:
                self.stop_event.wait(self.cfg.frame_interval_s)
        with self.cond:
            self.deadline = 0.0
        if self.stop_event.is_set():
            self._set_scanning(False)
            return
        if read:
            self._on_read(read, last_img, frames)
        else:
            self._emit("scan_timeout", {"frames": frames, "fetch_failures": failures,
                                        "window_s": self.cfg.window_s})
            if last_img is not None:
                self._publish_image(last_img, [])
        self._set_scanning(False)

    def _on_read(self, read: QrRead, img: np.ndarray, frames: int):
        # Stage 2: report that a code was read, without its content.
        log.info("access [%s]: QR read, fingerprint %s, %d chars, %s decoder, %.0f ms, frame %d",
                 self.id, read.fingerprint, len(read.text), read.decoder, read.ms, frames)
        self._emit("qr_seen", {"fingerprint": read.fingerprint, "length": len(read.text),
                               "decoder": read.decoder, "decode_ms": round(read.ms, 1),
                               "frames": frames})
        self._publish_image(img, [read.quad])

    # --- outputs -------------------------------------------------------------------

    def _emit(self, event_type: str, attrs: dict):
        attrs = {"scanned_at": now_iso(), "scanner": self.id, **attrs}
        self.events.appendleft({"event_type": event_type, **attrs})
        self.mqtt.publish_event(self.id, event_type, attrs)

    def _set_scanning(self, on: bool):
        if on != self.scanning:
            self.scanning = on
            self.mqtt.publish_scanning(self.id, on)

    def _publish_image(self, img: np.ndarray, quads: list[np.ndarray]):
        clean = blackout(img, quads)
        px1, py1, px2, py2 = crop_pixels(clean.shape, self.cfg.crop)
        crop = clean[py1:py2, px1:px2].copy()
        for q in quads:
            pts = (q - np.array([px1, py1])).round().astype(np.int32)
            cv2.polylines(crop, [pts], True, (0, 255, 0), 2)
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            self.last_jpeg = buf.tobytes()
            self.mqtt.publish_image(self.id, self.last_jpeg)

    # --- frames -------------------------------------------------------------------

    def fetch(self) -> bytes:
        with self.fetch_lock:
            data = self.source.fetch().data
        return data

    def grab(self) -> bytes:
        """A frame for the panel's preview, with any readable QR code blacked out."""
        data = self.fetch()
        img = decode_jpeg(data)
        if img is None:
            raise FetchError("could not decode the frame")
        read = QrDecoder().decode(img)
        ok, buf = cv2.imencode(".jpg", blackout(img, [read.quad] if read else []),
                               [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()

    def _save_debug(self, data: bytes):
        try:
            os.makedirs(self.debug_dir, mode=0o700, exist_ok=True)
            with open(os.path.join(self.debug_dir, f"{time.time():.3f}.jpg"), "wb") as f:
                f.write(data)
            files = sorted(os.listdir(self.debug_dir))
            cutoff = time.time() - DEBUG_MAX_AGE_S
            for i, name in enumerate(files):
                try:
                    old = float(name[:-4]) < cutoff
                except ValueError:
                    old = False
                if old or i < len(files) - DEBUG_MAX_FILES:
                    os.remove(os.path.join(self.debug_dir, name))
        except OSError as e:
            log.warning("access [%s]: could not save debug frame: %s", self.id, e)

    def debug_files(self) -> list[str]:
        try:
            return sorted(os.listdir(self.debug_dir))
        except OSError:
            return []

    def status(self) -> dict:
        return {**self.cfg.to_dict(), "scanning": self.scanning, "events": list(self.events),
                "last_error": self.last_error, "has_image": self.last_jpeg is not None,
                "debug_frames_saved": len(self.debug_files())}
