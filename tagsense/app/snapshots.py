"""Annotated frames saved when an object's reported state changes (last KEEP)."""
from __future__ import annotations

import json
import os

KEEP = 20


class Snapshots:
    def __init__(self, directory: str, keep: int = KEEP):
        self.dir = directory
        self.keep = keep
        self.index_path = os.path.join(directory, "index.json")
        self._index = self._load()

    def _load(self) -> list[dict]:
        try:
            with open(self.index_path) as f:
                index = json.load(f)
            return [e for e in index if isinstance(e, dict) and "name" in e]
        except (OSError, ValueError):
            return []

    def add(self, at: str, ts: float, from_state: str, to_state: str, reason: str,
            jpeg: bytes | None) -> dict:
        os.makedirs(self.dir, exist_ok=True)
        name = str(int(ts * 1000))
        while any(e["name"] == name for e in self._index):
            name = str(int(name) + 1)
        if jpeg:
            with open(os.path.join(self.dir, f"{name}.jpg"), "wb") as f:
                f.write(jpeg)
        entry = {"name": name, "at": at, "from": from_state, "to": to_state, "reason": reason,
                 "has_image": bool(jpeg)}
        self._index.insert(0, entry)
        for old in self._index[self.keep:]:
            try:
                os.remove(os.path.join(self.dir, f"{old['name']}.jpg"))
            except OSError:
                pass
        del self._index[self.keep:]
        tmp = f"{self.index_path}.tmp"
        with open(tmp, "w") as f:
            json.dump(self._index, f)
        os.replace(tmp, self.index_path)
        return entry

    def list(self) -> list[dict]:
        return list(self._index)

    def read(self, name: str) -> bytes | None:
        """The image for an indexed snapshot; None for unknown names."""
        if not any(e["name"] == name and e.get("has_image") for e in self._index):
            return None
        try:
            with open(os.path.join(self.dir, f"{name}.jpg"), "rb") as f:
                return f.read()
        except OSError:
            return None
