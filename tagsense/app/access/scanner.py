"""One scanner: a thread that looks for access codes during a short window.

A Scan press opens a window of `window_s` seconds (a press during an open
window extends it). Every code in each frame is judged by the verifier (in
verifier.py, the only place codes are judged). The window ends at the first
verified code, a lockout, or verification being unavailable; otherwise with a
scan_timeout event. There is no continuous scanning.

Capture modes:
- stream: the go2rtc video is held open for the window and the newest frame
  is checked each time (stream.py): one start-up delay, then many frames.
- snapshot: single JPEGs, `frame_interval_s` apart. Used for HA cameras.
- auto (both sources set): snapshots from the HA camera until the go2rtc
  stream delivers its first frame, then the stream.
A stream that fails falls back to snapshots for the rest of the window.

Nothing a code says leaves this module except what the verifier vouches for:
foreign and invalid codes are reported by a short fingerprint only, images have
every code blacked out, and saved debug frames have TagSense codes blacked out.
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
from . import codes
from .config import ScannerConfig
from .qr import QrDecoder, QrRead, blackout
from .stream import FrameStream, open_capture

log = logging.getLogger("tagsense.access")

EVENTS_KEPT = 20
DEBUG_MAX_FILES = 50
DEBUG_MAX_AGE_S = 24 * 3600
ENDS_WINDOW = ("verified", "locked_out", "unavailable")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Scanner:
    def __init__(self, cfg: ScannerConfig, source: Source, mqtt, access_dir: str,
                 stop_event: threading.Event, capture_factory=open_capture,
                 verifier=None, snapshot_source: Source | None = None):
        self.cfg = cfg
        self.source = source                    # snapshots (and the stream URL for go2rtc)
        self.snapshot_source = snapshot_source  # auto mode: the HA camera
        self.mqtt = mqtt
        self.verifier = verifier                # None: every TagSense code is "unavailable"
        self.stop_event = stop_event
        self.debug_dir = os.path.join(access_dir, "debug", cfg.id)
        self.cond = threading.Condition()
        self.deadline = 0.0             # monotonic end of the open window, 0 = closed
        self.decoder = QrDecoder()
        self.capture_factory = capture_factory
        self.stream: FrameStream | None = None      # open only during a stream window
        self.last_stats: dict | None = None
        self.preview_img: np.ndarray | None = None  # the frame just checked, during a window
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

    @property
    def stream_url(self) -> str | None:
        return getattr(self.source, "stream_url", None)

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
                with self.cond:
                    self.deadline = 0.0
                self.preview_img = None
                self._set_scanning(False)

    def _close_window(self):
        with self.cond:
            self.deadline = 0.0
        self.preview_img = None

    def _window(self):
        self._set_scanning(True)
        if self.verifier is not None:
            try:
                until = self.verifier.locked_until(self.id)
            except Exception:       # noqa: BLE001 - cannot tell: fail closed
                until = None
                self._emit("unavailable", {"reason": "lockout state unreadable"})
                self._close_window()
                self._set_scanning(False)
                return
            if until:               # locked out: do not even look
                self._emit("locked_out", {"locked_until": datetime.fromtimestamp(
                    until, timezone.utc).isoformat(timespec="seconds")})
                self._close_window()
                self._set_scanning(False)
                return

        t0 = time.monotonic()
        mode = self.cfg.capture
        auto = mode == "auto" and self.snapshot_source is not None and self.stream_url
        snap_src = self.snapshot_source if auto else self.source
        st = {"capture": "snapshot", "frames": 0, "fetch_failures": 0}
        fetch_ms: list[float] = []
        stream = None
        if mode in ("stream", "auto") and self.stream_url:
            st["capture"] = "auto" if auto else "stream"
            stream = self.stream = FrameStream(self.stream_url, self.id, self.capture_factory)
        streaming = False                   # auto: has the stream delivered yet?
        seq = 0
        last_img, last_quads = None, []
        seen: set[str] = set()
        window_bad = 0
        final: str | None = None
        try:
            while not self.stop_event.is_set() and final is None:
                with self.cond:
                    left = self.deadline - time.monotonic()
                if left <= 0:
                    break
                img = data = None
                if stream is not None:
                    wait = 0.0 if (auto and not streaming) else min(1.0, left)
                    f, seq = stream.next_frame(seq, timeout=wait)
                    if f is not None:
                        img = f
                        if auto and not streaming:
                            streaming = True
                            st["stream_from_s"] = round(time.monotonic() - t0, 1)
                    elif stream.ended:      # carry on with snapshots
                        st["stream_error"] = stream.error
                        st["capture"] += ", stream failed: snapshots"
                        log.warning("access [%s]: %s; using snapshots for this scan",
                                    self.id, stream.error)
                        stream.close()
                        stream = self.stream = None
                        auto, snap_src = False, (self.snapshot_source or self.source)
                        continue
                    elif not auto or streaming:
                        continue            # waiting for the next stream frame
                if img is None:
                    t = time.monotonic()
                    try:            # never wait much past the end of the window
                        data = self.fetch(snap_src, timeout=max(2.0, left))
                    except FetchError as e:
                        st["fetch_failures"] += 1
                        self.last_error = f"{datetime.now().strftime('%H:%M:%S')} {e}"
                        log.warning("access [%s]: fetch failed: %s", self.id, e)
                        self.stop_event.wait(max(self.cfg.frame_interval_s, 1.0))
                        continue
                    fetch_ms.append((time.monotonic() - t) * 1000)
                    st["snapshots"] = st.get("snapshots", 0) + 1
                    img = decode_jpeg(data)
                    if img is None:
                        st["fetch_failures"] += 1
                        continue
                st["frames"] += 1
                if "first_frame_s" not in st:
                    st["first_frame_s"] = round(time.monotonic() - t0, 1)
                self.preview_img = img
                reads = self.decoder.decode_all(img, self.cfg.crop)
                if self.cfg.debug_frames:
                    self._save_debug_frame(img, data, reads)
                last_img, last_quads = img, [r.quad for r in reads]
                for r in reads:
                    if r.text in seen:
                        continue            # each distinct code once per window
                    seen.add(r.text)
                    final, window_bad = self._judge(r, img, reads, st, window_bad)
                    if final:
                        break
                if final is None and stream is None and self.cfg.frame_interval_s:
                    self.stop_event.wait(self.cfg.frame_interval_s)
        finally:
            if stream is not None:
                st["frames_decoded"] = stream.frames_decoded
                stream.close()
                self.stream = None
        self._close_window()
        if self.stop_event.is_set():
            self._set_scanning(False)
            return
        elapsed = time.monotonic() - t0
        if last_img is not None:
            h, w = last_img.shape[:2]
            st["resolution"] = f"{w}x{h}"
        if fetch_ms:
            st["fetch_ms"] = round(sum(fetch_ms) / len(fetch_ms))
        if st["frames"] > 1 and "first_frame_s" in st:
            st["checked_per_s"] = round((st["frames"] - 1) / max(elapsed - st["first_frame_s"], 0.1), 1)
        self.last_stats = {"at": now_iso(), "result": final or "timeout", **st}
        log.info("access [%s]: scan %s after %.1f s: capture %s, first frame %s s, %d frames checked"
                 "%s%s%s%s", self.id, final or "timed out", elapsed, st["capture"],
                 st.get("first_frame_s", "-"), st["frames"],
                 f" ({st['checked_per_s']}/s)" if "checked_per_s" in st else "",
                 f", stream from {st['stream_from_s']} s" if "stream_from_s" in st else "",
                 f", {st['frames_decoded']} decoded" if "frames_decoded" in st else "",
                 f", snapshot {st['fetch_ms']} ms" if "fetch_ms" in st else "")
        if final is None:
            if last_img is not None:
                self._publish_image(last_img, last_quads)
            self._emit("scan_timeout", {**st, "window_s": self.cfg.window_s})
        self._set_scanning(False)

    def _judge(self, r: QrRead, img: np.ndarray, reads: list[QrRead], st: dict,
               window_bad: int) -> tuple[str | None, int]:
        """Verify one code and report it. Returns (status if it ends the window, bad count)."""
        quads = [x.quad for x in reads]
        if codes.parse(r.text) is None:
            log.info("access [%s]: foreign QR code, fingerprint %s", self.id, r.fingerprint)
            self._publish_image(img, quads)
            self._emit("unrecognised", {"fingerprint": r.fingerprint})
            return None, window_bad
        if self.verifier is None:
            res_status, attrs, bad = "unavailable", {"reason": "no verifier"}, False
        else:
            res = self.verifier.verify(r.text, self.id)
            res_status, attrs, bad = res.status, dict(res.attrs), res.bad
        if res_status == "invalid":
            attrs = {"fingerprint": r.fingerprint}      # nothing the code itself claims
        if res_status == "verified":
            attrs.update(capture=st["capture"], first_frame_s=st.get("first_frame_s"),
                         frames=st["frames"])
        log.info("access [%s]: %s %s code%s", self.id, res_status, attrs.get("code_type", "TagSense"),
                 f" '{attrs['label']}' ({attrs.get('code_id')})" if attrs.get("label")
                 else f", fingerprint {r.fingerprint}")
        self._publish_image(img, quads)                  # before the event, so it is current
        self._emit(res_status, attrs)
        if bad and self.verifier is not None:
            window_bad += 1
            try:
                if self.verifier.register_bad(self.id, window_bad):
                    log.warning("access [%s]: too many bad codes: locked out", self.id)
                    self._emit("locked_out", {"locked_until": datetime.fromtimestamp(
                        self.verifier.locked_until(self.id) or time.time(),
                        timezone.utc).isoformat(timespec="seconds")})
                    return "locked_out", window_bad
            except Exception as e:      # noqa: BLE001 - cannot count: stop scanning
                log.error("access [%s]: could not record a bad code (%s); stopping this scan",
                          self.id, type(e).__name__)
                self._emit("unavailable", {"reason": "could not record a bad code"})
                return "unavailable", window_bad
        return (res_status if res_status in ENDS_WINDOW else None), window_bad

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

    def fetch(self, source: Source | None = None, timeout: float | None = None) -> bytes:
        src = source or self.source
        with self.fetch_lock:
            return (src.fetch(timeout) if timeout else src.fetch()).data

    def grab(self) -> bytes:
        """A frame for the panel's preview, with every readable code blacked out.
        During a scan it is the frame the scanner just checked (no extra fetch);
        otherwise a fresh snapshot (from the HA camera in auto mode: it is faster)."""
        img = self.preview_img
        if img is None and self.stream is not None:
            img = self.stream.latest()
        if img is None:
            img = decode_jpeg(self.fetch(self.snapshot_source))
        if img is None:
            raise FetchError("could not decode the frame")
        quads = [r.quad for r in QrDecoder().decode_all(img)]
        ok, buf = cv2.imencode(".jpg", blackout(img, quads), [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()

    def _save_debug_frame(self, img: np.ndarray, data: bytes | None, reads: list[QrRead]):
        """Save a raw frame for testing, but never a readable TagSense code."""
        ours = [r.quad for r in reads if codes.parse(r.text) is not None]
        if ours or data is None:
            ok, buf = cv2.imencode(".jpg", blackout(img, ours), [cv2.IMWRITE_JPEG_QUALITY, 92])
            if not ok:
                return
            data = buf.tobytes()
        self._save_debug(data)

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
        locked = None
        if self.verifier is not None:
            try:
                until = self.verifier.locked_until(self.id)
                locked = datetime.fromtimestamp(until, timezone.utc).isoformat(
                    timespec="seconds") if until else None
            except Exception:       # noqa: BLE001
                locked = "unknown"
        return {**self.cfg.to_dict(), "scanning": self.scanning, "events": list(self.events),
                "last_error": self.last_error, "has_image": self.last_jpeg is not None,
                "debug_frames_saved": len(self.debug_files()), "last_stats": self.last_stats,
                "stream_available": bool(self.stream_url), "locked_until": locked}
