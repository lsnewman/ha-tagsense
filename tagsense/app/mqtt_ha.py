"""MQTT client plus Home Assistant discovery for the TagSense device."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Callable

import paho.mqtt.client as mqtt
import requests

log = logging.getLogger(__name__)

BASE = "tagsense"
DISCOVERY_PREFIX = "homeassistant"
AVAILABILITY = f"{BASE}/availability"
BIN_AVAILABLE = f"{BASE}/bin/available"
BIN_STATE = f"{BASE}/bin/state"
BIN_ATTRS = f"{BASE}/bin/attributes"
STATUS_STATE = f"{BASE}/status/state"
STATUS_ATTRS = f"{BASE}/status/attributes"
CROP_STATS_ATTRS = f"{BASE}/diag/crop_stats"
PHANTOM_ATTRS = f"{BASE}/diag/phantoms"
REFERENCE_ATTRS = f"{BASE}/diag/reference"
IMAGE = f"{BASE}/image"
CHECK_NOW = f"{BASE}/cmd/check_now"
HA_STATUS = f"{DISCOVERY_PREFIX}/status"
ONLINE, OFFLINE = "online", "offline"

# Enum sensor values. If HA rejects "unknown" as an option, change it here only.
ENUM_PRESENT, ENUM_ABSENT, ENUM_UNKNOWN = "present", "absent", "unknown"

SETTING_KEYS = ("poll_interval", "crop_x1", "crop_y1", "crop_x2", "crop_y2", "enabled")


def set_topic(key: str) -> str:
    return f"{BASE}/set/{key}"


def setting_state_topic(key: str) -> str:
    return f"{BASE}/state/{key}"


def diag_topic(key: str) -> str:
    return f"{BASE}/diag/{key}"


# (key, name, extra discovery fields)
DIAGNOSTICS = [
    ("last_source", "Last source", {"icon": "mdi:cctv"}),
    ("resolution", "Frame resolution", {"icon": "mdi:image-size-select-large"}),
    ("last_check", "Last check", {"device_class": "timestamp"}),
    ("last_error", "Last error", {"icon": "mdi:alert-circle-outline"}),
    ("warning", "Warning", {"icon": "mdi:alert-outline", "json_attributes_topic": REFERENCE_ATTRS}),
    ("fetch_ms", "Fetch time", {"unit_of_measurement": "ms", "state_class": "measurement",
                                "device_class": "duration", "suggested_display_precision": 0}),
    ("detect_ms", "Detect time", {"unit_of_measurement": "ms", "state_class": "measurement",
                                  "device_class": "duration", "suggested_display_precision": 1}),
    ("fetch_failures", "Fetch failures", {"state_class": "total_increasing", "icon": "mdi:download-off"}),
    ("discard_rate", "Discard rate", {"unit_of_measurement": "%", "state_class": "measurement",
                                      "suggested_display_precision": 0}),
    ("unique_frames", "Unique frames", {"state_class": "measurement", "icon": "mdi:content-duplicate"}),
    ("tag_size_px", "Tag size", {"unit_of_measurement": "px", "state_class": "measurement",
                                 "suggested_display_precision": 1}),
    ("sanity_ratio", "Sanity ratio", {"state_class": "measurement", "suggested_display_precision": 2}),
    ("phantom_decodes", "Phantom decodes", {"state_class": "measurement", "icon": "mdi:ghost-outline",
                                            "json_attributes_topic": PHANTOM_ATTRS}),
    ("miss_streak", "Miss streak", {"state_class": "measurement", "icon": "mdi:counter"}),
    ("crop_mean", "Crop brightness", {"state_class": "measurement", "suggested_display_precision": 1,
                                      "json_attributes_topic": CROP_STATS_ATTRS}),
    ("crop_std", "Crop contrast", {"state_class": "measurement", "suggested_display_precision": 1}),
]


@dataclass
class MqttConfig:
    host: str
    port: int = 1883
    username: str | None = None
    password: str | None = None


def supervisor_mqtt_config(opts: dict) -> MqttConfig:
    """Option overrides first, then the Supervisor MQTT service."""
    if opts.get("mqtt_host"):
        return MqttConfig(opts["mqtt_host"], int(opts.get("mqtt_port") or 1883),
                          opts.get("mqtt_username") or None, opts.get("mqtt_password") or None)
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("no mqtt_host option and no SUPERVISOR_TOKEN")
    try:
        r = requests.get("http://supervisor/services/mqtt",
                         headers={"Authorization": f"Bearer {token}"}, timeout=10)
        r.raise_for_status()
        d = r.json()["data"]
    except (requests.RequestException, ValueError, KeyError) as e:
        raise RuntimeError(f"MQTT service not available from Supervisor ({e}); "
                           "is the Mosquitto app installed?") from e
    return MqttConfig(d["host"], int(d.get("port", 1883)), d.get("username"), d.get("password"))


def discovery_configs(version: str, object_name: str = "Object") -> list[tuple[str, dict]]:
    """[(discovery topic, payload)] for every entity."""
    device = {"identifiers": ["tagsense"], "name": "TagSense", "manufacturer": "ha-tagsense",
              "model": "AprilTag presence sensor", "sw_version": version}
    origin = {"name": "TagSense", "sw_version": version,
              "support_url": "https://github.com/lsnewman/ha-tagsense"}
    app_avail = [{"topic": AVAILABILITY}]

    def cfg(component: str, obj: str, payload: dict) -> tuple[str, dict]:
        p = {"unique_id": f"tagsense_{obj}", "device": device, "origin": origin,
             "availability": app_avail, "has_entity_name": True}
        p.update(payload)
        return f"{DISCOVERY_PREFIX}/{component}/tagsense/{obj}/config", p

    out = [
        cfg("binary_sensor", "bin", {
            # unique_id/topics keep "bin" for compatibility with existing installs.
            "name": object_name, "device_class": "occupancy", "state_topic": BIN_STATE,
            "json_attributes_topic": BIN_ATTRS,
            "availability": app_avail + [{"topic": BIN_AVAILABLE}], "availability_mode": "all"}),
        cfg("sensor", "status", {
            "name": "Status", "device_class": "enum", "entity_category": "diagnostic",
            "options": [ENUM_PRESENT, ENUM_ABSENT, ENUM_UNKNOWN],
            "state_topic": STATUS_STATE, "json_attributes_topic": STATUS_ATTRS}),
        cfg("button", "check_now", {"name": "Check now", "command_topic": CHECK_NOW,
                                    "icon": "mdi:magnify-scan"}),
        cfg("switch", "enabled", {
            "name": "Enabled", "entity_category": "config",
            "command_topic": set_topic("enabled"), "state_topic": setting_state_topic("enabled"),
            "retain": True}),
        cfg("number", "poll_interval", {
            "name": "Poll interval", "entity_category": "config", "mode": "box",
            "min": 0, "max": 86400, "step": 1, "unit_of_measurement": "s",
            "icon": "mdi:timer-outline",
            "command_topic": set_topic("poll_interval"),
            "state_topic": setting_state_topic("poll_interval"), "retain": True}),
        cfg("image", "last_crop", {"name": "Last crop", "image_topic": IMAGE,
                                   "content_type": "image/jpeg"}),
    ]
    for key in ("crop_x1", "crop_y1", "crop_x2", "crop_y2"):
        out.append(cfg("number", key, {
            "name": key.replace("_", " ").capitalize(), "entity_category": "config",
            "mode": "box", "min": 0, "max": 1, "step": 0.01, "icon": "mdi:crop",
            "command_topic": set_topic(key), "state_topic": setting_state_topic(key),
            "retain": True}))
    for key, name, extra in DIAGNOSTICS:
        out.append(cfg("sensor", key, {"name": name, "entity_category": "diagnostic",
                                       "state_topic": diag_topic(key), **extra}))
    return out


def fmt(v) -> str:
    """MQTT payload for a sensor value; 'None' makes HA show unknown."""
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "ON" if v else "OFF"
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1e4 else f"{v:.0f}"
    return str(v)[:255]


class HaMqtt:
    def __init__(self, cfg: MqttConfig, version: str, object_name: str,
                 settings_values: Callable[[], dict],
                 on_setting: Callable[[str, str], None],
                 on_check_now: Callable[[], None],
                 on_connected: Callable[[], None]):
        self.version = version
        self.object_name = object_name
        self._settings_values = settings_values
        self._on_setting = on_setting
        self._on_check_now = on_check_now
        self._on_connected = on_connected
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="tagsense")
        if cfg.username:
            c.username_pw_set(cfg.username, cfg.password)
        c.will_set(AVAILABILITY, OFFLINE, qos=1, retain=True)
        c.reconnect_delay_set(1, 60)
        c.on_connect = self._handle_connect
        c.on_message = self._handle_message
        c.on_disconnect = lambda *a: log.warning("MQTT disconnected")
        self.client = c
        self.cfg = cfg

    def start(self):
        log.info("connecting to MQTT %s:%s", self.cfg.host, self.cfg.port)
        self.client.connect_async(self.cfg.host, self.cfg.port, keepalive=60)
        self.client.loop_start()

    def stop(self):
        self.client.publish(AVAILABILITY, OFFLINE, qos=1, retain=True).wait_for_publish(5)
        self.client.disconnect()
        self.client.loop_stop()

    def pub(self, topic: str, payload, retain: bool = True):
        self.client.publish(topic, payload, qos=1 if retain else 0, retain=retain)

    # --- callbacks (run on the paho thread: keep them short) -------------

    def _handle_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("MQTT connected")
        self.publish_discovery()
        # /data is the source of truth: overwrite any stale retained commands
        # before subscribing, so the retained echo matches what we hold.
        self.publish_settings(also_commands=True)
        client.subscribe([(f"{BASE}/set/+", 1), (CHECK_NOW, 1), (HA_STATUS, 1)])
        self.pub(AVAILABILITY, ONLINE)
        self._on_connected()

    def _handle_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", "replace").strip()
        if msg.topic == CHECK_NOW:
            self._on_check_now()
        elif msg.topic == HA_STATUS:
            if payload == ONLINE:
                log.info("HA restarted: republishing discovery")
                self.publish_discovery()
                self.publish_settings()
                self._on_connected()
        elif msg.topic.startswith(f"{BASE}/set/"):
            key = msg.topic.rsplit("/", 1)[1]
            if key in SETTING_KEYS:
                self._on_setting(key, payload)

    # --- publishing -------------------------------------------------------

    def publish_discovery(self):
        for topic, payload in discovery_configs(self.version, self.object_name):
            self.pub(topic, json.dumps(payload))

    def publish_setting(self, key: str, value):
        self.pub(setting_state_topic(key), fmt(value))

    def publish_settings(self, also_commands: bool = False):
        for key, value in self._settings_values().items():
            self.pub(setting_state_topic(key), fmt(value))
            if also_commands:
                self.pub(set_topic(key), fmt(value))

    def publish_state(self, value: str, reason: str, bin_attrs: dict, status_attrs: dict):
        # Attributes before state, so a state change never carries stale attributes.
        available = value in (ENUM_PRESENT, ENUM_ABSENT)
        if available:
            self.pub(BIN_ATTRS, json.dumps(bin_attrs))
            self.pub(BIN_STATE, "ON" if value == ENUM_PRESENT else "OFF")
        self.pub(BIN_AVAILABLE, ONLINE if available else OFFLINE)
        self.pub(STATUS_ATTRS, json.dumps({"reason": reason, **status_attrs}))
        self.pub(STATUS_STATE, value)

    def publish_diagnostics(self, values: dict, crop_stats: dict | None = None,
                            phantoms: dict | None = None, reference: dict | None = None):
        for key, value in values.items():
            self.pub(diag_topic(key), fmt(value))
        if crop_stats is not None:
            self.pub(CROP_STATS_ATTRS, json.dumps(crop_stats))
        if phantoms is not None:
            self.pub(PHANTOM_ATTRS, json.dumps(phantoms))
        if reference is not None:
            self.pub(REFERENCE_ATTRS, json.dumps(reference))

    def publish_image(self, jpeg: bytes):
        self.pub(IMAGE, jpeg, retain=False)
