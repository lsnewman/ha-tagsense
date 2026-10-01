"""TagSense app entry point: options, objects, camera workers and MQTT wiring."""
from __future__ import annotations

import json
import logging
import os
import signal
import threading

from . import decision as dec
from .cameras import CameraWorker
from .mqtt_ha import MqttClient, ObjectPublisher, legacy_topics, supervisor_mqtt_config
from .objects import ConfigError, ObjectConfig, migrate_legacy_files, parse_objects
from .scheduler import STARTUP
from .sources import build_source
from .tracker import TrackedObject, Tuning

log = logging.getLogger("tagsense")

DATA_DIR = os.environ.get("TAGSENSE_DATA", "/data")
VERSION = os.environ.get("TAGSENSE_VERSION", "dev")
LEGACY_CLEARED_MARKER = ".mqtt_v3"
LEGACY_CLEAR_DELAY_S = 3.0   # let HA remove the old entities before adding new ones

DEFAULT_OPTIONS = {
    "objects": [],
    "go2rtc_url": "",
    "fallback_source": "none",
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


def load_options() -> dict:
    opts = dict(DEFAULT_OPTIONS)
    try:
        with open(os.path.join(DATA_DIR, "options.json")) as f:
            opts.update({k: v for k, v in json.load(f).items() if v is not None})
    except (OSError, ValueError) as e:
        log.warning("could not read options.json (%s); using defaults", e)
    return opts


def camera_key(oc: ObjectConfig, fallback: str) -> tuple:
    """Objects sharing a key share one worker (one fetch per burst)."""
    key = oc.camera_key
    if fallback not in ("none", oc.source):
        key += (oc.camera_entity if fallback == "ha_camera" else oc.go2rtc_stream,)
    return key


def make_source(oc: ObjectConfig, opts: dict):
    fallback = opts.get("fallback_source", "none")
    if fallback not in ("none", oc.source):
        needed = oc.camera_entity if fallback == "ha_camera" else oc.go2rtc_stream
        if not needed:
            log.warning("[%s] fallback_source %s needs %s on this object; no fallback",
                        oc.id, fallback, "camera_entity" if fallback == "ha_camera" else "go2rtc_stream")
            fallback = "none"
    return build_source({"source": oc.source, "fallback_source": fallback,
                         "go2rtc_url": opts.get("go2rtc_url", ""),
                         "go2rtc_stream": oc.go2rtc_stream, "camera_entity": oc.camera_entity})


class App:
    def __init__(self, opts: dict):
        self.opts = opts
        self.stop_event = threading.Event()
        objs, legacy = parse_objects(opts)
        if legacy:
            log.warning("using 0.2-style single-object options; move them under 'objects' "
                        "(see the docs). They will stop working in a future release.")
        migrate_legacy_files(DATA_DIR, objs[0])

        tuning = Tuning(
            cfg=dec.Config(present_min_hits=int(opts["present_min_hits"]),
                           absent_checks=int(opts["absent_checks"]),
                           unknown_after_failures=int(opts["unknown_after_failures"])),
            max_aspect=float(opts["max_aspect"]),
            confirm_delay_s=float(opts["confirm_delay_s"]),
            sanity_min_ratio=float(opts["sanity_min_ratio"]),
            sanity_min_h=float(opts["sanity_min_h"]))

        self.mqtt = MqttClient(supervisor_mqtt_config(opts), VERSION,
                               on_connected=self._on_connected,
                               on_setting=self._on_setting,
                               on_check_now=self._on_check_now,
                               on_ha_restart=self._on_ha_restart)
        self.cameras: dict[tuple, CameraWorker] = {}
        self.objects: dict[str, TrackedObject] = {}
        fallback = opts.get("fallback_source", "none")
        for oc in objs:
            key = camera_key(oc, fallback)
            if key not in self.cameras:
                name = oc.go2rtc_stream if oc.source == "go2rtc" else oc.camera_entity
                self.cameras[key] = CameraWorker(
                    name, make_source(oc, opts), threading.Condition(),
                    int(opts["burst_size"]), float(opts["burst_interval_s"]), self.stop_event)
            cam = self.cameras[key]
            obj = TrackedObject(oc, tuning, DATA_DIR, ObjectPublisher(self.mqtt, oc.id, oc.name),
                                cam.cond)
            cam.objects.append(obj)
            self.objects[oc.id] = obj
        for cam in self.cameras.values():
            for o in cam.objects:
                o.known_ids = {p.oc.tag_id for p in cam.objects if p is not o}
            log.info("camera %s: %s", cam.name,
                     ", ".join(f"{o.oc.name} (tag {o.oc.tag_id})" for o in cam.objects))

    # --- MQTT callbacks (paho thread) -------------------------------------

    def _on_connected(self, first: bool):
        marker = os.path.join(DATA_DIR, LEGACY_CLEARED_MARKER)
        if first and not os.path.exists(marker):
            log.info("clearing pre-0.3 MQTT entities and topics")
            self.mqtt.clear_retained(legacy_topics())
            open(marker, "w").close()
            threading.Timer(LEGACY_CLEAR_DELAY_S, self._publish_and_start, args=(True,)).start()
        else:
            self._publish_and_start(first)

    def _publish_and_start(self, first: bool):
        for o in self.objects.values():
            o.publish_all(also_commands=True)
        if first:
            for o in self.objects.values():
                o.request(STARTUP)

    def _on_ha_restart(self):
        for o in self.objects.values():
            o.publish_all()

    def _on_setting(self, obj_id: str, key: str, value: str):
        if o := self.objects.get(obj_id):
            o.on_setting(key, value)

    def _on_check_now(self, obj_id: str):
        if o := self.objects.get(obj_id):
            o.on_check_now()
        else:
            log.warning("check requested for unknown object %r", obj_id)

    # --- lifecycle --------------------------------------------------------

    def run(self):
        self.mqtt.start()
        for cam in self.cameras.values():
            cam.thread.start()
        self.stop_event.wait()
        for cam in self.cameras.values():
            with cam.cond:
                cam.cond.notify_all()
        for cam in self.cameras.values():
            cam.thread.join(timeout=15)
        self.mqtt.stop()

    def shutdown(self, *_):
        log.info("stopping")
        self.stop_event.set()


def main():
    opts = load_options()
    logging.basicConfig(level=str(opts.get("log_level", "info")).upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("TagSense %s starting", VERSION)
    if int(opts["present_min_hits"]) > int(opts["burst_size"]):
        log.warning("present_min_hits %s > burst_size %s: clamping",
                    opts["present_min_hits"], opts["burst_size"])
        opts["present_min_hits"] = opts["burst_size"]
    try:
        app = App(opts)
    except (ConfigError, ValueError, RuntimeError) as e:
        log.error("configuration error: %s", e)
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, app.shutdown)
    signal.signal(signal.SIGINT, app.shutdown)
    app.run()


if __name__ == "__main__":
    main()
