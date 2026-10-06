"""The access module's entry point: scanners, their store and the MQTT link."""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import zipfile
from datetime import datetime, timezone

from ..mqtt_ha import MqttConfig, supervisor_mqtt_config
from ..objects import ConfigError, slugify, unique_id
from ..sources import HaCameraSource, build_source
from .config import ScannerConfig, ScannerStore, parse_scanner, validate_scanners
from .mqtt import AccessMqtt
from .scanner import Scanner
from .store import AccessStore
from .confirm import ConfirmBook
from .verifier import Verifier

log = logging.getLogger("tagsense.access")


class AccessError(Exception):
    """The access module cannot start; the bin sensor is unaffected."""


ACTIVITY = ("ha_scan", "confirm", "test_ok")
SNAPSHOT_TIMEOUT_S = 30     # some cameras take 20 s to produce a single JPEG


def make_source(cfg: ScannerConfig, opts: dict):
    src = build_source({"source": cfg.source, "fallback_source": cfg.fallback_source or "none",
                        "go2rtc_url": opts.get("go2rtc_url", ""),
                        "go2rtc_stream": cfg.go2rtc_stream, "camera_entity": cfg.camera_entity})
    if hasattr(src, "set_timeout"):
        src.set_timeout(SNAPSHOT_TIMEOUT_S)
    else:
        src.timeout = SNAPSHOT_TIMEOUT_S
    return src


def make_snapshot_source(cfg: ScannerConfig, opts: dict):
    """Auto capture: the HA camera, used for snapshots until the stream runs."""
    if cfg.capture != "auto" or not cfg.camera_entity:
        return None
    src = HaCameraSource(cfg.camera_entity)
    src.timeout = SNAPSHOT_TIMEOUT_S
    return src


def access_mqtt_config(opts: dict) -> MqttConfig:
    """The broker of the main connection, with the access module's own login."""
    user, pw = opts.get("access_mqtt_username"), opts.get("access_mqtt_password")
    if not user or not pw:
        raise AccessError("access_enabled is on but access_mqtt_username / access_mqtt_password "
                          "are not set. Access needs its own MQTT login (see the "
                          "Documentation tab); it stays off until they are set")
    try:
        base = supervisor_mqtt_config(opts)
    except RuntimeError as e:
        raise AccessError(str(e)) from None
    if base.username == user:
        raise AccessError("access_mqtt_username must not be the login TagSense uses for the bin "
                          "sensor: create a separate MQTT user for access")
    return MqttConfig(base.host, base.port, user, pw)


class AccessManager:
    def __init__(self, opts: dict, data_dir: str, version: str, mqtt=None,
                 source_factory=make_source, snapshot_factory=make_snapshot_source,
                 capture_factory=None):
        self.opts = opts
        self.dir = os.path.join(data_dir, "access")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self.source_factory = source_factory
        self.snapshot_factory = snapshot_factory
        self.capture_factory = capture_factory        # tests: a fake video capture
        self.codes = AccessStore(self.dir)             # keys, people, static codes, lockouts
        self.verifier = Verifier(self.codes)
        self.confirm = ConfirmBook()
        self.store = ScannerStore(self.dir)
        self.configs = self.store.load()
        self.lock = threading.RLock()
        self.mqtt = mqtt or AccessMqtt(access_mqtt_config(opts), version,
                                       on_scan=self.scan, on_connected=self._on_connected)
        self.stop_event = threading.Event()
        self.scanners: dict[str, Scanner] = {}
        # When Home Assistant last started a scan, confirmed an event, and a panel test
        # last verified a code: the panel's setup checklist.
        self.activity_path = os.path.join(self.dir, "activity.json")
        self.activity: dict[str, str] = {}
        try:
            with open(self.activity_path) as f:
                self.activity = {k: v for k, v in json.load(f).items() if k in ACTIVITY}
        except (OSError, ValueError, AttributeError):
            pass
        self._build()

    def _build(self):
        self.stop_event = threading.Event()
        extra = {"capture_factory": self.capture_factory} if self.capture_factory else {}
        self.scanners = {c.id: Scanner(c, self.source_factory(c, self.opts), self.mqtt, self.dir,
                                       self.stop_event, verifier=self.verifier,
                                       snapshot_source=self.snapshot_factory(c, self.opts),
                                       confirm=self.confirm, on_test_ok=lambda: self.note("test_ok"),
                                       **extra)
                         for c in self.configs}

    def start(self):
        self.mqtt.start()
        for s in self.scanners.values():
            s.thread.start()
        log.info("access: enabled, scanners: %s", ", ".join(self.scanners) or "(none yet)")

    def _stop_scanners(self):
        self.stop_event.set()
        for s in self.scanners.values():
            with s.cond:
                s.cond.notify_all()
        for s in self.scanners.values():
            if s.thread.is_alive():
                s.thread.join(timeout=30)

    def stop(self):
        with self.lock:
            self._stop_scanners()
        self.mqtt.stop()

    def _on_connected(self):
        with self.lock:
            for s in self.scanners.values():
                self.mqtt.publish_discovery(s.id, s.cfg.name)

    # --- commands ---------------------------------------------------------------

    def scan(self, sid: str, trigger: str = "button", test: bool = False):
        s = self.scanners.get(sid)
        if not s:
            log.warning("access: scan requested for unknown scanner %r", sid)
            return False
        s.scan(trigger, test)
        if trigger == "button":         # the MQTT Scan button: Home Assistant asked
            self.note("ha_scan")
        return True

    def note(self, kind: str):
        """Record when something last happened (see ACTIVITY). Never raises."""
        self.activity[kind] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            tmp = self.activity_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.activity, f)
            os.replace(tmp, self.activity_path)
        except OSError as e:
            log.warning("access: could not save activity: %s", e)

    # --- config (panel) ------------------------------------------------------------

    def apply(self, configs: list[ScannerConfig], removed: list[ScannerConfig] = ()):
        validate_scanners(configs)
        for c in configs:
            try:
                self.source_factory(c, self.opts)
            except ValueError as e:
                raise ConfigError(f"{c.name}: {e}") from None
        with self.lock:
            self.store.save(configs)
            self._stop_scanners()
            for c in removed:
                self.mqtt.remove(c.id, c.name)
            self.configs = list(configs)
            self._build()
            for s in self.scanners.values():
                if self.mqtt.connected:
                    self.mqtt.publish_discovery(s.id, s.cfg.name)
                s.thread.start()
        log.info("access: scanners reloaded: %s", ", ".join(c.id for c in configs) or "(none)")

    def create(self, raw: dict) -> str:
        taken = {c.id for c in self.configs}
        raw = dict(raw)
        raw["id"] = (str(raw.get("id") or "").strip().lower()
                     or unique_id(slugify(str(raw.get("name") or "")), taken))
        cfg = parse_scanner(raw)
        if cfg.id in taken:
            raise ConfigError(f"id {cfg.id!r} is already used")
        self.apply(self.configs + [cfg])
        return cfg.id

    def update(self, sid: str, raw: dict) -> str:
        cur = next((c for c in self.configs if c.id == sid), None)
        if not cur:
            raise KeyError(sid)
        cfg = parse_scanner(cur.to_dict() | dict(raw) | {"id": sid})
        if cfg != cur:
            self.apply([cfg if c.id == sid else c for c in self.configs])
        return sid

    def delete(self, sid: str):
        cur = next((c for c in self.configs if c.id == sid), None)
        if not cur:
            raise KeyError(sid)
        self.apply([c for c in self.configs if c.id != sid], removed=[cur])

    def status(self) -> dict:
        return {"enabled": True, "mqtt_connected": bool(self.mqtt.connected),
                "activity": dict(self.activity),
                "scanners": [s.status() for s in self.scanners.values()]}

    def debug_zip(self, sid: str) -> bytes:
        s = self.scanners[sid]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for name in s.debug_files():
                z.write(os.path.join(s.debug_dir, name), f"{sid}/{name}")
        return buf.getvalue()
