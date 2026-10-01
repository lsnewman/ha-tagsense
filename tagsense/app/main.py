"""TagSense app entry point: MQTT wiring, scheduler loop and burst checks."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import threading
import time
from datetime import datetime, timezone

from . import decision as dec
from .analysis import FrameResult, analyse
from .detector import Detector, annotate, validate_crop
from .reference import Reference
from .mqtt_ha import HaMqtt, fmt, set_topic, supervisor_mqtt_config
from .scheduler import MANUAL, STARTUP, Scheduler
from .settings import Settings
from .sources import FetchError, build_source

log = logging.getLogger("tagsense")

DATA_DIR = os.environ.get("TAGSENSE_DATA", "/data")
VERSION = os.environ.get("TAGSENSE_VERSION", "dev")

DEFAULT_OPTIONS = {
    "object_name": "Object",
    "source": "go2rtc",
    "fallback_source": "none",
    "go2rtc_url": "",
    "go2rtc_stream": "",
    "camera_entity": "",
    "tag_id": 5,
    "max_aspect": 3.0,
    "burst_size": 5,
    "burst_interval_s": 1.0,
    "present_min_hits": 1,
    "absent_checks": 3,
    "confirm_delay_s": 45,
    "unknown_after_failures": 2,
    "sanity_min_ratio": 0.5,
    "sanity_min_h": 0.02,
    "log_level": "info",
}


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def load_options() -> dict:
    opts = dict(DEFAULT_OPTIONS)
    try:
        with open(os.path.join(DATA_DIR, "options.json")) as f:
            opts.update({k: v for k, v in json.load(f).items() if v is not None})
    except (OSError, ValueError) as e:
        log.warning("could not read options.json (%s); using defaults", e)
    return opts


class App:
    def __init__(self, opts: dict):
        self.opts = opts
        self.cfg = dec.Config(present_min_hits=int(opts["present_min_hits"]),
                              absent_checks=int(opts["absent_checks"]),
                              unknown_after_failures=int(opts["unknown_after_failures"]))
        self.detector = Detector(int(opts["tag_id"]), float(opts["max_aspect"]))
        self.source = build_source(opts)
        self.settings_path = os.path.join(DATA_DIR, "settings.json")
        self.state_path = os.path.join(DATA_DIR, "state.json")
        self.reference_path = os.path.join(DATA_DIR, "reference.json")
        self.reference = Reference.load(self.reference_path)
        self.settings = Settings.load(self.settings_path)
        self.decision = dec.Decision.load(self.state_path)
        self.sched = Scheduler(time.monotonic(), self.settings.poll_interval,
                               float(opts["confirm_delay_s"]), self.settings.enabled)
        self.cond = threading.Condition()
        self.stop_event = threading.Event()
        self.started = False
        self.fetch_failures_total = 0
        self.last_error: str | None = None
        self.last_phantom: dict | None = None
        self.last_diag: dict = {}
        self.last_bin_attrs: dict = {}
        self.last_status_attrs: dict = {}
        name = str(opts.get("object_name") or "").strip() or DEFAULT_OPTIONS["object_name"]
        self.mqtt = HaMqtt(supervisor_mqtt_config(opts), VERSION, name,
                           settings_values=self._settings_values,
                           on_setting=self._on_setting,
                           on_check_now=self._on_check_now,
                           on_connected=self._on_connected)

    # --- MQTT callbacks (paho thread) -------------------------------------

    def _settings_values(self) -> dict:
        with self.cond:
            s = self.settings
            return {"poll_interval": s.poll_interval, "crop_x1": s.crop_x1, "crop_y1": s.crop_y1,
                    "crop_x2": s.crop_x2, "crop_y2": s.crop_y2, "enabled": s.enabled}

    def _on_setting(self, key: str, value: str):
        with self.cond:
            changed = self.settings.set(key, value)
            if changed:
                log.info("setting %s -> %s", key, getattr(self.settings, key))
                self.settings.save(self.settings_path)
                now = time.monotonic()
                if key == "poll_interval":
                    self.sched.set_poll_interval(self.settings.poll_interval, now)
                elif key == "enabled":
                    self.sched.set_enabled(self.settings.enabled, now)
                    self._publish_state_locked()
                self.cond.notify_all()
        # Echo the stored (possibly clamped) value. Also overwrite the retained
        # command so a rejected value does not linger on the broker.
        stored = getattr(self.settings, key)
        self.mqtt.publish_setting(key, stored)
        if fmt(stored) != value:
            self.mqtt.pub(set_topic(key), fmt(stored))

    def _on_check_now(self):
        with self.cond:
            if not self.settings.enabled:
                log.info("check requested while disabled: ignored")
                return
            log.info("check requested")
            self.sched.request(MANUAL)
            self.cond.notify_all()

    def _on_connected(self):
        with self.cond:
            self._publish_state_locked()
            if self.last_diag:
                self.mqtt.publish_diagnostics(self.last_diag)
            if not self.started:
                self.started = True
                self.sched.request(STARTUP)
                self.cond.notify_all()

    # --- publishing -------------------------------------------------------

    def _publish_state_locked(self):
        value, reason = self.decision.reported(self.cfg, self.settings.enabled)
        self.mqtt.publish_state(value, reason, self.last_bin_attrs,
                                {**self.last_status_attrs, "miss_streak": self.decision.miss_streak,
                                 "last_seen": iso(self.decision.last_seen_ts)})

    # --- worker -----------------------------------------------------------

    def run(self):
        self.mqtt.start()
        while not self.stop_event.is_set():
            with self.cond:
                trigger = self.sched.due(time.monotonic())
                if trigger is None:
                    wait = self.sched.seconds_until_due(time.monotonic())
                    self.cond.wait(timeout=min(wait, 60.0) if wait is not None else 60.0)
                    continue
                crop = self.settings.crop
                raw_crop = (self.settings.crop_x1, self.settings.crop_y1,
                            self.settings.crop_x2, self.settings.crop_y2)
            try:
                self._check(trigger, crop, raw_crop)
            except Exception:       # never let one bad check kill the worker
                log.exception("check failed")
        self.mqtt.stop()

    def _burst(self, crop) -> tuple[list[FrameResult], list, int, set]:
        results, frames, failures, hashes = [], [], 0, set()
        n = int(self.opts["burst_size"])
        for i in range(n):
            if i and self.stop_event.wait(float(self.opts["burst_interval_s"])):
                break
            try:
                frame = self.source.fetch()
            except FetchError as e:
                failures += 1
                self.last_error = f"{datetime.now().strftime('%H:%M:%S')} {e}"
                log.warning("fetch failed: %s", e)
                continue
            hashes.add(hashlib.blake2b(frame.data, digest_size=16).digest())
            frames.append(frame)
            results.append(analyse(frame.data, self.detector, crop,
                                   float(self.opts["sanity_min_ratio"]),
                                   float(self.opts["sanity_min_h"])))
        return results, frames, failures, hashes

    def _check(self, trigger: str, crop, raw_crop):
        n = int(self.opts["burst_size"])
        results, frames, failures, hashes = self._burst(crop)
        valid = [r for r in results if r.valid]
        hits = [r for r in results if r.hit]
        attempted = len(frames) + failures
        res = dec.CheckResult(frames=attempted or n, fetch_failures=failures,
                              valid=len(valid), hits=len(hits))
        self.fetch_failures_total += failures

        decoded = [r for r in results if r.det is not None]
        best = max(hits, key=lambda r: r.det.size_px) if hits else None
        image_src = best or (decoded[-1] if decoded else None)
        warnings = []
        if validate_crop(raw_crop) != tuple(raw_crop):
            warnings.append(f"crop {raw_crop} invalid, using {crop}")
        if best:
            if gw := self.reference.warning(best.det):     # compare before learning
                warnings.append(gw)
                log.warning("geometry: %s", gw)
            self.reference.learn(best.det)
            self.reference.save(self.reference_path)
        phantoms = [ph for r in decoded for ph in r.det.others]
        for ph in phantoms:
            if ph.rejected_target:
                log.warning("shape gate: %s", ph.describe())
            else:
                log.info("phantom candidate: %s", ph.describe())
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
                self.last_bin_attrs = {
                    "last_seen": iso(self.decision.last_seen_ts), "size_px": round(d.size_px, 1),
                    "centre": [round(v, 4) for v in d.centre_norm], "area_px": round(d.area_px),
                    "source": next(f.source for f, r in zip(frames, results) if r is best)}
            else:
                self.last_bin_attrs = {**self.last_bin_attrs,
                                       "last_seen": iso(self.decision.last_seen_ts)}
            self.last_status_attrs = {
                "trigger": trigger, "outcome": outcome, "frames": attempted,
                "valid": len(valid), "hits": len(hits), "last_check": iso(now)}
            self.last_diag = {
                "last_source": ",".join(sorted({f.source for f in frames})) or None,
                "resolution": image_src.check.resolution if image_src else None,
                "last_check": iso(now),
                "last_error": self.last_error,
                "warning": "; ".join(warnings) or None,
                "fetch_ms": _mean([f.fetch_ms for f in frames]),
                "detect_ms": _mean([r.det.ms for r in decoded]),
                "fetch_failures": self.fetch_failures_total,
                "discard_rate": 100.0 * (attempted - len(valid)) / attempted if attempted else None,
                "unique_frames": len(hashes),
                "tag_size_px": best.det.size_px if best else None,
                "sanity_ratio": _mean([r.check.ratio for r in decoded]),
                "phantom_decodes": len(phantoms),
                "miss_streak": self.decision.miss_streak,
                "crop_mean": _mean([r.det.mean for r in stats_src]),
                "crop_std": _mean([r.det.std for r in stats_src]),
            }
            crop_stats = {"per_frame_mean": [round(r.det.mean, 1) for r in decoded],
                          "per_frame_std": [round(r.det.std, 1) for r in decoded],
                          "crop_std": self.last_diag["crop_std"]}
            phantom_attrs = {"this_check": [p.describe() for p in phantoms],
                             "last_phantom": self.last_phantom}
            self._publish_state_locked()
            self.mqtt.publish_diagnostics(self.last_diag, crop_stats, phantom_attrs,
                                          self.reference.as_attrs())
        if image_src:
            self.mqtt.publish_image(annotate(image_src.check.bgr, image_src.det, phantoms))
        log.info("check %s: %d/%d valid, %d hits, %d fetch failures -> %s; reported %s (%s), "
                 "streak %d", trigger, len(valid), attempted, len(hits), failures, outcome,
                 value, reason, self.decision.miss_streak)

    def shutdown(self, *_):
        log.info("stopping")
        self.stop_event.set()
        with self.cond:
            self.cond.notify_all()


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def main():
    opts = load_options()
    logging.basicConfig(level=str(opts.get("log_level", "info")).upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("TagSense %s starting (object=%s, source=%s, fallback=%s, tag_id=%s)", VERSION,
             opts["object_name"], opts["source"], opts["fallback_source"], opts["tag_id"])
    if int(opts["present_min_hits"]) > int(opts["burst_size"]):
        log.warning("present_min_hits %s > burst_size %s: clamping",
                    opts["present_min_hits"], opts["burst_size"])
        opts["present_min_hits"] = opts["burst_size"]
    try:
        app = App(opts)
    except (ValueError, RuntimeError) as e:
        log.error("configuration error: %s", e)
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, app.shutdown)
    signal.signal(signal.SIGINT, app.shutdown)
    app.run()


if __name__ == "__main__":
    main()
