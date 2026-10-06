"""Who is using the panel, and are they a Home Assistant admin?

The ingress proxy adds X-Remote-User-Id (and -Name, -Display-Name) to every
request. web.py only accepts requests from the ingress proxy's address, so the
header cannot be set by anyone else.

Admin status comes from Home Assistant's user list, read with the Supervisor
token the app already has (homeassistant_api: true): Core's WebSocket command
config/auth/list, through the Supervisor's proxy. A user is an admin if they
are the owner or in the system-admin group. Anything uncertain (no header, the
lookup failed, the user is not in the list or inactive) means "not an admin".
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("tagsense.users")

WS_URL = "ws://supervisor/core/websocket"
CACHE_S = 60.0
TIMEOUT_S = 10.0
ADMIN_GROUP = "system-admin"


@dataclass(frozen=True)
class HaUser:
    id: str
    name: str
    is_admin: bool
    is_active: bool


def ws_command(token: str, command: str, url: str = WS_URL, timeout: float = TIMEOUT_S):
    """One Core WebSocket command (through the Supervisor's proxy); returns its result."""
    import websocket                     # websocket-client; only needed here

    ws = websocket.create_connection(url, timeout=timeout)
    try:
        hello = json.loads(ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected greeting {hello.get('type')!r}")
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        auth = json.loads(ws.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError(f"authentication refused ({auth.get('type')})")
        ws.send(json.dumps({"id": 1, "type": command}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                break
        if not msg.get("success"):
            raise RuntimeError(f"{command} refused: {msg.get('error', {}).get('code')}")
        return msg.get("result")
    finally:
        ws.close()


def fetch_users_ws(token: str, url: str = WS_URL, timeout: float = TIMEOUT_S) -> list[HaUser]:
    """Home Assistant's user list over the Core WebSocket API."""
    return [HaUser(u["id"], u.get("name") or u.get("username") or "?",
                   bool(u.get("is_owner")) or ADMIN_GROUP in (u.get("group_ids") or []),
                   bool(u.get("is_active", True)))
            for u in ws_command(token, "config/auth/list", url, timeout) or []
            if not u.get("system_generated")]


class UserDirectory:
    """Caches the user list; never raises into a web request."""

    def __init__(self, fetch=None, token: str | None = None):
        self._token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self._fetch = fetch or self._fetch_ws
        self._lock = threading.Lock()
        self._users: dict[str, HaUser] = {}
        self._at = 0.0
        self.error: str | None = None
        self.source = "Home Assistant WebSocket (config/auth/list)"
        self._logged = False

    def _fetch_ws(self) -> list[HaUser]:
        if not self._token:
            raise RuntimeError("no Supervisor token")
        return fetch_users_ws(self._token)

    def _refresh(self):
        users = self._fetch()
        self._users = {u.id: u for u in users}
        self._at = time.monotonic()
        self.error = None
        if not self._logged:
            log.info("user lookup works: %d users from %s", len(users), self.source)
            self._logged = True

    def get(self, user_id: str | None) -> HaUser | None:
        if not user_id:
            return None
        with self._lock:
            if time.monotonic() - self._at > CACHE_S or user_id not in self._users:
                try:
                    self._refresh()
                except Exception as e:      # noqa: BLE001 - fail closed, report it
                    self.error = f"{type(e).__name__}: {e}"
                    log.warning("user lookup failed (%s); treating everyone as not an admin",
                                self.error)
                    self._users, self._at = {}, 0.0
            return self._users.get(user_id)

    def is_admin(self, user_id: str | None) -> bool:
        u = self.get(user_id)
        return bool(u and u.is_active and u.is_admin)

    def users(self) -> list[HaUser]:
        with self._lock:
            return sorted(self._users.values(), key=lambda u: u.name.lower())
