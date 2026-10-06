"""Web UI served through Home Assistant ingress (sidebar panel).

A small JSON API plus one static page. HA handles login; requests are only
accepted from the Supervisor's ingress proxy.
"""
from __future__ import annotations

import json
import logging
import os
import re
import hmac
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import globals as gl
from .mqtt_ha import SETTING_KEYS
from .objects import ConfigError, ObjectConfig, parse_one, slugify, unique_id
from .detector import DEFAULT_FAMILY, FAMILIES, family_info
from .sources import FetchError, list_go2rtc_streams, list_ha_cameras
from .tagprint import tag_png, tag_svg

log = logging.getLogger("tagsense.web")

INGRESS_IPS = {"172.30.32.2"}
# Home Assistant Core reaches apps from the hassio network's gateway. Only the
# confirm route accepts it, and only with the confirm token: apps on the host
# network share this address, so the address alone proves nothing.
CONFIRM_IPS = {"172.30.32.1"}
CONFIRM_RE = re.compile(r"/api/confirm/([A-Za-z0-9_-]{16,64})")
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
MAX_BODY = 8 * 1024 * 1024        # an import with a day of chart history per object
CONFIG_FIELDS = ("name", "tag_family", "tag_id", "source", "go2rtc_stream", "camera_entity",
                 "fallback")


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
        t = self.app.tuning
        return {"version": os.environ.get("TAGSENSE_VERSION", "dev"),
                "go2rtc_configured": bool(self.app.opts.get("go2rtc_url")),
                "max_aspect": t.max_aspect, "min_size_ratio": t.min_size_ratio,
                "families": {f: {"ids": family_info(f).id_count, "cells": family_info(f).cells}
                             for f in FAMILIES},
                "default_family": DEFAULT_FAMILY,
                "objects": [o.status() for o in self.app.objects.values()],
                "access": {"enabled": self.app.access is not None,
                           "error": self.app.access_error,
                           # names only, for the panel's navigation
                           "scanners": [{"id": c.id, "name": c.name} for c in self.app.access.configs]
                           if self.app.access else []}}

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
            o.on_setting(key, str(value), from_api=True)
        return o.settings_values()

    def check(self, oid: str) -> dict:
        o = self._obj(oid)
        if not o.settings.enabled:
            raise ApiError(409, "object is disabled")
        o.on_check_now()
        return {"requested": oid}

    def reset_reference(self, oid: str) -> dict:
        self._obj(oid).reset_reference()
        return {"reset": oid}

    def set_orientation(self, oid: str) -> dict:
        if not self._obj(oid).set_orientation():
            raise ApiError(409, "the object is not present: nothing to take 0° from")
        return {"set_orientation": oid}

    def history(self, oid: str) -> list:
        return self._obj(oid).history_list()

    def chart(self, oid: str) -> list:
        return self._obj(oid).chart_points()

    def snapshots(self, oid: str) -> list:
        return self._obj(oid).snapshots.list()

    def snapshot(self, oid: str, name: str) -> bytes:
        data = self._obj(oid).snapshots.read(name)
        if not data:
            raise ApiError(404, "no such snapshot")
        return data

    # --- global settings (the panel's Settings page) ----------------------

    def globals_get(self) -> dict:
        o = self.app.opts
        return {"values": dict(self.app.globals), "defaults": gl.DEFAULTS,
                "ranges": {k: [lo, hi] for k, (_, lo, hi, _) in gl.SPEC.items()},
                "log_levels": list(gl.LOG_LEVELS),
                # read-only, and never a password
                "app_options": {"go2rtc_url": o.get("go2rtc_url") or "",
                                "mqtt": f"override: {o['mqtt_host']}:{o.get('mqtt_port') or 1883}"
                                if o.get("mqtt_host") else "the Mosquitto broker app (automatic)",
                                "mqtt_connected": bool(self.app.connected),
                                "access_enabled": bool(o.get("access_enabled")),
                                "access_mqtt_username": o.get("access_mqtt_username") or ""}}

    def globals_set(self, body: dict) -> dict:
        try:
            self.app.set_globals(body)
        except ConfigError as e:
            raise ApiError(400, str(e)) from None
        return self.globals_get()

    # --- export / import --------------------------------------------------

    def export(self) -> dict:
        out = []
        for c in self.app.configs:
            entry = c.to_dict()
            if o := self.app.objects.get(c.id):
                with o.cond:
                    entry["settings"] = o.settings_values()
                entry["history"] = o.export_history()      # chart log + learned position
            out.append(entry)
        result = {"tagsense": 1, "objects": out, "globals": dict(self.app.globals)}
        if self.app.access is not None:      # scanner settings only: never keys, passes or codes
            result["access_scanners"] = [c.to_dict() for c in self.app.access.configs]
        return result

    def import_(self, body: dict) -> dict:
        """Add or update objects by id; objects not in the import are kept."""
        data = body
        if "text" in body:
            try:
                data = json.loads(body["text"])
            except ValueError as e:
                raise ApiError(400, f"not valid JSON: {e}") from None
        is_dict = isinstance(data, dict)
        entries = data.get("objects", []) if is_dict else data
        scanners = data.get("access_scanners") if is_dict else None
        if not isinstance(entries, list) or not (entries or scanners or (is_dict and data.get("globals"))):
            raise ApiError(400, "expected a list of objects (as exported)")
        configs = list(self.app.configs)
        by_id = {c.id: i for i, c in enumerate(configs)}
        added, updated, settings, histories = [], [], {}, {}
        for n, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ApiError(400, f"objects[{n}]: expected an object")
            raw = {k: entry.get(k) for k in ("id", *CONFIG_FIELDS)}
            try:
                oc = parse_one(raw, f"objects[{n}]")
            except ConfigError as e:
                raise ApiError(400, str(e)) from None
            if oc.id in by_id:
                if configs[by_id[oc.id]] != oc:
                    configs[by_id[oc.id]] = oc
                updated.append(oc.id)
            else:
                by_id[oc.id] = len(configs)
                configs.append(oc)
                added.append(oc.id)
            if isinstance(entry.get("settings"), dict):
                settings[oc.id] = entry["settings"]
            if isinstance(entry.get("history"), dict):
                histories[oc.id] = entry["history"]
        for oid, values in settings.items():      # check before changing anything
            if unknown := set(values) - set(SETTING_KEYS):
                raise ApiError(400, f"{oid}: unknown setting(s) {sorted(unknown)}")
        new_globals = data.get("globals") if is_dict else None
        if new_globals is not None:
            if not isinstance(new_globals, dict):
                raise ApiError(400, "globals: expected an object")
            try:
                gl.validate({**self.app.globals, **new_globals})
            except ConfigError as e:
                raise ApiError(400, f"globals: {e}") from None
        if configs != self.app.configs:
            self._apply(configs)
        for oid, values in settings.items():
            self.settings(oid, values)
        globals_changed = False
        if new_globals is not None:
            before = dict(self.app.globals)
            globals_changed = self.app.set_globals(new_globals) != before
        history = {}
        for oid, h in histories.items():
            if o := self.app.objects.get(oid):
                history[oid] = o.import_history(h)
        log.info("imported objects: added %s, updated %s", added or "none", updated or "none")
        return {"added": added, "updated": updated, "history": history, "globals_changed": globals_changed,
                "scanners": self._import_scanners(scanners)}

    def _import_scanners(self, raw) -> dict:
        """Add or update access scanners by id (settings only); others are kept."""
        if not isinstance(raw, list) or not raw:
            return {}
        if self.app.access is None:
            return {"skipped": "access is not enabled on this install (access_enabled)"}
        from .access.config import parse_scanner
        acc = self.app.access
        configs = list(acc.configs)
        by_id = {c.id: i for i, c in enumerate(configs)}
        added, updated = [], []
        for n, entry in enumerate(raw):
            try:
                sc = parse_scanner(entry, f"access_scanners[{n}]")
            except ConfigError as e:
                raise ApiError(400, str(e)) from None
            if sc.id in by_id:
                configs[by_id[sc.id]] = sc
                updated.append(sc.id)
            else:
                by_id[sc.id] = len(configs)
                configs.append(sc)
                added.append(sc.id)
        if configs != acc.configs:
            try:
                acc.apply(configs)
            except ConfigError as e:
                raise ApiError(400, str(e)) from None
        log.info("imported scanners: added %s, updated %s", added or "none", updated or "none")
        return {"added": added, "updated": updated}

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

    # --- who is using the panel ----------------------------------------------------

    def whoami(self, headers) -> dict:
        """Read-only check of the ingress user header and the admin lookup."""
        uid = headers.get("X-Remote-User-Id")
        u = self.app.users.get(uid) if uid else None
        return {"header_present": bool(uid),
                "name": headers.get("X-Remote-User-Display-Name") or headers.get("X-Remote-User-Name"),
                "found": u is not None, "admin": bool(u and u.is_active and u.is_admin),
                "lookup": self.app.users.source, "lookup_error": self.app.users.error,
                # The panel is admin-only in this build, so listing names is fine here.
                "users": [{"name": x.name, "admin": x.is_admin, "active": x.is_active}
                          for x in self.app.users.users()] if u and u.is_admin else []}

    # --- "My pass": the only data a non-admin can reach -----------------------------

    def _my_person(self, headers):
        uid = headers.get("X-Remote-User-Id")
        if not uid:
            raise ApiError(403, "not signed in")
        if self.app.access is None:
            return None
        return next((p for p in self.app.access.codes.people()
                     if p.ha_user_id == uid and p.enabled), None)

    def pass_info(self, headers) -> dict:
        p = self._my_person(headers)
        if self.app.access is None:
            return {"available": False, "has_pass": False}
        if p is None:
            return {"available": True, "has_pass": False}
        period = self.app.access.verifier.period
        _, left = self._issue().current_pass(p, period)
        return {"available": True, "has_pass": True, "label": p.label, "period": period,
                "seconds_left": round(left, 1)}

    def pass_png(self, headers, invert: bool = False) -> bytes:
        p = self._my_person(headers)
        if p is None:
            raise ApiError(404, "no pass")
        from .access import codes
        payload, _ = self._issue().current_pass(p, self.app.access.verifier.period)
        return codes.qr_png(payload, module_px=14, invert=invert)

    # --- access (404 unless access_enabled and started) -------------------------

    def _access(self):
        if self.app.access is None:
            raise ApiError(404, "not found")
        return self.app.access

    def _scanner(self, sid: str):
        s = self._access().scanners.get(sid)
        if not s:
            raise ApiError(404, f"no scanner {sid!r}")
        return s

    def access_status(self) -> dict:
        return self._access().status()

    def access_create(self, body: dict) -> dict:
        try:
            return {"id": self._access().create(body)}
        except ConfigError as e:
            raise ApiError(400, str(e)) from None

    def access_update(self, sid: str, body: dict) -> dict:
        self._scanner(sid)
        try:
            return {"id": self._access().update(sid, body)}
        except ConfigError as e:
            raise ApiError(400, str(e)) from None

    def access_delete(self, sid: str) -> dict:
        self._scanner(sid)
        self._access().delete(sid)
        return {"deleted": sid}

    def access_scan(self, sid: str) -> dict:
        self._scanner(sid)
        self._access().scan(sid, "panel")
        return {"scanning": sid}

    def access_image(self, sid: str) -> bytes:
        data = self._scanner(sid).last_jpeg
        if not data:
            raise ApiError(404, "no image yet")
        return data

    def access_frame(self, sid: str) -> bytes:
        try:
            return self._scanner(sid).grab()
        except FetchError as e:
            raise ApiError(502, str(e)) from None

    # --- access codes (admin) -------------------------------------------------------

    def _issue(self):
        self._access()                   # 404 first: never import access code when it is off
        from .access import issue
        return issue

    def _codes_call(self, fn, *a, **k):
        issue = self._issue()
        try:
            return fn(*a, **k)
        except issue.IssueError as e:
            raise ApiError(400, str(e)) from None

    def access_codes(self) -> dict:
        acc, issue = self._access(), self._issue()
        users = {u.id: u.name for u in self.app.users.users()}
        if not users:
            self.app.users.get("-")           # refresh the cached list
            users = {u.id: u.name for u in self.app.users.users()}
        now = time.time()
        v, _ = acc.codes.static_key()
        return {
            "people": [p.public() | {"ha_user_name": users.get(p.ha_user_id)}
                       for p in acc.codes.people()],
            "static": [{"code_id": r.code_id, "label": r.label, "status": issue.static_status(r, now),
                        "valid_from": r.valid_from, "expires": r.expires, "uses_total": r.uses_total,
                        "uses_left": r.uses_left, "created": r.created, "used": len(r.used_at),
                        "current_key": r.v == v}
                       for r in sorted(acc.codes.static_codes(), key=lambda r: -r.created)],
            "users": [{"id": i, "name": n} for i, n in sorted(users.items(), key=lambda x: x[1].lower())],
            "users_error": self.app.users.error,
            "key_version": v, "period": acc.verifier.period,
            "backstop_days": issue.BACKSTOP_DAYS, "max_days": issue.MAX_DAYS}

    def access_person_add(self, body: dict) -> dict:
        p = self._codes_call(self._issue().add_person, self._access().codes,
                             str(body.get("label") or ""), str(body.get("ha_user_id") or ""))
        log.info("access: pass added for %r", p.label)
        return {"handle": p.handle}

    def access_person_update(self, handle: str, body: dict) -> dict:
        self._access()
        if "enabled" in body:
            self._codes_call(self._issue().set_enabled, self._access().codes, handle,
                             bool(body["enabled"]))
        return {"handle": handle}

    def access_person_reenrol(self, handle: str) -> dict:
        self._codes_call(self._issue().re_enrol, self._access().codes, handle)
        log.info("access: pass %s re-enrolled (earlier codes no longer work)", handle)
        return {"handle": handle}

    def access_person_delete(self, handle: str) -> dict:
        self._codes_call(self._issue().remove_person, self._access().codes, handle)
        return {"deleted": handle}

    def access_static_issue(self, body: dict) -> dict:
        def num(k, cast=float):
            v = body.get(k)
            if v in (None, ""):
                return None
            try:
                return cast(v)
            except (TypeError, ValueError):
                raise ApiError(400, f"{k} must be a number") from None
        rec, _ = self._codes_call(
            self._issue().issue_static, self._access().codes, str(body.get("label") or ""),
            expires=num("expires"), uses=num("uses", int), valid_from=num("valid_from"),
            backstop_days=num("backstop_days", int) or self._issue().BACKSTOP_DAYS)
        log.info("access: static code %s issued for %r", rec.code_id, rec.label)
        return {"code_id": rec.code_id}

    def access_static_png(self, code_id: str) -> bytes:
        acc, issue = self._access(), self._issue()
        from .access import codes
        rec = acc.codes.static_record(code_id)
        if rec is None:
            raise ApiError(404, f"no code {code_id!r}")
        if issue.static_status(rec) not in ("active", "not yet valid"):
            raise ApiError(410, f"this code is {issue.static_status(rec)}")
        return codes.qr_png(self._codes_call(issue.static_payload, acc.codes, rec))

    def access_static_revoke(self, code_id: str) -> dict:
        self._codes_call(self._issue().revoke_static, self._access().codes, code_id)
        log.info("access: static code %s revoked", code_id)
        return {"revoked": code_id}

    def access_static_revoke_all(self) -> dict:
        n = self._issue().revoke_all_static(self._access().codes)
        log.info("access: all static codes revoked (%d)", n)
        return {"revoked": n}

    def access_rotate_key(self) -> dict:
        v = self._access().codes.rotate_static_key()
        log.info("access: static signing key rotated; every earlier static code is invalid")
        return {"key_version": v}

    def access_confirm_setup(self) -> dict:
        import socket
        acc = self._access()
        return {"hostname": socket.gethostname(), "port": 8099,
                "token": acc.codes.confirm_token(), "ttl_s": 30}

    def access_confirm_rotate(self) -> dict:
        self._access().codes.rotate_confirm_token()
        log.info("access: confirm token rotated (update Home Assistant's secrets.yaml)")
        return self.access_confirm_setup()

    def access_clear_lockout(self, sid: str) -> dict:
        self._scanner(sid)
        self._access().verifier.clear_lockout(sid)
        log.info("access [%s]: lockout cleared from the panel", sid)
        return {"cleared": sid}

    def access_debug(self, sid: str) -> bytes:
        self._scanner(sid)
        return self._access().debug_zip(sid)

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
    ("POST", r"/api/objects/(?P<oid>[a-z0-9_]+)/reset_reference", "reset_reference"),
    ("POST", r"/api/objects/(?P<oid>[a-z0-9_]+)/set_orientation", "set_orientation"),
    ("GET", r"/api/objects/(?P<oid>[a-z0-9_]+)/history", "history"),
    ("GET", r"/api/objects/(?P<oid>[a-z0-9_]+)/chart", "chart"),
    ("GET", r"/api/objects/(?P<oid>[a-z0-9_]+)/snapshots", "snapshots"),
    ("GET", r"/api/objects/(?P<oid>[a-z0-9_]+)/snapshots/(?P<name>\d+)\.jpg", "snapshot"),
    ("GET", r"/api/globals", "globals_get"),
    ("PATCH", r"/api/globals", "globals_set"),
    ("GET", r"/api/export", "export"),
    ("POST", r"/api/import", "import_"),
    ("GET", r"/api/objects/(?P<oid>[a-z0-9_]+)/frame\.jpg", "frame"),
    ("GET", r"/api/objects/(?P<oid>[a-z0-9_]+)/(?P<kind>last|phantom)\.jpg", "image"),
    ("GET", r"/api/tag/(?P<tag>\d+)\.(?P<fmt>png|svg)", "tag"),
    ("GET", r"/api/whoami", "whoami"),
    ("GET", r"/api/pass", "pass_info"),
    ("GET", r"/api/pass\.png", "pass_png"),
    ("GET", r"/api/access", "access_status"),
    ("POST", r"/api/access/scanners", "access_create"),
    ("PUT", r"/api/access/scanners/(?P<sid>[a-z0-9_]+)", "access_update"),
    ("DELETE", r"/api/access/scanners/(?P<sid>[a-z0-9_]+)", "access_delete"),
    ("POST", r"/api/access/scanners/(?P<sid>[a-z0-9_]+)/scan", "access_scan"),
    ("GET", r"/api/access/scanners/(?P<sid>[a-z0-9_]+)/last\.jpg", "access_image"),
    ("GET", r"/api/access/scanners/(?P<sid>[a-z0-9_]+)/frame\.jpg", "access_frame"),
    ("GET", r"/api/access/scanners/(?P<sid>[a-z0-9_]+)/debug\.zip", "access_debug"),
    ("POST", r"/api/access/scanners/(?P<sid>[a-z0-9_]+)/clear_lockout", "access_clear_lockout"),
    ("GET", r"/api/access/codes", "access_codes"),
    ("POST", r"/api/access/people", "access_person_add"),
    ("PATCH", r"/api/access/people/(?P<handle>[A-Z2-7]{4})", "access_person_update"),
    ("DELETE", r"/api/access/people/(?P<handle>[A-Z2-7]{4})", "access_person_delete"),
    ("POST", r"/api/access/people/(?P<handle>[A-Z2-7]{4})/reenrol", "access_person_reenrol"),
    ("POST", r"/api/access/static", "access_static_issue"),
    ("GET", r"/api/access/static/(?P<code_id>[A-Z2-7]{8})\.png", "access_static_png"),
    ("POST", r"/api/access/static/(?P<code_id>[A-Z2-7]{8})/revoke", "access_static_revoke"),
    ("POST", r"/api/access/static/revoke_all", "access_static_revoke_all"),
    ("POST", r"/api/access/rotate_key", "access_rotate_key"),
    ("GET", r"/api/access/confirm_setup", "access_confirm_setup"),
    ("POST", r"/api/access/confirm_setup/rotate", "access_confirm_rotate"),
]
# The only routes a signed-in non-admin may use (the page itself, "/", has no data).
# Every other route is admin-only: a new route is admin-only unless added here.
NON_ADMIN_ROUTES = ("whoami", "pass_info", "pass_png")
NO_BODY = ("check", "reset_reference", "set_orientation", "access_scan", "access_clear_lockout",
           "access_person_reenrol", "access_static_revoke", "access_static_revoke_all",
           "access_rotate_key", "access_confirm_rotate")


def make_handler(api: Api, allow_all: bool, trust_admin: bool = False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TagSense"

        def log_message(self, fmt, *args):
            log.debug("%s " + fmt, self.client_address[0], *args)

        _disposition = None

        def _attachment(self, filename: str):
            self._disposition = f'attachment; filename="{filename}"'

        def _send(self, status: int, body: bytes, ctype: str, cache: bool = False):
            self.send_response(status)
            if self._disposition:
                self.send_header("Content-Disposition", self._disposition)
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

        def _confirm(self, event_id: str):
            """POST /api/confirm/<event_id> from Home Assistant (rest_command)."""
            acc = api.app.access
            if acc is None:
                return self._json(404, {"error": "not found"})
            auth = self.headers.get("Authorization") or ""
            token = acc.codes.confirm_token()
            if not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
                log.warning("access: confirm request with a wrong or missing token from %s",
                            self.client_address[0])
                return self._json(401, {"confirmed": False, "error": "wrong token"})
            info = acc.confirm.confirm(event_id)
            if info is None:
                log.warning("access: confirm refused: unknown, used or expired event id")
                return self._json(200, {"confirmed": False})
            log.info("access [%s]: verified event confirmed to Home Assistant (%s '%s')",
                     info.get("scanner"), info.get("code_type"), info.get("label"))
            return self._json(200, {"confirmed": True, **info})

        def _dispatch(self, method: str):
            url = urlparse(self.path)
            path, query = url.path, parse_qs(url.query)
            ip = self.client_address[0]
            m = CONFIRM_RE.fullmatch(path)
            if m and method == "POST" and (allow_all or ip in CONFIRM_IPS):
                try:
                    return self._confirm(m.group(1))
                except Exception:       # noqa: BLE001 - fail closed
                    log.exception("confirm request failed")
                    return self._json(500, {"confirmed": False})
            if not allow_all and ip not in INGRESS_IPS:
                return self._send(403, b"forbidden", "text/plain")
            if method == "GET" and path in ("/", "/index.html"):
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            for m, pattern, name in ROUTES:
                match = re.fullmatch(pattern, path)
                if m == method and match:
                    break
            else:
                return self._json(404, {"error": "not found"})
            # Admin guard, before any route runs. Fails closed: no header, a failed
            # user lookup or an unknown user all mean "not an admin".
            if name not in NON_ADMIN_ROUTES and not (
                    trust_admin or api.app.users.is_admin(self.headers.get("X-Remote-User-Id"))):
                return self._json(403, {"error": "TagSense settings are for Home Assistant admins"})
            kw = match.groupdict()
            try:
                if name == "tag":
                    q = lambda k, d: (query.get(k) or [d])[0]
                    tag, quiet = int(kw["tag"]), q("quiet", "1") != "0"
                    family = q("family", DEFAULT_FAMILY)
                    if kw["fmt"] == "svg":
                        size = float(q("size_mm", "100"))
                        body = tag_svg(tag, size, quiet, family)
                        fam = "" if family == DEFAULT_FAMILY else f"{family}-"
                        self._attachment(f"tagsense-{fam}tag{tag}-{size:g}mm{'' if quiet else '-noborder'}.svg")
                        return self._send(200, body, "image/svg+xml", cache=True)
                    body = tag_png(tag, int(q("cell", "100")), label=q("label", "1") != "0",
                                   quiet=quiet, family=family)
                    return self._send(200, body, "image/png", cache=True)
                if name == "frame":
                    age = float((query.get("max_age") or ["0"])[0])
                    return self._send(200, api.frame(kw["oid"], age), "image/jpeg")
                if name == "image":
                    return self._send(200, api.image(kw["oid"], kw["kind"]), "image/jpeg")
                if name in ("whoami", "pass_info"):
                    return self._json(200, getattr(api, name)(self.headers))
                if name == "pass_png":
                    inv = (query.get("invert") or ["0"])[0] == "1"
                    return self._send(200, api.pass_png(self.headers, inv), "image/png")
                if name == "access_static_png":
                    return self._send(200, api.access_static_png(kw["code_id"]), "image/png")
                if name in ("access_image", "access_frame"):
                    return self._send(200, getattr(api, name)(kw["sid"]), "image/jpeg")
                if name == "access_debug":
                    body = api.access_debug(kw["sid"])
                    self._attachment(f"tagsense-scan-frames-{kw['sid']}.zip")
                    return self._send(200, body, "application/zip")
                if name == "snapshot":
                    return self._send(200, api.snapshot(kw["oid"], kw["name"]), "image/jpeg", cache=True)
                args = list(kw.values())
                if method in ("POST", "PUT", "PATCH") and name not in NO_BODY:
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


def start_web(app, port: int, allow_all: bool | None = None,
              trust_admin: bool = False) -> ThreadingHTTPServer:
    """`trust_admin` skips the admin guard. Only tests and local demos pass it;
    there is deliberately no option or environment variable for it."""
    if allow_all is None:
        allow_all = os.environ.get("TAGSENSE_WEB_ALLOW_ALL") == "1"
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(Api(app), allow_all, trust_admin))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="web", daemon=True).start()
    log.info("web UI listening on port %d%s", server.server_address[1],
             " (open to all clients)" if allow_all else "")
    return server
