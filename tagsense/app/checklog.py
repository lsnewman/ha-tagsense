"""The last 24 hours of checks for the panel's chart, kept across restarts.

One compact JSON line per check, appended. Old lines are dropped on load and
when the file has grown to twice the records still kept.
"""
from __future__ import annotations

import json
import os
import time

KEEP_S = 24 * 3600


class CheckLog:
    def __init__(self, path: str, keep_s: float = KEEP_S, clock=time.time):
        self.path = path
        self.keep_s = keep_s
        self.clock = clock
        self._records: list[dict] = []
        self._lines = 0          # lines in the file, including expired ones
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                lines = f.readlines()
        except OSError:
            return
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("t"), (int, float)):
                self._records.append(rec)
        self._lines = len(lines)
        self._prune()
        self._compact()

    def _prune(self):
        cutoff = self.clock() - self.keep_s
        i = 0
        while i < len(self._records) and self._records[i]["t"] < cutoff:
            i += 1
        del self._records[:i]

    def _compact(self):
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            f.writelines(json.dumps(r, separators=(",", ":")) + "\n" for r in self._records)
        os.replace(tmp, self.path)
        self._lines = len(self._records)

    def append(self, rec: dict):
        self._records.append(rec)
        with open(self.path, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self._lines += 1
        self._prune()
        if self._lines > 2 * max(len(self._records), 100):
            self._compact()

    def points(self) -> list[dict]:
        self._prune()
        return list(self._records)
