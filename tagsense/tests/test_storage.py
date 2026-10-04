"""Snapshots and the 24h check log."""
import json

from app.checklog import CheckLog
from app.snapshots import Snapshots


def test_snapshots_keep_last_and_reject_unknown_names(tmp_path):
    s = Snapshots(str(tmp_path / "snap"), keep=3)
    for i in range(5):
        s.add(f"at{i}", 1000.0 + i, "present", "absent", "no_tag", b"\xff\xd8%d" % i)
    s.add("at5", 1005.0, "absent", "unknown", "fetch_failed", None)
    names = [e["name"] for e in s.list()]
    assert names == ["1005000", "1004000", "1003000"]
    assert sorted(p.name for p in (tmp_path / "snap").glob("*.jpg")) == ["1003000.jpg", "1004000.jpg"]
    assert s.read("1004000") == b"\xff\xd84"
    assert s.read("1005000") is None and s.read("1000000") is None and s.read("../x") is None
    assert [e["name"] for e in Snapshots(str(tmp_path / "snap")).list()] == names


def test_checklog_prunes_and_survives_restart(tmp_path):
    now = [100000.0]
    path = str(tmp_path / "checks.jsonl")
    log = CheckLog(path, keep_s=3600, clock=lambda: now[0])
    for i in range(10):
        log.append({"t": now[0] - 4000 + i * 500, "hits": i})
    assert [p["hits"] for p in log.points()] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    now[0] += 1800
    log2 = CheckLog(path, keep_s=3600, clock=lambda: now[0])
    assert [p["hits"] for p in log2.points()] == [5, 6, 7, 8, 9]
    assert len((tmp_path / "checks.jsonl").read_text().splitlines()) == 5      # compacted


def test_checklog_compacts_when_grown(tmp_path):
    now = [0.0]
    path = tmp_path / "c.jsonl"
    log = CheckLog(str(path), keep_s=10, clock=lambda: now[0])
    for i in range(500):
        now[0] = float(i)
        log.append({"t": now[0]})
    lines = path.read_text().splitlines()
    assert len(lines) <= 200 and json.loads(lines[-1])["t"] == 499.0
    assert len(log.points()) == 11
