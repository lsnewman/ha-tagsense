"""MQTT client plus Home Assistant discovery: one HA device per tracked object."""
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
HA_STATUS = f"{DISCOVERY_PREFIX}/status"
ONLINE, OFFLINE = "online", "offline"
SUPPORT_URL = "https://github.com/lsnewman/ha-tagsense"

# Enum sensor values. If HA rejects "unknown" as an option, change it here only.
ENUM_PRESENT, ENUM_ABSENT, ENUM_UNKNOWN = "present", "absent", "unknown"

SETTING_KEYS = ("poll_interval", "crop_x1", "crop_y1", "crop_x2", "crop_y2", "enabled",
                "rotation_steps")

# (key, name, extra discovery fields); "{attrs}" is replaced by an attributes topic
DIAGNOSTICS = [
    ("last_source", "Last source", {"icon": "mdi:cctv"}),
    ("resolution", "Frame resolution", {"icon": "mdi:image-size-select-large"}),
    ("last_error", "Last error", {"icon": "mdi:alert-circle-outline"}),
    ("warning", "Warning", {"icon": "mdi:alert-outline", "_attrs": "reference"}),
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
    ("tag_aspect", "Tag aspect", {"state_class": "measurement", "suggested_display_precision": 2,
                                  "icon": "mdi:aspect-ratio"}),
    ("sanity_ratio", "Sanity ratio", {"state_class": "measurement", "suggested_display_precision": 2}),
    ("phantom_decodes", "Discarded decodes", {"state_class": "measurement", "icon": "mdi:ghost-outline",
                                            "_attrs": "phantoms"}),
    ("miss_streak", "Miss streak", {"state_class": "measurement", "icon": "mdi:counter"}),
    ("crop_mean", "Crop brightness", {"state_class": "measurement", "suggested_display_precision": 1,
                                      "_attrs": "crop_stats"}),
    ("crop_std", "Crop contrast", {"state_class": "measurement", "suggested_display_precision": 1}),
]
# Removed entities: their discovery config is cleared so HA deletes them.
RETIRED_DIAGNOSTICS = ("last_check",)
COMMANDS = ("check_now", "reset_reference", "set_orientation")
DIAG_ATTRS = ("reference", "phantoms", "crop_stats")


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


def fmt(v) -> str:
    """MQTT payload for a sensor value; 'None' makes HA show unknown."""
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "ON" if v else "OFF"
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1e4 else f"{v:.0f}"
    return str(v)[:255]


class Topics:
    """MQTT topics for one object: tagsense/<id>/..."""

    def __init__(self, obj_id: str):
        b = f"{BASE}/{obj_id}"
        self.base = b
        self.presence = f"{b}/presence"
        self.presence_attrs = f"{b}/presence/attributes"
        self.presence_available = f"{b}/presence/available"
        self.status = f"{b}/status"
        self.status_attrs = f"{b}/status/attributes"
        self.image = f"{b}/image"
        self.problem = f"{b}/problem"
        self.problem_attrs = f"{b}/problem/attributes"
        self.rotation = f"{b}/rotation"
        self.rotation_attrs = f"{b}/rotation/attributes"
        self.rotation_available = f"{b}/rotation/available"

    def cmd(self, name: str) -> str:
        return f"{self.base}/cmd/{name}"

    def set(self, key: str) -> str:
        return f"{self.base}/set/{key}"

    def setting(self, key: str) -> str:
        return f"{self.base}/setting/{key}"

    def diag(self, key: str) -> str:
        return f"{self.base}/diag/{key}"

    def attrs(self, name: str) -> str:
        return f"{self.base}/diag/{name}/attributes"


def discovery_configs(version: str, obj_id: str, name: str) -> list[tuple[str, dict]]:
    """[(discovery topic, payload)] for every entity of one object."""
    t = Topics(obj_id)
    device = {"identifiers": [f"tagsense_{obj_id}"], "name": f"TagSense {name}",
              "manufacturer": "ha-tagsense", "model": "AprilTag presence sensor",
              "sw_version": version}
    origin = {"name": "TagSense", "sw_version": version, "support_url": SUPPORT_URL}
    app_avail = [{"topic": AVAILABILITY}]
    # Rotation entities only make sense while the object is there.
    rot_avail = {"availability": app_avail + [{"topic": t.rotation_available}],
                 "availability_mode": "all"}

    def cfg(component: str, key: str, payload: dict) -> tuple[str, dict]:
        p = {"unique_id": f"tagsense_{obj_id}_{key}", "device": device, "origin": origin,
             "availability": app_avail, "has_entity_name": True}
        p.update(payload)
        return f"{DISCOVERY_PREFIX}/{component}/tagsense_{obj_id}/{key}/config", p

    out = [
        cfg("binary_sensor", "presence", {
            "name": None,      # main feature: takes the device name, e.g. "TagSense Bin"
            "device_class": "occupancy", "state_topic": t.presence,
            "json_attributes_topic": t.presence_attrs,
            "availability": app_avail + [{"topic": t.presence_available}],
            "availability_mode": "all"}),
        cfg("sensor", "status", {
            "name": "Status", "device_class": "enum", "entity_category": "diagnostic",
            "options": [ENUM_PRESENT, ENUM_ABSENT, ENUM_UNKNOWN],
            "state_topic": t.status, "json_attributes_topic": t.status_attrs}),
        cfg("binary_sensor", "problem", {
            "name": "Tag rejected", "device_class": "problem", "entity_category": "diagnostic",
            "state_topic": t.problem, "json_attributes_topic": t.problem_attrs}),
        cfg("button", "check_now", {"name": "Check now", "command_topic": t.cmd("check_now"),
                                    "icon": "mdi:magnify-scan"}),
        cfg("button", "reset_reference", {
            "name": "Reset learned position", "entity_category": "config",
            "command_topic": t.cmd("reset_reference"), "icon": "mdi:map-marker-off"}),
        cfg("sensor", "rotation", {
            "name": "Rotation", "unit_of_measurement": "°", "state_class": "measurement",
            "icon": "mdi:rotate-right", "state_topic": t.rotation,
            "json_attributes_topic": t.rotation_attrs, **rot_avail}),
        cfg("number", "rotation_steps", {
            "name": "Rotation steps", "entity_category": "config", "mode": "box",
            "min": 1, "max": 36, "step": 1, "icon": "mdi:angle-acute",
            "command_topic": t.set("rotation_steps"), "state_topic": t.setting("rotation_steps"),
            "retain": True, **rot_avail}),
        cfg("button", "set_orientation", {
            "name": "Set current orientation as 0°", "entity_category": "config",
            "command_topic": t.cmd("set_orientation"), "icon": "mdi:rotate-left", **rot_avail}),
        cfg("switch", "enabled", {
            "name": "Enabled", "entity_category": "config",
            "command_topic": t.set("enabled"), "state_topic": t.setting("enabled"),
            "retain": True}),
        cfg("number", "poll_interval", {
            "name": "Poll interval", "entity_category": "config", "mode": "box",
            "min": 0, "max": 86400, "step": 1, "unit_of_measurement": "s",
            "icon": "mdi:timer-outline",
            "command_topic": t.set("poll_interval"), "state_topic": t.setting("poll_interval"),
            "retain": True}),
        cfg("image", "last_crop", {"name": "Last crop", "image_topic": t.image,
                                   "content_type": "image/jpeg"}),
    ]
    for key in ("crop_x1", "crop_y1", "crop_x2", "crop_y2"):
        out.append(cfg("number", key, {
            "name": key.replace("_", " ").capitalize(), "entity_category": "config",
            "mode": "box", "min": 0, "max": 1, "step": 0.01, "icon": "mdi:crop",
            "command_topic": t.set(key), "state_topic": t.setting(key), "retain": True}))
    for key, label, extra in DIAGNOSTICS:
        extra = dict(extra)
        if attrs := extra.pop("_attrs", None):
            extra["json_attributes_topic"] = t.attrs(attrs)
        out.append(cfg("sensor", key, {"name": label, "entity_category": "diagnostic",
                                       "state_topic": t.diag(key), **extra}))
    return out


class ObjectPublisher:
    """Publishes one object's discovery, state, settings and diagnostics."""

    def __init__(self, client: "MqttClient", obj_id: str, name: str):
        self.client, self.id, self.name = client, obj_id, name
        self.t = Topics(obj_id)

    def pub(self, topic: str, payload, retain: bool = True):
        self.client.pub(topic, payload, retain)

    def publish_discovery(self):
        for topic, payload in discovery_configs(self.client.version, self.id, self.name):
            self.pub(topic, json.dumps(payload))
        for topic in self._retired_topics():
            self.pub(topic, b"")

    def _retired_topics(self) -> list[str]:
        return [t for k in RETIRED_DIAGNOSTICS
                for t in (f"{DISCOVERY_PREFIX}/sensor/tagsense_{self.id}/{k}/config", self.t.diag(k))]

    def publish_setting(self, key: str, value):
        self.pub(self.t.setting(key), fmt(value))

    def publish_settings(self, values: dict, also_commands: bool = False):
        for key, value in values.items():
            self.pub(self.t.setting(key), fmt(value))
            if also_commands:
                self.pub(self.t.set(key), fmt(value))

    def publish_state(self, value: str, reason: str, presence_attrs: dict, status_attrs: dict):
        # Attributes before state, so a state change never carries stale attributes.
        available = value in (ENUM_PRESENT, ENUM_ABSENT)
        if available:
            self.pub(self.t.presence_attrs, json.dumps(presence_attrs))
            self.pub(self.t.presence, "ON" if value == ENUM_PRESENT else "OFF")
        self.pub(self.t.presence_available, ONLINE if available else OFFLINE)
        self.pub(self.t.status_attrs, json.dumps({"reason": reason, **status_attrs}))
        self.pub(self.t.status, value)

    def publish_diagnostics(self, values: dict, attrs: dict[str, dict] | None = None):
        for name, payload in (attrs or {}).items():
            self.pub(self.t.attrs(name), json.dumps(payload))
        for key, value in values.items():
            self.pub(self.t.diag(key), fmt(value))

    def publish_problem(self, alert: dict | None):
        self.pub(self.t.problem_attrs, json.dumps(alert or {}))
        self.pub(self.t.problem, "ON" if alert else "OFF")

    def publish_rotation(self, available: bool, value: float | None, angle: float | None,
                         steps: int):
        if available:
            self.pub(self.t.rotation_attrs, json.dumps({"angle": angle, "steps": steps}))
            self.pub(self.t.rotation, fmt(value))
        self.pub(self.t.rotation_available, ONLINE if available else OFFLINE)

    def publish_image(self, jpeg: bytes):
        self.pub(self.t.image, jpeg, retain=False)

    def remove(self):
        """Delete this object's entities from HA and clear its retained topics."""
        t = self.t
        topics = [topic for topic, _ in discovery_configs(self.client.version, self.id, self.name)]
        topics += [t.presence, t.presence_attrs, t.presence_available, t.status, t.status_attrs,
                   t.problem, t.problem_attrs, t.rotation, t.rotation_attrs, t.rotation_available]
        topics += [t.cmd(c) for c in COMMANDS] + self._retired_topics()
        topics += [t.setting(k) for k in SETTING_KEYS] + [t.set(k) for k in SETTING_KEYS]
        topics += [t.diag(k) for k, _, _ in DIAGNOSTICS]
        topics += [t.attrs(n) for n in DIAG_ATTRS]
        for topic in topics:
            self.pub(topic, b"")


class MqttClient:
    """The shared connection. Routes commands to objects by id."""

    def __init__(self, cfg: MqttConfig, version: str,
                 on_connected: Callable[[bool], None],
                 on_setting: Callable[[str, str, str], None],
                 on_command: Callable[[str, str], None],
                 on_ha_restart: Callable[[], None]):
        self.version = version
        self._on_connected = on_connected
        self._on_setting = on_setting
        self._on_command = on_command
        self._on_ha_restart = on_ha_restart
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
        self._connected_once = False

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

    # --- callbacks (paho thread: keep short) ------------------------------

    def _handle_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("MQTT connected")
        first = not self._connected_once
        self._connected_once = True
        # Discovery and settings are published by the app before subscribing, so
        # retained command echoes match /data (the source of truth).
        self._on_connected(first)
        client.subscribe([(f"{BASE}/+/set/+", 1), (f"{BASE}/+/cmd/+", 1),
                          (HA_STATUS, 1)])
        self.pub(AVAILABILITY, ONLINE)

    def _handle_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", "replace").strip()
        if msg.topic == HA_STATUS:
            if payload == ONLINE:
                log.info("HA restarted: republishing discovery")
                self._on_ha_restart()
            return
        parts = msg.topic.split("/")
        if len(parts) == 4 and parts[0] == BASE and parts[2] == "cmd" and parts[3] in COMMANDS:
            self._on_command(parts[1], parts[3])
        elif len(parts) == 4 and parts[0] == BASE and parts[2] == "set" and parts[3] in SETTING_KEYS:
            self._on_setting(parts[1], parts[3], payload)
