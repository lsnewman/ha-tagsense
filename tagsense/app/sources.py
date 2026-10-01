"""Frame fetchers: go2rtc (primary) and the HA camera proxy (fallback)."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

TIMEOUT_S = 10
SUPERVISOR_URL = "http://supervisor"


class FetchError(Exception):
    pass


@dataclass
class Frame:
    data: bytes
    source: str
    fetch_ms: float


class Source:
    name = "source"

    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()

    def _get(self, url: str, **kw) -> Frame:
        t0 = time.perf_counter()
        try:
            r = self._session.get(url, timeout=TIMEOUT_S, **kw)
            r.raise_for_status()
        except requests.RequestException as e:
            raise FetchError(f"{self.name}: {e}") from e
        if not r.content:
            raise FetchError(f"{self.name}: empty response")
        return Frame(r.content, self.name, (time.perf_counter() - t0) * 1000.0)

    def fetch(self) -> Frame:
        raise NotImplementedError


class Go2rtcSource(Source):
    name = "go2rtc"

    def __init__(self, base_url: str, stream: str, **kw):
        super().__init__(**kw)
        if not base_url:
            raise ValueError("go2rtc_url is not set")
        if not stream:
            raise ValueError("go2rtc_stream is not set")
        self.url = base_url.rstrip("/") + "/api/frame.jpeg"
        self.stream = stream

    def fetch(self) -> Frame:
        return self._get(self.url, params={"src": self.stream})


class HaCameraSource(Source):
    name = "ha_camera"

    def __init__(self, entity_id: str, token: str | None = None, **kw):
        super().__init__(**kw)
        if not entity_id:
            raise ValueError("camera_entity is not set")
        self.url = f"{SUPERVISOR_URL}/core/api/camera_proxy/{entity_id}"
        self.token = token or os.environ.get("SUPERVISOR_TOKEN", "")

    def fetch(self) -> Frame:
        return self._get(self.url, headers={"Authorization": f"Bearer {self.token}"})


class FallbackSource(Source):
    def __init__(self, primary: Source, fallback: Source):
        self.primary, self.fallback = primary, fallback
        self.name = f"{primary.name}+{fallback.name}"

    def fetch(self) -> Frame:
        try:
            return self.primary.fetch()
        except FetchError as e1:
            try:
                return self.fallback.fetch()
            except FetchError as e2:
                raise FetchError(f"{e1}; {e2}") from e2


def build_source(opts: dict) -> Source:
    def make(kind: str) -> Source:
        if kind == "go2rtc":
            return Go2rtcSource(opts.get("go2rtc_url", ""), opts.get("go2rtc_stream", ""))
        if kind == "ha_camera":
            return HaCameraSource(opts.get("camera_entity", ""))
        raise ValueError(f"unknown source {kind!r}")

    primary = make(opts.get("source", "go2rtc"))
    fb = opts.get("fallback_source", "none")
    if fb and fb != "none" and fb != opts.get("source"):
        return FallbackSource(primary, make(fb))
    return primary


# --- discovery helpers for the web UI ----------------------------------------

def list_go2rtc_streams(base_url: str, timeout: float = 5) -> list[str]:
    if not base_url:
        return []
    r = requests.get(base_url.rstrip("/") + "/api/streams", timeout=timeout)
    r.raise_for_status()
    return sorted(r.json().keys())


def list_ha_cameras(token: str | None = None, timeout: float = 5) -> list[dict]:
    token = token or os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return []
    r = requests.get(f"{SUPERVISOR_URL}/core/api/states",
                     headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    r.raise_for_status()
    return sorted(({"entity_id": s["entity_id"],
                    "name": s.get("attributes", {}).get("friendly_name", s["entity_id"])}
                   for s in r.json() if s["entity_id"].startswith("camera.")),
                  key=lambda c: c["entity_id"])
