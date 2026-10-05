"""The access module's own MQTT connection and HA discovery.

A separate connection with its own login (`access_mqtt_username`), so a broker
ACL can make tagsense/access/# and the access discovery topics writable by
this login only. The Supervisor's MQTT login is shared by every app, so it
must not be able to publish here: anything that can write these topics can
fake an access event.

Discovery uses the node id `tagsense_access`
(homeassistant/<component>/tagsense_access/<object>/config) so one ACL line
covers it. Events and images are never retained, so an HA or broker restart
cannot replay them.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

import paho.mqtt.client as mqtt

from ..mqtt_ha import DISCOVERY_PREFIX, HA_STATUS, OFFLINE, ONLINE, SUPPORT_URL, MqttConfig

log = logging.getLogger("tagsense.access")

BASE = "tagsense/access"
NODE = "tagsense_access"
AVAILABILITY = f"{BASE}/availability"
COMMANDS = ("scan",)
# verified is the only one an automation should act on; the rest report
# attempts (invalid, replayed, ...) so they can be notified on.
EVENT_TYPES = ("verified", "invalid", "not_yet_valid", "expired", "replayed", "revoked",
               "locked_out", "unrecognised", "unavailable", "scan_timeout")


class Topics:
    def __init__(self, sid: str):
        b = f"{BASE}/{sid}"
        self.base = b
        self.event = f"{b}/event"
        self.image = f"{b}/image"
        self.scanning = f"{b}/scanning"

    def cmd(self, name: str) -> str:
        return f"{self.base}/cmd/{name}"


def discovery_configs(version: str, sid: str, name: str) -> list[tuple[str, dict]]:
    t = Topics(sid)
    device = {"identifiers": [f"tagsense_access_{sid}"], "name": f"TagSense Access {name}",
              "manufacturer": "ha-tagsense", "model": "QR access scanner", "sw_version": version}
    origin = {"name": "TagSense", "sw_version": version, "support_url": SUPPORT_URL}

    def cfg(component: str, key: str, payload: dict) -> tuple[str, dict]:
        p = {"unique_id": f"tagsense_access_{sid}_{key}", "device": device, "origin": origin, "availability_topic": AVAILABILITY,
             "has_entity_name": True}
        p.update(payload)
        return f"{DISCOVERY_PREFIX}/{component}/{NODE}/{sid}_{key}/config", p

    return [
        cfg("event", "code", {"name": "Code", "state_topic": t.event,
                              "event_types": list(EVENT_TYPES), "icon": "mdi:qrcode-scan"}),
        cfg("button", "scan", {"name": "Scan", "command_topic": t.cmd("scan"),
                               "icon": "mdi:qrcode-scan"}),
        cfg("binary_sensor", "scanning", {"name": "Scanning", "state_topic": t.scanning,
                                          "icon": "mdi:cctv"}),
        cfg("image", "last_scan", {"name": "Last scan", "image_topic": t.image,
                                   "content_type": "image/jpeg"}),
    ]


class AccessMqtt:
    """Publishes scanner entities and events; routes Scan presses to `on_scan`."""

    def __init__(self, cfg: MqttConfig, version: str, on_scan: Callable[[str], None],
                 on_connected: Callable[[], None], client=None):
        self.version = version
        self._on_scan = on_scan
        self._on_connected = on_connected
        self.connected = False
        if client is not None:          # tests
            self.client = client
            return
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="tagsense-access")
        c.username_pw_set(cfg.username, cfg.password)
        c.will_set(AVAILABILITY, OFFLINE, qos=1, retain=True)
        c.reconnect_delay_set(1, 60)
        c.on_connect = self._handle_connect
        c.on_message = self._handle_message
        c.on_disconnect = self._handle_disconnect
        self.client = c
        self.cfg = cfg

    def start(self):
        log.info("access: connecting to MQTT %s:%s as %s", self.cfg.host, self.cfg.port,
                 self.cfg.username)
        self.client.connect_async(self.cfg.host, self.cfg.port, keepalive=60)
        self.client.loop_start()

    def stop(self):
        try:
            self.client.publish(AVAILABILITY, OFFLINE, qos=1, retain=True).wait_for_publish(5)
        except (RuntimeError, ValueError):
            pass
        self.client.disconnect()
        self.client.loop_stop()

    def pub(self, topic: str, payload, retain: bool):
        self.client.publish(topic, payload, qos=1, retain=retain)

    # --- entities -------------------------------------------------------------

    def publish_discovery(self, sid: str, name: str):
        for topic, payload in discovery_configs(self.version, sid, name):
            self.pub(topic, json.dumps(payload), retain=True)
        self.pub(Topics(sid).scanning, "OFF", retain=True)

    def remove(self, sid: str, name: str):
        t = Topics(sid)
        for topic, _ in discovery_configs(self.version, sid, name):
            self.pub(topic, b"", retain=True)
        for topic in (t.scanning, t.cmd("scan")):
            self.pub(topic, b"", retain=True)

    def publish_event(self, sid: str, event_type: str, attrs: dict):
        assert event_type in EVENT_TYPES, event_type
        self.pub(Topics(sid).event, json.dumps({"event_type": event_type, **attrs}), retain=False)

    def publish_scanning(self, sid: str, on: bool):
        self.pub(Topics(sid).scanning, "ON" if on else "OFF", retain=True)

    def publish_image(self, sid: str, jpeg: bytes):
        self.pub(Topics(sid).image, jpeg, retain=False)

    # --- callbacks (paho thread) ----------------------------------------------

    def _handle_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("access: MQTT connect failed: %s (check access_mqtt_username/"
                      "access_mqtt_password and the broker's login list)", reason_code)
            return
        log.info("access: MQTT connected")
        self.connected = True
        self._on_connected()
        client.subscribe([(f"{BASE}/+/cmd/+", 1), (HA_STATUS, 1)])
        self.pub(AVAILABILITY, ONLINE, retain=True)

    def _handle_disconnect(self, *a):
        self.connected = False
        log.warning("access: MQTT disconnected")

    def _handle_message(self, client, userdata, msg):
        if msg.topic == HA_STATUS:
            if msg.payload.decode("utf-8", "replace").strip() == ONLINE:
                self._on_connected()
            return
        if msg.retain:              # a stale retained press must never start a scan
            return
        parts = msg.topic.split("/")
        if len(parts) == 5 and parts[3] == "cmd" and parts[4] in COMMANDS:
            self._on_scan(parts[2])
