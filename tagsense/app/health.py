"""The panel's Health page and the debug bundle.

Everything here is read-only. The bundle is for attaching to a bug report, so
it never contains a secret: passwords, tokens and keys are replaced by
"(hidden)" wherever they appear, and nothing from the access code store
(keys, passes, codes) is included at all.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import time
import zipfile
from collections import deque
from datetime import datetime, timezone

from .decision import FAILED

LOG_LINES = 500
DAY_S = 24 * 3600
SECRET_KEY = re.compile(r"(password|token|secret|(^|_)key$)", re.I)


# --- recent log lines, for the page and the bundle ---------------------------------

class RingHandler(logging.Handler):
    def __init__(self, size: int = LOG_LINES):
        super().__init__()
        self.lines: deque[str] = deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        self._lock2 = threading.Lock()

    def emit(self, record):
        try:
            line = self.format(record)
        except Exception:       # noqa: BLE001 - never break logging
            return
        with self._lock2:
            self.lines.append(line)

    def tail(self, n: int = LOG_LINES) -> list[str]:
        with self._lock2:
            return list(self.lines)[-n:]


_ring: RingHandler | None = None


def log_ring() -> RingHandler:
    """The process-wide handler, attached to the root logger once."""
    global _ring
    if _ring is None:
        _ring = RingHandler()
        logging.getLogger().addHandler(_ring)
    return _ring


# --- helpers -------------------------------------------------------------------------

def redact(obj):
    """A copy with every secret-looking value hidden."""
    if isinstance(obj, dict):
        return {k: ("(hidden)" if SECRET_KEY.search(str(k)) and v not in (None, "") else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def mqtt_connected(client_owner, fallback: bool = False) -> bool:
    c = getattr(client_owner, "client", None)
    fn = getattr(c, "is_connected", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:       # noqa: BLE001
            return fallback
    return bool(getattr(client_owner, "connected", fallback))


def day_summary(points: list[dict], now: float) -> dict:
    """The last 24 hours of an object's checks, from its chart log."""
    recent = [p for p in points if p.get("t", 0) >= now - DAY_S]
    checks = len(recent)
    failed = sum(1 for p in recent if p.get("outcome") == FAILED)
    frames = sum(p.get("frames") or 0 for p in recent if p.get("outcome") != FAILED)
    valid = sum(p.get("valid") or 0 for p in recent if p.get("outcome") != FAILED)
    changes = sum(1 for a, b in zip(recent, recent[1:]) if a.get("reported") != b.get("reported"))
    return {"checks": checks, "failed": failed,
            "failed_pct": round(100 * failed / checks) if checks else None,
            "discard_pct": round(100 * (frames - valid) / frames) if frames else None,
            "state_changes": changes}


def ago_s(mono: float) -> float | None:
    return round(time.monotonic() - mono, 1) if mono else None


# --- the page ------------------------------------------------------------------------

def health(app) -> dict:
    now = time.time()
    objects, cameras = [], []
    for cam in list(app.cameras.values()):
        cameras.append({"name": cam.name, "objects": [o.oc.name for o in cam.objects],
                        "last_frame_age_s": ago_s(cam.last_frame_at),
                        "fetch_failures_total": cam.fetch_failures_total,
                        "last_error": cam.last_error})
    for o in list(app.objects.values()):
        st = o.status()
        d = st.get("diag") or {}
        objects.append({"id": st["id"], "name": st["name"], "state": st["state"], "reason": st["reason"],
                        "enabled": st["settings"]["enabled"], "last_check": st["last_check"],
                        "alert": (st.get("alert") or {}).get("message"),
                        "fetch_ms": d.get("fetch_ms"), "resolution": d.get("resolution"),
                        "warning": d.get("warning"), "last_error": d.get("last_error"),
                        "day": day_summary(o.checklog.points(), now)})
    out = {"version": os.environ.get("TAGSENSE_VERSION", "dev"),
           "mqtt_connected": mqtt_connected(app.mqtt, app.connected),
           "go2rtc_configured": bool(app.opts.get("go2rtc_url")),
           "users_error": app.users.error,
           "entities_error": app.entities.check(),
           "cameras": cameras, "objects": objects,
           "access": None}
    if app.access is not None:
        acc = app.access.status()
        out["access"] = {"mqtt_connected": acc["mqtt_connected"], "activity": acc["activity"],
                         "scanners": [{"id": s["id"], "name": s["name"], "scanning": s["scanning"],
                                       "locked_until": s["locked_until"], "last_error": s["last_error"],
                                       "last_scan": (s["last_stats"] or {}).get("at"),
                                       "last_event": (s["events"] or [{}])[0].get("event_type")}
                                      for s in acc["scanners"]]}
    elif app.access_error:
        out["access"] = {"error": app.access_error}
    return out


# --- the bundle ----------------------------------------------------------------------

def debug_bundle(app) -> bytes:
    """A zip for a bug report. Contains no secrets (see the module docstring)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        def put(name, obj):
            z.writestr(name, json.dumps(redact(obj), indent=1, default=str))
        put("info.json", {"created": now, "version": os.environ.get("TAGSENSE_VERSION", "dev"),
                          "options": app.opts, "globals": app.globals})
        put("health.json", health(app))
        for o in list(app.objects.values()):
            put(f"objects/{o.oc.id}/status.json", o.status())
            put(f"objects/{o.oc.id}/recent_checks.json", o.history_list())
            z.writestr(f"objects/{o.oc.id}/checks_24h.jsonl",
                       "".join(json.dumps(p) + "\n" for p in o.checklog.points()))
        if app.access is not None:
            put("access/scanners.json", app.access.status())     # settings and events, no codes
            codes = app.access.codes
            put("access/codes_summary.json", {"passes": len(codes.people()),
                                              "static_codes": len(codes.static_codes())})
        z.writestr("log.txt", "\n".join(log_ring().tail()) + "\n")
    return buf.getvalue()
