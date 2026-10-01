"""Web UI served through Home Assistant ingress (sidebar panel).

A small JSON API plus one static page. HA handles login; requests are only
accepted from the Supervisor's ingress proxy.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .objects import ConfigError, ObjectConfig, parse_one, slugify, unique_id
from .sources import FetchError, list_go2rtc_streams, list_ha_cameras
from .tagprint import tag_png

log = logging.getLogger("tagsense.web")

INGRESS_IPS = {"172.30.32.2"}
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
MAX_BODY = 64 * 1024
CONFIG_FIELDS = ("name", "tag_id", "source", "go2rtc_stream", "camera_entity")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Api:
    """What the UI can do. Thin wrapper over App; separated for testing."""

    def __init__(self, app):
        self.app = app

    def _obj(self, oid: str):
        o = self.app.objects.get(oid)
        if not o:
            raise ApiError(404, f"no object {oid!r}")
        return o

    def state(self) -> dict:
        return {"version": os.environ.get("TAGSENSE_VERSION", "dev"),
                "go2rtc_configured": bool(self.app.opts.get("go2rtc_url")),
                "objects": [o.status() for o in self.app.objects.values()]}

    def sources(self) -> dict:
        out = {"go2rtc": [], "ha_cameras": [], "errors": {}}
        try:
            out["go2rtc"] = list_go2rtc_streams(self.app.opts.get("go2rtc_url", ""))
        except Exception as e:      # noqa: BLE001 - shown to the user
            out["errors"]["go2rtc"] = str(e)
        try:
            out["ha_cameras"] = list_ha_cameras()
        except Exception as e:      # noqa: BLE001
            out["errors"]["ha_cameras"] = str(e)
        return out

    def create(self, body: dict) -> dict:
        raw = {k: body.get(k) for k in CONFIG_FIELDS}
        taken = {c.id for c in self.app.configs}
        raw["id"] = (str(body.get("id") or "").strip().lower()
                     or unique_id(slugify(str(body.get("name") or "")), taken))
        oc = self._parse(raw)
        if oc.id in taken:
            raise ApiError(400, f"id {oc.id!r} is already used")
        self._apply(self.app.configs + [oc])
        return {"id": oc.id}

    def update(self, oid: str, body: dict) -> dict:
        current = next((c for c in self.app.configs if c.id == oid), None)
        if not current:
            raise ApiError(404, f"no object {oid!r}")
        raw = current.to_dict() | {k: body[k] for k in CONFIG_FIELDS if k in body}
        raw["id"] = oid                                     # ids never change
        oc = self._parse(raw)
        if oc != current:
            self._apply([oc if c.id == oid else c for c in self.app.configs])
        return {"id": oid}

    def delete(self, oid: str) -> dict:
        current = next((c for c in self.app.configs if c.id == oid), None)
        if not current:
            raise ApiError(404, f"no object {oid!r}")
        self._apply([c for c in self.app.configs if c.id != oid], removed=[current])
        return {"deleted": oid}

    def settings(self, oid: str, body: dict) -> dict:
        o = self._obj(oid)
        for key, value in body.items():
            if key not in o.settings_values():
                raise ApiError(400, f"unknown setting {key!r}")
            if isinstance(value, bool):
                value = "ON" if value else "OFF"
            o.on_setting(key, str(value))
        return o.settings_values()

    def check(self, oid: str) -> dict:
        o = self._obj(oid)
        if not o.settings.enabled:
            raise ApiError(409, "object is disabled")
        o.on_check_now()
        return {"requested": oid}

    def history(self, oid: str) -> list:
        return self._obj(oid).history_list()

    def frame(self, oid: str, max_age_s: float) -> bytes:
        try:
            return self._obj(oid).camera.grab(max_age_s).data
        except FetchError as e:
            raise ApiError(502, str(e)) from None

    def image(self, oid: str, kind: str) -> bytes:
        o = self._obj(oid)
        data = o.last_phantom_jpeg if kind == "phantom" else o.last_jpeg
        if not data:
            raise ApiError(404, "no image yet")
        return data

    def _parse(self, raw: dict) -> ObjectConfig:
        try:
            return parse_one(raw)
        except ConfigError as e:
            raise ApiError(400, str(e)) from None

    def _apply(self, configs, removed=()):
        try:
            self.app.apply_configs(configs, removed)
        except ConfigError as e:
            raise ApiError(400, str(e)) from None


ROUTES = [
    ("GET", r"/api/state", "state"),
    ("GET", r"/api/sources", "sources"),
    ("POST", r"/api/objects", "create"),
    ("PUT", r"/api/objects/(?P<oid>[a-z0-9_]+)", "update"),
    ("DELETE", r"/api/objects/(?P<oid>[a-z0-9_]+)", "delete"),
    ("PATCH", r"/api/objects/(?P<oid>[a-z0-9_]+)/settings", "settings"),
    ("POST", r"/api/objects/(?P<oid>[a-z0-9_]+)/check", "check"),
    ("GET", r"/api/objects/(?P<oid>[a-z0-9_]+)/history", "history"),
    ("GET", r"/api/objects/(?P<oid>[a-z0-9_]+)/frame\.jpg", "frame"),
    ("GET", r"/api/objects/(?P<oid>[a-z0-9_]+)/(?P<kind>last|phantom)\.jpg", "image"),
    ("GET", r"/api/tag/(?P<tag>\d+)\.png", "tag"),
]


def make_handler(api: Api, allow_all: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TagSense"

        def log_message(self, fmt, *args):
            log.debug("%s " + fmt, self.client_address[0], *args)

        def _send(self, status: int, body: bytes, ctype: str, cache: bool = False):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj):
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ApiError(413, "request too large")
            if not n:
                return {}
            try:
                body = json.loads(self.rfile.read(n))
            except ValueError:
                raise ApiError(400, "invalid JSON") from None
            if not isinstance(body, dict):
                raise ApiError(400, "expected a JSON object")
            return body

        def _dispatch(self, method: str):
            if not allow_all and self.client_address[0] not in INGRESS_IPS:
                return self._send(403, b"forbidden", "text/plain")
            url = urlparse(self.path)
            path, query = url.path, parse_qs(url.query)
            if method == "GET" and path in ("/", "/index.html"):
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            for m, pattern, name in ROUTES:
                match = re.fullmatch(pattern, path)
                if m == method and match:
                    break
            else:
                return self._json(404, {"error": "not found"})
            kw = match.groupdict()
            try:
                if name == "tag":
                    cell = int((query.get("cell") or ["100"])[0])
                    return self._send(200, tag_png(int(kw["tag"]), cell), "image/png", cache=True)
                if name == "frame":
                    age = float((query.get("max_age") or ["0"])[0])
                    return self._send(200, api.frame(kw["oid"], age), "image/jpeg")
                if name == "image":
                    return self._send(200, api.image(kw["oid"], kw["kind"]), "image/jpeg")
                args = list(kw.values())
                if method in ("POST", "PUT", "PATCH") and name != "check":
                    args.append(self._body())
                return self._json(200, getattr(api, name)(*args))
            except ApiError as e:
                return self._json(e.status, {"error": str(e)})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            except Exception:       # noqa: BLE001
                log.exception("web request failed: %s %s", method, path)
                return self._json(500, {"error": "internal error, see the app log"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def start_web(app, port: int, allow_all: bool | None = None) -> ThreadingHTTPServer:
    if allow_all is None:
        allow_all = os.environ.get("TAGSENSE_WEB_ALLOW_ALL") == "1"
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(Api(app), allow_all))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="web", daemon=True).start()
    log.info("web UI listening on port %d%s", server.server_address[1],
             " (open to all clients)" if allow_all else "")
    return server
