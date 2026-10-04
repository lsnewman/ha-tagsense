"""TagSense app entry point: options, objects, camera workers, MQTT and web UI."""
from __future__ import annotations

import json
import logging
import os
import signal
import threading

from . import decision as dec
from .cameras import CameraWorker
from .ha_notify import Notifier
from .mqtt_ha import MqttClient, ObjectPublisher, supervisor_mqtt_config
from .objects import (ConfigError, ObjectConfig, ObjectStore, remove_object_dir,
                      validate_list)
from .scheduler import STARTUP
from .sources import build_source
from .tracker import TrackedObject, Tuning

log = logging.getLogger("tagsense")

DATA_DIR = os.environ.get("TAGSENSE_DATA", "/data")
VERSION = os.environ.get("TAGSENSE_VERSION", "dev")
WEB_PORT = int(os.environ.get("TAGSENSE_WEB_PORT", "8099"))

DEFAULT_OPTIONS = {
    "go2rtc_url": "",
    "max_aspect": 2.0,
    "min_size_ratio": 0.5,
    "burst_size": 5,
    "burst_interval_s": 1.0,
    "present_min_hits": 2,
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


def make_source(oc: ObjectConfig, opts: dict):
    return build_source({"source": oc.source, "fallback_source": oc.fallback_source or "none",
                         "go2rtc_url": opts.get("go2rtc_url", ""),
                         "go2rtc_stream": oc.go2rtc_stream, "camera_entity": oc.camera_entity})


class App:
    def __init__(self, opts: dict, mqtt=None, source_factory=make_source, data_dir: str = DATA_DIR,
                 notifier: Notifier | None = None):
        self.opts = opts
        self.notifier = notifier or Notifier()
        self.data_dir = data_dir
        self.source_factory = source_factory
        self.stop_event = threading.Event()
        self.reload_lock = threading.RLock()
        self.store = ObjectStore(data_dir)
        self.configs = self.store.load_or_empty()
        self.tuning = Tuning(
            cfg=dec.Config(present_min_hits=int(opts["present_min_hits"]),
                           absent_checks=int(opts["absent_checks"]),
                           unknown_after_failures=int(opts["unknown_after_failures"])),
            max_aspect=float(opts["max_aspect"]),
            confirm_delay_s=float(opts["confirm_delay_s"]),
            sanity_min_ratio=float(opts["sanity_min_ratio"]),
            sanity_min_h=float(opts["sanity_min_h"]),
            min_size_ratio=float(opts["min_size_ratio"]))
        self.mqtt = mqtt or MqttClient(supervisor_mqtt_config(opts), VERSION,
                                       on_connected=self._on_connected,
                                       on_setting=self._on_setting,
                                       on_command=self._on_command,
                                       on_ha_restart=self._on_ha_restart)
        self.connected = False
        self.cameras: dict[tuple, CameraWorker] = {}
        self.objects: dict[str, TrackedObject] = {}
        self.gen_stop = threading.Event()
        self._build(self.configs)

    # --- building and live reload -----------------------------------------

    def _build(self, configs: list[ObjectConfig]):
        """Create camera workers and tracked objects (workers not started)."""
        self.gen_stop = threading.Event()
        cameras: dict[tuple, CameraWorker] = {}
        objects: dict[str, TrackedObject] = {}
        for oc in configs:
            key = oc.camera_key
            if key not in cameras:
                name = oc.go2rtc_stream if oc.source == "go2rtc" else oc.camera_entity
                if oc.fallback:
                    name += "+fallback"
                cameras[key] = CameraWorker(
                    name, self.source_factory(oc, self.opts), threading.Condition(),
                    int(self.opts["burst_size"]), float(self.opts["burst_interval_s"]),
                    self.gen_stop)
            cam = cameras[key]
            obj = TrackedObject(oc, self.tuning, self.data_dir,
                                ObjectPublisher(self.mqtt, oc.id, oc.name), cam.cond, self.notifier)
            obj.camera = cam
            cam.objects.append(obj)
            objects[oc.id] = obj
        for cam in cameras.values():
            for o in cam.objects:
                o.known_ids = {p.oc.tag_id for p in cam.objects if p is not o}
            log.info("camera %s: %s", cam.name,
                     ", ".join(f"{o.oc.name} (tag {o.oc.tag_id})" for o in cam.objects))
        if not configs:
            log.info("no objects configured yet: add one in the TagSense panel")
        self.cameras, self.objects = cameras, objects

    def _start_workers(self):
        for cam in self.cameras.values():
            cam.thread.start()

    def _stop_workers(self):
        self.gen_stop.set()
        for cam in self.cameras.values():
            with cam.cond:
                cam.cond.notify_all()
        for cam in self.cameras.values():
            if cam.thread.is_alive():
                cam.thread.join(timeout=30)

    def apply_configs(self, configs: list[ObjectConfig], removed: list[ObjectConfig] = ()):
        """Validate, save and switch to a new object list without restarting."""
        validate_list(configs)
        for oc in configs:      # fail before stopping anything (e.g. go2rtc_url unset)
            try:
                self.source_factory(oc, self.opts)
            except ValueError as e:
                raise ConfigError(f"{oc.name}: {e}") from None
        with self.reload_lock:
            old_objects = self.objects
            self.store.save(configs)
            self._stop_workers()
            for oc in removed:
                if o := old_objects.get(oc.id):
                    o.pub.remove()
                    o.dismiss_alert()
                remove_object_dir(self.data_dir, oc.id)
            # Renamed objects keep their id; republish discovery with the new name.
            self.configs = list(configs)
            self._build(self.configs)
            if self.connected:
                for o in self.objects.values():
                    o.publish_all(also_commands=True)
                    o.request(STARTUP)
            self._start_workers()
        log.info("objects reloaded: %s", ", ".join(o.id for o in configs) or "(none)")

    # --- MQTT callbacks (paho thread) -------------------------------------

    def _on_connected(self, first: bool):
        with self.reload_lock:
            self.connected = True
            for o in self.objects.values():
                o.publish_all(also_commands=True)
            if first:
                for o in self.objects.values():
                    o.request(STARTUP)

    def _on_ha_restart(self):
        with self.reload_lock:
            for o in self.objects.values():
                o.publish_all()

    def _on_setting(self, obj_id: str, key: str, value: str):
        if o := self.objects.get(obj_id):
            o.on_setting(key, value)

    def _on_command(self, obj_id: str, cmd: str):
        o = self.objects.get(obj_id)
        if not o:
            log.warning("%s requested for unknown object %r", cmd, obj_id)
        elif cmd == "check_now":
            o.on_check_now()
        elif cmd == "reset_reference":
            o.reset_reference()

    # --- lifecycle --------------------------------------------------------

    def run(self, web: bool = True):
        if web:
            from .web import start_web
            start_web(self, WEB_PORT)
        self.mqtt.start()
        self._start_workers()
        self.stop_event.wait()
        with self.reload_lock:
            self._stop_workers()
        self.mqtt.stop()

    def shutdown(self, *_):
        log.info("stopping")
        self.gen_stop.set()
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
