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
from .analysis import FrameResult, analyse_image
from .checklog import CheckLog
from .detector import Detector, Phantom, annotate, quad_aspect, validate_crop
from .ha_notify import Notifier
from .mqtt_ha import ObjectPublisher, fmt
from .objects import ObjectConfig, object_dir
from .reference import MIN_HITS, Reference
from .rotation import circular_mean, fmt_deg, lid_angle, snap
from .scheduler import MANUAL, Scheduler
from .settings import Settings
from .snapshots import Snapshots
from .sources import Frame

log = logging.getLogger("tagsense")


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


HISTORY_LEN = 50


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _norm(corners, frame_wh) -> np.ndarray:
    w, h = frame_wh
    return np.asarray(corners, dtype=np.float64) / (w, h)


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
    min_size_ratio: float = 0.5     # 0 = off


class TrackedObject:
    def __init__(self, oc: ObjectConfig, tuning: Tuning, data_dir: str,
                 publisher: ObjectPublisher, cond: threading.Condition,
                 notifier: Notifier | None = None):
        self.oc = oc
        self.tuning = tuning
        self.cfg = tuning.cfg
        self.pub = publisher
        self.cond = cond
        self.notifier = notifier or Notifier()
        d = object_dir(data_dir, oc.id)
        self.settings_path = os.path.join(d, "settings.json")
        self.state_path = os.path.join(d, "state.json")
        self.reference_path = os.path.join(d, "reference.json")
        self.detector = Detector(oc.tag_id, tuning.max_aspect, oc.tag_family)
        self.settings = Settings.load(self.settings_path)
        self.decision = dec.Decision.load(self.state_path)
        self.reference = Reference.load(self.reference_path)
        self.sched = Scheduler(time.monotonic(), self.settings.poll_interval,
                               tuning.confirm_delay_s, self.settings.enabled)
        self.camera = None                  # CameraWorker, set by the app
        self.known_ids: set[int] = set()   # other objects' tags (same family) on this camera
        self.last_phantom: dict | None = None
        # For the web UI
        self.history: deque[dict] = deque(maxlen=HISTORY_LEN)
        self.last_jpeg: bytes | None = None
        self.last_phantom_jpeg: bytes | None = None
        self.last_corners: list | None = None      # normalised corners of the last hit
        self.last_diag: dict = {}
        self.last_presence_attrs: dict = {}
        self.last_status_attrs: dict = {}
        # Rotation: the stepped value reported and the exact angle behind it
        # (kept while the object is away; its entities are unavailable then).
        self.rotation: float | None = None
        self.rotation_angle: float | None = None
        self.snapshots = Snapshots(os.path.join(d, "snapshots"))
        self.checklog = CheckLog(os.path.join(d, "checks.jsonl"))
        # Rejection alert: reasons from the latest check with rejected target reads,
        # and the message last sent to HA ("?" = maybe one left from before a restart).
        self.last_reject_reasons: list[str] = []
        self._notified: str | None = "?" if self.decision.reject_streak >= dec.ALERT_AFTER else None
        # Per-burst analysis, filled frame by frame by the camera worker
        self._burst_crop = None
        self._results: list[FrameResult] | None = None

    @property
    def id(self) -> str:
        return self.oc.id

    # --- MQTT-driven (paho thread) ----------------------------------------

    def settings_values(self) -> dict:
        s = self.settings
        return {"poll_interval": s.poll_interval, "crop_x1": s.crop_x1, "crop_y1": s.crop_y1,
                "crop_x2": s.crop_x2, "crop_y2": s.crop_y2, "enabled": s.enabled,
                "rotation_steps": s.rotation_steps}

    def on_setting(self, key: str, value: str, from_api: bool = False):
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
                elif key == "rotation_steps" and self.rotation_angle is not None:
                    self.rotation = snap(self.rotation_angle, self.settings.rotation_steps)
                    self._publish_rotation_locked()
                self.cond.notify_all()
            stored = getattr(self.settings, key)
        # Echo the stored (possibly clamped) value. Overwrite the retained command
        # if it was rejected, or if the change came from the panel: otherwise the
        # old retained command is replayed (and briefly applied) on the next start.
        self.pub.publish_setting(key, stored)
        if from_api or fmt(stored) != value:
            self.pub.pub(self.pub.t.set(key), fmt(stored))

    def on_check_now(self):
        with self.cond:
            if not self.settings.enabled:
                log.info("[%s] check requested while disabled: ignored", self.id)
                return
            log.info("[%s] check requested", self.id)
            self.sched.request(MANUAL)
            self.cond.notify_all()

    def reset_reference(self):
        """Forget the learned usual position and size (the object has moved).
        The size gate and geometry warnings stay off until MIN_HITS new hits."""
        with self.cond:
            self.reference = Reference()        # also forgets 0 deg: set again by the next hit
            self.reference.save(self.reference_path)
            self.rotation = self.rotation_angle = None
            self.decision.reject_streak = 0
            self.decision.save(self.state_path)
            self._publish_state_locked()
        log.info("[%s] learned position reset", self.id)
        self._sync_alert()

    def set_orientation(self) -> bool:
        """Make the tag's current corner layout 0 deg. False if there is no hit to use."""
        with self.cond:
            present = self.decision.reported(self.cfg, self.settings.enabled)[0] == dec.PRESENT
            if not self.last_corners or not present:
                log.warning("[%s] set orientation: the object is not present", self.id)
                return False
            self.reference.set_orient(self.last_corners)
            self.reference.save(self.reference_path)
            self.rotation, self.rotation_angle = 0.0, 0.0
            self._publish_rotation_locked()
        log.info("[%s] current orientation set as 0°", self.id)
        return True

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
        self.pub.publish_problem(self._alert_locked())
        self._publish_rotation_locked()

    def _publish_rotation_locked(self):
        present = self.decision.reported(self.cfg, self.settings.enabled)[0] == dec.PRESENT
        a = self.rotation_angle
        self.pub.publish_rotation(present, fmt_deg(self.rotation) if self.rotation is not None else None,
                                  round(a, 1) if a is not None else None, self.settings.rotation_steps)

    def rotation_status(self) -> dict:
        r, a = self.rotation, self.rotation_angle
        return {"value": fmt_deg(r) if r is not None else None,
                "angle": round(a, 1) if a is not None else None,
                "steps": self.settings.rotation_steps, "zero_set": self.reference.orient is not None,
                "zero_corners": self.reference.orient}

    # --- rejection alert --------------------------------------------------

    def _alert_locked(self) -> dict | None:
        """The target was read but rejected in ALERT_AFTER checks in a row."""
        streak = self.decision.reject_streak
        if streak < dec.ALERT_AFTER or not self.settings.enabled:
            return None
        reasons = self.last_reject_reasons
        size = any(r.startswith("size") for r in reasons)
        aspect = any(r.startswith("aspect") for r in reasons)
        t, name = self.tuning, self.oc.name
        msg = [f"Tag {self.oc.tag_id} was read in {dec.ALERT_AFTER} or more checks in a row, "
               "but every read was rejected by the shape gate, so they count as misses "
               f"({name} is reported absent once enough of them add up)."]
        if size:
            msg.append(f"Too small: under {t.min_size_ratio:.0%} of its learned usual size.")
        if aspect:
            msg.append(f"Too skewed: aspect above max_aspect {t.max_aspect:g}.")
        if not (size or aspect):
            msg.append("See the TagSense panel for the reason.")
        msg.append(f"If {name} is not there, these are phantom reads and nothing needs "
                   f"changing. If {name} is actually there,")
        if size:
            msg.append("it has probably moved further from the camera: press Reset learned "
                       "position in the TagSense panel (or on its device in HA), or lower "
                       "min_size_ratio in the app options (0 = off).")
        if aspect:
            msg.append(("If the tag is also" if size else "the tag is probably") + " seen at a "
                       "steeper angle now: raise max_aspect in the app options.")
        if not (size or aspect):
            msg.append("check the crop and the tag.")
        return {"reason": "size" if size and not aspect else "aspect" if aspect and not size
                else "size_and_aspect" if size else "unknown",
                "checks": streak, "detail": "; ".join(reasons) or None, "message": " ".join(msg)}

    def _sync_alert(self):
        """Raise, update or dismiss the HA notification (call without the lock)."""
        with self.cond:
            alert = self._alert_locked()
        msg = alert["message"] if alert else None
        if msg == self._notified:
            return
        nid = f"tagsense_{self.id}_rejected"
        if msg:
            log.warning("[%s] alert: %s", self.id, msg)
            self.notifier.notify(nid, f"TagSense: {self.oc.name}", msg)
        else:
            log.info("[%s] alert cleared", self.id)
            self.notifier.dismiss(nid)
        self._notified = msg

    def dismiss_alert(self):
        """The object is being deleted."""
        if self._notified is not None:
            self.notifier.dismiss(f"tagsense_{self.id}_rejected")
            self._notified = None

    # --- worker-driven ----------------------------------------------------

    def crop(self):
        s = self.settings
        raw = (s.crop_x1, s.crop_y1, s.crop_x2, s.crop_y2)
        return s.crop, raw

    def begin_burst(self):
        """The camera worker is starting a burst: analyse it frame by frame."""
        with self.cond:
            self._burst_crop = self.crop()
        self._results = []

    def analyse_frame(self, img: np.ndarray | None) -> FrameResult:
        t = self.tuning
        r = analyse_image(img, self.detector, self._burst_crop[0], t.sanity_min_ratio, t.sanity_min_h)
        self._size_gate(r)
        self._results.append(r)
        return r

    def satisfied(self) -> bool:
        """Early burst exit: already present and the first present_min_hits
        frames all hit, so the rest of the burst cannot change anything."""
        rs = self._results or []
        with self.cond:
            present = self.decision.state == dec.PRESENT
        return present and len(rs) >= self.cfg.present_min_hits and all(r.hit for r in rs)

    def process(self, trigger: str, burst: Burst):
        results, self._results = self._results, None
        if results is None or len(results) != len(burst.images):
            self.begin_burst()       # not analysed while fetching: do it now
            results = [self.analyse_frame(img) for img in burst.images]
            self._results = None
        crop, raw_crop = self._burst_crop
        frames = burst.frames
        valid = [r for r in results if r.valid]
        hits = [r for r in results if r.hit]
        decoded = [r for r in results if r.det is not None]
        rejected_frames = [r for r in decoded if any(p.rejected_target for p in r.det.others)]
        attempted = burst.attempted
        res = dec.CheckResult(frames=attempted or burst.requested, fetch_failures=burst.failures,
                              valid=len(valid), hits=len(hits), rejected=len(rejected_frames))

        best = max(hits, key=lambda r: r.det.size_px) if hits else None
        aspect = max(quad_aspect(r.det.corners) for r in hits) if hits else None
        image_src = best or (decoded[-1] if decoded else None)
        warnings = []
        if validate_crop(raw_crop) != tuple(raw_crop):
            warnings.append(f"crop {raw_crop} invalid, using {crop}")
        angle = None
        if best:
            if gw := self.reference.warning(best.det):     # compare before learning
                warnings.append(gw)
                log.warning("[%s] geometry: %s", self.id, gw)
            self.reference.learn(best.det)
            if self.reference.orient is None:
                self.reference.set_orient(_norm(best.det.corners, best.det.frame_wh))
                log.info("[%s] rotation 0° set from the first hit", self.id)
            self.reference.save(self.reference_path)
            ref = self.reference.orient
            angle = circular_mean([a for r in hits if (a := lid_angle(
                ref, _norm(r.det.corners, r.det.frame_wh))) is not None])
        phantoms = [ph for r in decoded for ph in r.det.others
                    if ph.rejected_target or ph.id not in self.known_ids]
        for ph in phantoms:
            if ph.rejected_target:
                log.warning("[%s] shape gate: %s", self.id, ph.describe())
            else:
                log.info("[%s] ignored decode: %s", self.id, ph.describe())
        if rejected := [ph for ph in phantoms if ph.rejected_target]:
            warnings.append(f"tag id {self.detector.tag_id} decode rejected by shape gate: "
                            + "; ".join(sorted({ph.reason for ph in rejected})))
        stats_src = valid or decoded
        now = time.time()
        if phantoms:
            self.last_phantom = {"at": iso(now), "ids": sorted({p.id for p in phantoms}),
                                 "detail": phantoms[0].describe()}

        with self.cond:
            before, _ = self.decision.reported(self.cfg, self.settings.enabled)
            outcome = self.decision.update(res, self.cfg, now, self.settings.poll_interval)
            self.decision.save(self.state_path)
            if rejected:
                self.last_reject_reasons = sorted({ph.reason for ph in rejected})
            self.sched.on_result(trigger, outcome, self.decision.miss_streak,
                                 self.cfg.absent_checks, time.monotonic())
            prev_rotation = self.rotation
            if angle is not None:
                self.rotation_angle = angle
                self.rotation = snap(angle, self.settings.rotation_steps, self.rotation)
            rotated = prev_rotation is not None and self.rotation != prev_rotation
            if rotated:
                log.info("[%s] rotation %s° -> %s° (exact %.1f°)", self.id, fmt_deg(prev_rotation),
                         fmt_deg(self.rotation), angle)
            value, reason = self.decision.reported(self.cfg, self.settings.enabled)
            if best:
                d = best.det
                src = next(f.source for f, r in zip(frames, results) if r is best)
                self.last_presence_attrs = {
                    "last_seen": iso(self.decision.last_seen_ts), "size_px": round(d.size_px, 1),
                    "centre": [round(v, 4) for v in d.centre_norm], "area_px": round(d.area_px),
                    "aspect": round(aspect, 2), "tag_id": self.oc.tag_id, "source": src}
            else:
                self.last_presence_attrs = {**self.last_presence_attrs,
                                            "last_seen": iso(self.decision.last_seen_ts)}
            self.last_status_attrs = {
                "trigger": trigger, "outcome": outcome, "frames": attempted,
                "valid": len(valid), "hits": len(hits), "last_check": iso(now)}
            self.last_diag = {
                "last_source": ",".join(sorted({f.source for f in frames})) or None,
                "resolution": image_src.check.resolution if image_src else None,
                "last_error": burst.last_error,
                "warning": "; ".join(warnings) or None,
                "fetch_ms": _mean([f.fetch_ms for f in frames]),
                "detect_ms": _mean([r.det.ms for r in decoded]),
                "fetch_failures": burst.fetch_failures_total,
                "discard_rate": 100.0 * (attempted - len(valid)) / attempted if attempted else None,
                "unique_frames": len(burst.hashes),
                "tag_size_px": best.det.size_px if best else None,
                "tag_aspect": aspect,
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
                "aspect": round(aspect, 2) if aspect else None,
                "rotation": fmt_deg(self.rotation) if angle is not None else None,
                "angle": round(angle, 1) if angle is not None else None,
                "warning": self.last_diag["warning"],
                "phantoms": [p.describe() for p in phantoms]})
            crop_std = self.last_diag["crop_std"]
            self.checklog.append({
                "t": round(now, 1), "hits": len(hits), "valid": len(valid), "frames": attempted,
                "contrast": round(crop_std, 1) if crop_std is not None else None,
                "aspect": round(aspect, 2) if aspect else None,
                "rotation": fmt_deg(self.rotation) if angle is not None else None,
                "outcome": outcome, "reported": value})
        jpeg = None
        if image_src is not None:
            jpeg = annotate(image_src.check.bgr, image_src.det, phantoms)
            self.last_jpeg = jpeg
            if phantoms:
                self.last_phantom_jpeg = jpeg
            self.pub.publish_image(jpeg)
        if value != before:
            self.snapshots.add(iso(now), now, before, value, reason, jpeg)
        if rotated:
            self.snapshots.add(iso(now), now, f"{fmt_deg(prev_rotation)}°", f"{fmt_deg(self.rotation)}°",
                               f"rotated (exact {angle:.1f}°)", jpeg)
        self._sync_alert()
        log.info("[%s] check %s: %d/%d valid, %d hits, %d fetch failures -> %s; reported %s (%s), "
                 "streak %d", self.id, trigger, len(valid), attempted, len(hits), burst.failures,
                 outcome, value, reason, self.decision.miss_streak)
        return outcome

    def _size_gate(self, r: FrameResult):
        """Shape gate, part 2: reject a target decode far smaller than the learned
        usual size (phantoms in texture decode tiny). Off until MIN_HITS are learned."""
        ref, limit = self.reference, self.tuning.min_size_ratio
        d = r.det
        if limit <= 0 or not ref.ready or ref.size <= 0 or d is None or not d.found:
            return
        ratio = (d.size_px / d.frame_wh[1]) / ref.size
        if ratio < limit:
            d.others.append(Phantom(d.tag_id, d.corners, d.frame_wh, rejected_target=True,
                                    reason=f"size {ratio:.0%} of usual < {limit:.0%}"))
            d.found, d.corners = False, None

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
                | {"centre": [self.reference.cx, self.reference.cy] if self.reference.hits else None,
                   "size": self.reference.size if self.reference.hits else None,
                   "min_hits": MIN_HITS},
                "alert": self._alert_locked(),
                "rotation": self.rotation_status(),
                "last_corners": self.last_corners,
                "diag": self.last_diag,
                "last_phantom": self.last_phantom,
                "has_image": self.last_jpeg is not None,
                "has_phantom_image": self.last_phantom_jpeg is not None,
            }

    def history_list(self) -> list[dict]:
        with self.cond:
            return list(self.history)

    def chart_points(self) -> list[dict]:
        with self.cond:
            return self.checklog.points()
