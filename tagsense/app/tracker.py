"""One tracked object: its settings, decision, scheduler, reference and entities.

A camera worker fetches a burst of frames and hands it to every object on that
camera; each object judges it with its own crop and tag ID (process()).
All mutable state is guarded by the owning camera's Condition.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from . import decision as dec
from .analysis import analyse_image
from .detector import Detector, annotate, validate_crop
from .mqtt_ha import ObjectPublisher, fmt
from .objects import ObjectConfig, object_dir
from .reference import Reference
from .scheduler import MANUAL, Scheduler
from .settings import Settings
from .sources import Frame

log = logging.getLogger("tagsense")


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


HISTORY_LEN = 50


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


@dataclass
class Burst:
    """Frames fetched by a camera worker, decoded once, shared by its objects."""
    frames: list[Frame] = field(default_factory=list)
    images: list[np.ndarray | None] = field(default_factory=list)   # aligned with frames
    failures: int = 0
    hashes: set = field(default_factory=set)
    last_error: str | None = None          # camera's most recent fetch error
    fetch_failures_total: int = 0          # camera's running total
    requested: int = 0

    @property
    def attempted(self) -> int:
        return len(self.frames) + self.failures


@dataclass
class Tuning:
    """Global detection/decision options shared by all objects."""
    cfg: dec.Config
    max_aspect: float
    confirm_delay_s: float
    sanity_min_ratio: float
    sanity_min_h: float


class TrackedObject:
    def __init__(self, oc: ObjectConfig, tuning: Tuning, data_dir: str,
                 publisher: ObjectPublisher, cond: threading.Condition):
        self.oc = oc
        self.tuning = tuning
        self.cfg = tuning.cfg
        self.pub = publisher
        self.cond = cond
        d = object_dir(data_dir, oc.id)
        self.settings_path = os.path.join(d, "settings.json")
        self.state_path = os.path.join(d, "state.json")
        self.reference_path = os.path.join(d, "reference.json")
        self.detector = Detector(oc.tag_id, tuning.max_aspect)
        self.settings = Settings.load(self.settings_path)
        self.decision = dec.Decision.load(self.state_path)
        self.reference = Reference.load(self.reference_path)
        self.sched = Scheduler(time.monotonic(), self.settings.poll_interval,
                               tuning.confirm_delay_s, self.settings.enabled)
        self.camera = None                  # CameraWorker, set by the app
        self.known_ids: set[int] = set()   # other objects' tags on this camera: not phantoms
        self.last_phantom: dict | None = None
        # For the web UI
        self.history: deque[dict] = deque(maxlen=HISTORY_LEN)
        self.last_jpeg: bytes | None = None
        self.last_phantom_jpeg: bytes | None = None
        self.last_corners: list | None = None      # normalised corners of the last hit
        self.last_diag: dict = {}
        self.last_presence_attrs: dict = {}
        self.last_status_attrs: dict = {}

    @property
    def id(self) -> str:
        return self.oc.id

    # --- MQTT-driven (paho thread) ----------------------------------------

    def settings_values(self) -> dict:
        s = self.settings
        return {"poll_interval": s.poll_interval, "crop_x1": s.crop_x1, "crop_y1": s.crop_y1,
                "crop_x2": s.crop_x2, "crop_y2": s.crop_y2, "enabled": s.enabled}

    def on_setting(self, key: str, value: str):
        with self.cond:
            changed = self.settings.set(key, value)
            if changed:
                log.info("[%s] setting %s -> %s", self.id, key, getattr(self.settings, key))
                self.settings.save(self.settings_path)
                now = time.monotonic()
                if key == "poll_interval":
                    self.sched.set_poll_interval(self.settings.poll_interval, now)
                elif key == "enabled":
                    self.sched.set_enabled(self.settings.enabled, now)
                    self._publish_state_locked()
                self.cond.notify_all()
            stored = getattr(self.settings, key)
        # Echo the stored (possibly clamped) value; overwrite a rejected retained command.
        self.pub.publish_setting(key, stored)
        if fmt(stored) != value:
            self.pub.pub(self.pub.t.set(key), fmt(stored))

    def on_check_now(self):
        with self.cond:
            if not self.settings.enabled:
                log.info("[%s] check requested while disabled: ignored", self.id)
                return
            log.info("[%s] check requested", self.id)
            self.sched.request(MANUAL)
            self.cond.notify_all()

    def request(self, trigger: str):
        with self.cond:
            self.sched.request(trigger)
            self.cond.notify_all()

    def publish_all(self, also_commands: bool = False):
        """Discovery, settings and the current state (on connect / HA restart)."""
        with self.cond:
            values = self.settings_values()
        self.pub.publish_discovery()
        self.pub.publish_settings(values, also_commands)
        with self.cond:
            self._publish_state_locked()
            if self.last_diag:
                self.pub.publish_diagnostics(self.last_diag)

    def _publish_state_locked(self):
        value, reason = self.decision.reported(self.cfg, self.settings.enabled)
        self.pub.publish_state(value, reason, self.last_presence_attrs,
                               {**self.last_status_attrs, "miss_streak": self.decision.miss_streak,
                                "last_seen": iso(self.decision.last_seen_ts)})

    # --- worker-driven ----------------------------------------------------

    def crop(self):
        s = self.settings
        raw = (s.crop_x1, s.crop_y1, s.crop_x2, s.crop_y2)
        return s.crop, raw

    def process(self, trigger: str, burst: Burst):
        with self.cond:
            crop, raw_crop = self.crop()
        t = self.tuning
        results = [analyse_image(img, self.detector, crop, t.sanity_min_ratio, t.sanity_min_h)
                   for img in burst.images]
        frames = burst.frames
        valid = [r for r in results if r.valid]
        hits = [r for r in results if r.hit]
        attempted = burst.attempted
        res = dec.CheckResult(frames=attempted or burst.requested, fetch_failures=burst.failures,
                              valid=len(valid), hits=len(hits))

        decoded = [r for r in results if r.det is not None]
        best = max(hits, key=lambda r: r.det.size_px) if hits else None
        image_src = best or (decoded[-1] if decoded else None)
        warnings = []
        if validate_crop(raw_crop) != tuple(raw_crop):
            warnings.append(f"crop {raw_crop} invalid, using {crop}")
        if best:
            if gw := self.reference.warning(best.det):     # compare before learning
                warnings.append(gw)
                log.warning("[%s] geometry: %s", self.id, gw)
            self.reference.learn(best.det)
            self.reference.save(self.reference_path)
        phantoms = [ph for r in decoded for ph in r.det.others
                    if ph.rejected_target or ph.id not in self.known_ids]
        for ph in phantoms:
            if ph.rejected_target:
                log.warning("[%s] shape gate: %s", self.id, ph.describe())
            else:
                log.info("[%s] phantom candidate: %s", self.id, ph.describe())
        if any(ph.rejected_target for ph in phantoms):
            warnings.append(f"tag id {self.detector.tag_id} decode rejected by shape gate "
                            f"(aspect > {self.detector.max_aspect:g})")
        stats_src = valid or decoded
        now = time.time()
        if phantoms:
            self.last_phantom = {"at": iso(now), "ids": sorted({p.id for p in phantoms}),
                                 "detail": phantoms[0].describe()}

        with self.cond:
            outcome = self.decision.update(res, self.cfg, now, self.settings.poll_interval)
            self.decision.save(self.state_path)
            self.sched.on_result(trigger, outcome, self.decision.miss_streak,
                                 self.cfg.absent_checks, time.monotonic())
            value, reason = self.decision.reported(self.cfg, self.settings.enabled)
            if best:
                d = best.det
                src = next(f.source for f, r in zip(frames, results) if r is best)
                self.last_presence_attrs = {
                    "last_seen": iso(self.decision.last_seen_ts), "size_px": round(d.size_px, 1),
                    "centre": [round(v, 4) for v in d.centre_norm], "area_px": round(d.area_px),
                    "tag_id": self.oc.tag_id, "source": src}
            else:
                self.last_presence_attrs = {**self.last_presence_attrs,
                                            "last_seen": iso(self.decision.last_seen_ts)}
            self.last_status_attrs = {
                "trigger": trigger, "outcome": outcome, "frames": attempted,
                "valid": len(valid), "hits": len(hits), "last_check": iso(now)}
            self.last_diag = {
                "last_source": ",".join(sorted({f.source for f in frames})) or None,
                "resolution": image_src.check.resolution if image_src else None,
                "last_check": iso(now),
                "last_error": burst.last_error,
                "warning": "; ".join(warnings) or None,
                "fetch_ms": _mean([f.fetch_ms for f in frames]),
                "detect_ms": _mean([r.det.ms for r in decoded]),
                "fetch_failures": burst.fetch_failures_total,
                "discard_rate": 100.0 * (attempted - len(valid)) / attempted if attempted else None,
                "unique_frames": len(burst.hashes),
                "tag_size_px": best.det.size_px if best else None,
                "sanity_ratio": _mean([r.check.ratio for r in decoded]),
                "phantom_decodes": len(phantoms),
                "miss_streak": self.decision.miss_streak,
                "crop_mean": _mean([r.det.mean for r in stats_src]),
                "crop_std": _mean([r.det.std for r in stats_src]),
            }
            attrs = {
                "crop_stats": {"per_frame_mean": [round(r.det.mean, 1) for r in decoded],
                               "per_frame_std": [round(r.det.std, 1) for r in decoded],
                               "crop_std": self.last_diag["crop_std"]},
                "phantoms": {"this_check": [p.describe() for p in phantoms],
                             "last_phantom": self.last_phantom},
                "reference": self.reference.as_attrs(),
            }
            self._publish_state_locked()
            self.pub.publish_diagnostics(self.last_diag, attrs)
            if best:
                w, h = best.det.frame_wh
                self.last_corners = [[round(float(x) / w, 4), round(float(y) / h, 4)]
                                     for x, y in best.det.corners]
            self.history.appendleft({
                "at": iso(now), "trigger": trigger, "outcome": outcome, "frames": attempted,
                "valid": len(valid), "hits": len(hits), "reported": value, "reason": reason,
                "warning": self.last_diag["warning"],
                "phantoms": [p.describe() for p in phantoms]})
        if image_src is not None:
            jpeg = annotate(image_src.check.bgr, image_src.det, phantoms)
            self.last_jpeg = jpeg
            if phantoms:
                self.last_phantom_jpeg = jpeg
            self.pub.publish_image(jpeg)
        log.info("[%s] check %s: %d/%d valid, %d hits, %d fetch failures -> %s; reported %s (%s), "
                 "streak %d", self.id, trigger, len(valid), attempted, len(hits), burst.failures,
                 outcome, value, reason, self.decision.miss_streak)
        return outcome

    # --- web UI -----------------------------------------------------------

    def status(self) -> dict:
        with self.cond:
            value, reason = self.decision.reported(self.cfg, self.settings.enabled)
            return {
                **self.oc.to_dict(),
                "state": value, "reason": reason,
                "last_check": iso(self.decision.last_check_ts),
                "last_seen": iso(self.decision.last_seen_ts),
                "miss_streak": self.decision.miss_streak,
                "settings": self.settings_values(),
                "reference": self.reference.as_attrs()
                | {"centre": [self.reference.cx, self.reference.cy] if self.reference.hits else None},
                "last_corners": self.last_corners,
                "diag": self.last_diag,
                "last_phantom": self.last_phantom,
                "has_image": self.last_jpeg is not None,
                "has_phantom_image": self.last_phantom_jpeg is not None,
            }

    def history_list(self) -> list[dict]:
        with self.cond:
            return list(self.history)
