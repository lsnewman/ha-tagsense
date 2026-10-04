"""Home Assistant persistent notifications through the Supervisor's Core API proxy.

One notification per id: creating it again updates it in place. Failures are
logged, never raised: an alert must not break a check.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("tagsense")

CORE_API = "http://supervisor/core/api"


class Notifier:
    def notify(self, nid: str, title: str, message: str):
        self._call("create", {"notification_id": nid, "title": title, "message": message})

    def dismiss(self, nid: str):
        self._call("dismiss", {"notification_id": nid})

    def _call(self, service: str, data: dict):
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            log.debug("no SUPERVISOR_TOKEN: notification %s %s skipped", service, data["notification_id"])
            return
        try:
            r = requests.post(f"{CORE_API}/services/persistent_notification/{service}",
                              headers={"Authorization": f"Bearer {token}"}, json=data, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("could not %s HA notification %s: %s", service, data["notification_id"], e)


class NullNotifier(Notifier):
    """Records calls instead of sending them (tests)."""

    def __init__(self):
        self.calls: list[tuple] = []

    def _call(self, service: str, data: dict):
        self.calls.append((service, data))
